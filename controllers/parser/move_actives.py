import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response

from db_manager import get_session_maker
from models import DesignNumber, Storage, Consignment, User
from parser_storage import LOG_DIR
from progress_tasks import start_task
from sql_builders import models as models_sql

from .serial_none import validate_serial_none_rows


logger = logging.getLogger("parser")


# Excel dates in the project's other parsers are entered in Moscow time while
# the database (relocate.date/date_current) stores UTC — the same shift as in
# ptoir_parser.py (MSK_OFFSET). Here no date comes from the file, so the shift is
# applied once to "now" for the whole move batch.
MOVE_TZ_SHIFT = timedelta(hours=3)


async def resolve_user_by_fullname(db_session: AsyncSession, fullname: str) -> int | None:
    """Look up fdw_users by the full name built exactly as session['fullname'] in auth.py:

    ' '.join(filter(None, [lastname, firstname, middlename])) or username.
    """
    result = await db_session.execute(select(User.id, User.lastname, User.firstname, User.middlename, User.username))
    for uid, lastname, firstname, middlename, username in result.all():
        built = " ".join(filter(None, [lastname, firstname, middlename])) or username or ""
        if built.strip() == fullname:
            return uid
    return None


async def validate_move_rows(
    db_session: AsyncSession, rows: list[dict],
    storage_name: str, consignment_name: str, user_fullname: str, set_nocm: bool,
    progress: dict | None = None,
) -> tuple[list[dict], list[dict], int | None, int | None, int | None, int | None]:
    """Validate the Excel rows for 'Переместить активы' (the move_active equivalent).

    Assets are located through lsn/lcn by the same logic as the
    "set serial='none' lcn" button (validate_serial_none_rows):
    a model lcn ('M9.6.5') yields every train of that type and their asset
    lcns, among which the assets that actually exist are then looked up.
    Rows with no asset at their lcn simply do not appear in the result —
    not an error (the position may already have been removed from some
    trains).

    Storage, consignment and user are shared by the whole file (set once in
    the modal). set_nocm is the "Установить позицию ТМЦ = 'NOCM'" checkbox:
    when on, id_design_number for every moved asset is resolved through
    design_number.number == 'NOCM' and additionally written by the UPDATE.
    Returns (errors, valid_rows, id_storage, id_consignment, id_user, id_design_number).
    """
    errors: list[dict] = []
    valid_rows: list[dict] = []

    storage_name = storage_name.strip()
    consignment_name = consignment_name.strip()
    user_fullname = user_fullname.strip()

    id_storage: int | None = None
    if not storage_name:
        errors.append({"row": 0, "field": "Склад", "message": "Поле 'Склад' пустое"})
    else:
        id_storage = await db_session.scalar(select(Storage.id).where(Storage.name == storage_name))
        if id_storage is None:
            errors.append({"row": 0, "field": "Склад", "message": f"Склад не найден: '{storage_name}'"})

    id_consignment: int | None = None
    if not consignment_name:
        errors.append({"row": 0, "field": "Партия", "message": "Поле 'Партия' пустое"})
    else:
        id_consignment = await db_session.scalar(select(Consignment.id).where(Consignment.name == consignment_name))
        if id_consignment is None:
            errors.append({"row": 0, "field": "Партия", "message": f"Партия не найдена: '{consignment_name}'"})

    id_user: int | None = None
    if not user_fullname:
        errors.append({"row": 0, "field": "Пользователь", "message": "Поле 'Пользователь' пустое"})
    else:
        id_user = await resolve_user_by_fullname(db_session, user_fullname)
        if id_user is None:
            errors.append({"row": 0, "field": "Пользователь", "message": f"Пользователь не найден: '{user_fullname}'"})

    id_design_number: int | None = None
    if set_nocm:
        id_design_number = await db_session.scalar(select(DesignNumber.id).where(DesignNumber.number == "NOCM"))
        if id_design_number is None:
            errors.append({"row": 0, "field": "ТМЦ", "message": "Позиция ТМЦ 'NOCM' не найдена"})

    if errors:
        return errors, valid_rows, id_storage, id_consignment, id_user, id_design_number

    lcn_errors, lcn_rows = await validate_serial_none_rows(db_session, rows, progress=progress)
    if lcn_errors:
        return lcn_errors, valid_rows, id_storage, id_consignment, id_user, id_design_number

    merged_lcns = models_sql.merge_serial_none_lcns(lcn_rows)
    if not merged_lcns:
        errors.append({"row": 0, "field": "lcn", "message": "Не найдено ни одного lcn для перемещения"})
        return errors, valid_rows, id_storage, id_consignment, id_user, id_design_number

    stmt = text(
        "SELECT id, active_number, id_location FROM public.actives WHERE lcn::text IN :lcns"
    ).bindparams(bindparam("lcns", expanding=True))
    result = await db_session.execute(stmt, {"lcns": merged_lcns})
    for row_num, (id_active, active_number, id_location_old) in enumerate(result.all(), start=1):
        valid_rows.append({
            "row": row_num,
            "active_number": active_number,
            "id_active": id_active,
            "id_location_old": id_location_old,
        })

    return errors, valid_rows, id_storage, id_consignment, id_user, id_design_number


class MoveActivesController(Controller):
    path = "/parser"

    @post("/move-actives/generate-sql/start")
    async def move_actives_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start background generation of the asset move SQL file; returns a task_id to poll."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        reason = str(data.get("reason", "") or "")
        storage_name = str(data.get("storage_name", "") or "")
        consignment_name = str(data.get("consignment_name", "") or "")
        user_fullname = str(data.get("user_fullname", "") or "")
        set_nocm = str(data.get("set_nocm", "") or "").strip().lower() in ("1", "true", "on")

        return start_task(len(rows), lambda progress: self._run_move_actives_generate(
            progress, rows, reason, storage_name, consignment_name, user_fullname, set_nocm
        ))

    async def _run_move_actives_generate(
        self, progress: dict, rows: list[dict],
        reason: str, storage_name: str, consignment_name: str, user_fullname: str, set_nocm: bool,
    ) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows, id_storage, id_consignment, id_user, id_design_number = await validate_move_rows(
                    session, rows, storage_name, consignment_name, user_fullname, set_nocm, progress=progress,
                )
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для перемещения"}])
            return

        move_date = datetime.now() - MOVE_TZ_SHIFT
        sql_lines = models_sql.move_actives(
            valid_rows, id_storage, id_consignment, id_user, reason, move_date, id_design_number,
        )
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/move-actives/execute-sql/start")
    async def move_actives_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start the background atomic asset move; returns a task_id to poll."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        reason = str(data.get("reason", "") or "")
        storage_name = str(data.get("storage_name", "") or "")
        consignment_name = str(data.get("consignment_name", "") or "")
        user_fullname = str(data.get("user_fullname", "") or "")
        set_nocm = str(data.get("set_nocm", "") or "").strip().lower() in ("1", "true", "on")

        return start_task(len(rows), lambda progress: self._run_move_actives_execute(
            progress, rows, reason, storage_name, consignment_name, user_fullname, set_nocm
        ))

    async def _run_move_actives_execute(
        self, progress: dict, rows: list[dict],
        reason: str, storage_name: str, consignment_name: str, user_fullname: str, set_nocm: bool,
    ) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows, id_storage, id_consignment, id_user, id_design_number = await validate_move_rows(
                        session, rows, storage_name, consignment_name, user_fullname, set_nocm, progress=progress,
                    )
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для перемещения"}])
                    return

                progress.update(processed=0, total=1, phase="executing")
                move_date = datetime.now() - MOVE_TZ_SHIFT
                sql_body = "\n".join(
                    models_sql.move_actives(
                        valid_rows, id_storage, id_consignment, id_user, reason, move_date, id_design_number,
                    )
                )
                try:
                    # DO $$ ... $$ containing several statements cannot go through an
                    # ordinary execute() (asyncpg will not prepare several commands in one
                    # prepared statement) — the same trick as in create_actives and
                    # create_named_actives: a raw connection, which session.rollback()
                    # below also rolls back, since the session opened a transaction earlier
                    # (the validation queries).
                    conn = await session.connection()
                    raw_conn = await conn.get_raw_connection()
                    await raw_conn.driver_connection.execute(sql_body)
                    progress["processed"] = 1
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
                    return
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
            return

        now = datetime.now()
        log_lines = [
            f"=== Execute move-actives: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Storage={storage_name} (id={id_storage}), Consignment={consignment_name} (id={id_consignment}), "
            f"User={user_fullname} (id={id_user})",
            f"Rows processed: {len(valid_rows)}",
            "",
            sql_body,
            "",
        ]
        log_file = LOG_DIR / f"move_actives_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Moved %d actives to storage '%s', log: %s", len(valid_rows), storage_name, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Перемещено активов: {len(valid_rows)} (склад: {storage_name})")
