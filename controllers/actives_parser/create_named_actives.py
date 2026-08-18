import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.response import Response

from db_manager import get_session_maker
from models import Actives, Consignment, DesignNumber, Storage
import excel_upload
from parser_storage import LOG_DIR
from progress_tasks import error_response, start_task
from sql_builders import actives as actives_sql

from .common import PREFIX, ActivesRepository, StorageRepository, ConsignmentRepository, DesignNumberRepository


logger = logging.getLogger("actives_parser")


async def validate_create_named_actives(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the rows creating named assets (the asset number comes from the file).

    Unlike creating assets from materials, no iterator_number_last counter is needed
    here: active_number is taken from the 'Актив' column and must not already exist in
    the database. Each row is one asset with its own location record; the lcn is still
    handed out from storage.last_lcn when the SQL runs.
    """
    actives_repo = ActivesRepository(session=db_session)
    storage_repo = StorageRepository(session=db_session)
    consignment_repo = ConsignmentRepository(session=db_session)
    design_number_repo = DesignNumberRepository(session=db_session)

    errors: list[dict] = []
    valid_rows: list[dict] = []
    batch_numbers: set[str] = set()

    required_columns = ("Актив", "ТМЦ", "Положение", "Партия")
    if rows:
        missing = [c for c in required_columns if c not in rows[0]]
        if missing:
            errors.append({
                "row": 0, "field": ", ".join(missing),
                "message": f"В файле не найдены колонки: {', '.join(missing)}",
            })
            return errors, valid_rows

    storage_cache: dict[str, int | None] = {}
    consignment_cache: dict[str, int | None] = {}
    design_number_cache: dict[str, int | None] = {}

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        active_number = str(row.get("Актив") or "").strip()
        if not active_number:
            errors.append({"row": row_num, "field": "Актив", "message": "Поле 'Актив' пустое"})
            continue

        if active_number in batch_numbers:
            errors.append({"row": row_num, "field": "Актив",
                            "message": f"Дубликат внутри файла: '{active_number}'"})
            continue

        existing = await actives_repo.get_one_or_none(Actives.active_number == active_number)
        if existing is not None:
            errors.append({"row": row_num, "field": "Актив",
                            "message": f"Актив уже существует: '{active_number}'"})
            continue

        design_number = str(row.get("ТМЦ") or "").strip()
        if not design_number:
            errors.append({"row": row_num, "field": "ТМЦ", "message": "Поле 'ТМЦ' пустое"})
            continue
        if design_number not in design_number_cache:
            dn = await design_number_repo.get_one_or_none(DesignNumber.number == design_number)
            design_number_cache[design_number] = dn.id if dn else None
        id_design_number = design_number_cache[design_number]
        if id_design_number is None:
            errors.append({"row": row_num, "field": "ТМЦ", "message": f"ТМЦ не найдена: '{design_number}'"})
            continue

        storage_name = str(row.get("Положение") or "").strip()
        if not storage_name:
            errors.append({"row": row_num, "field": "Положение", "message": "Поле 'Положение' пустое"})
            continue
        if storage_name not in storage_cache:
            storage = await storage_repo.get_one_or_none(Storage.name == storage_name)
            storage_cache[storage_name] = storage.id if storage else None
        id_storage = storage_cache[storage_name]
        if id_storage is None:
            errors.append({"row": row_num, "field": "Положение", "message": f"Склад не найден: '{storage_name}'"})
            continue

        consignment_name = str(row.get("Партия") or "").strip()
        if not consignment_name:
            errors.append({"row": row_num, "field": "Партия", "message": "Поле 'Партия' пустое"})
            continue
        if consignment_name not in consignment_cache:
            c = await consignment_repo.get_one_or_none(Consignment.name == consignment_name)
            consignment_cache[consignment_name] = c.id if c else None
        id_consignment = consignment_cache[consignment_name]
        if id_consignment is None:
            errors.append({"row": row_num, "field": "Партия", "message": f"Партия не найдена: '{consignment_name}'"})
            continue

        batch_numbers.add(active_number)
        valid_rows.append({
            "row_num": row_num,
            "active_number": active_number,
            "id_design_number": id_design_number,
            "id_storage": id_storage,
            "id_consignment": id_consignment,
        })

    return errors, valid_rows


class CreateNamedActivesController(Controller):
    path = "/actives-parser"

    @post("/create-named-actives/generate-sql/start")
    async def create_named_actives_generate_sql_start(self, request: Request) -> Response:
        """Start background generation of the named asset creation SQL file; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_create_named_actives_generate(progress, rows))

    async def _run_create_named_actives_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_create_named_actives(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
            return

        sql_lines = actives_sql.create_named_actives(valid_rows)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/create-named-actives/execute/start")
    async def create_named_actives_execute_start(self, request: Request) -> Response:
        """Start the background atomic creation of named assets; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_create_named_actives_execute(progress, rows))

    async def _run_create_named_actives_execute(self, progress: dict, rows: list[dict]) -> None:
        valid_rows: list[dict] = []
        sql_lines: list[str] = []
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_create_named_actives(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
                    return

                progress.update(processed=0, total=1, phase="executing")

                sql_lines = actives_sql.create_named_actives(valid_rows)
                sql_body = "\n".join(sql_lines)

                try:
                    # IMPORTANT: as in create-actives, session.rollback() below also rolls this
                    # raw call back only because the session opened a real transaction earlier
                    # (the repository queries inside validate_create_named_actives). Do not
                    # remove the session usage before this point.
                    conn = await session.connection()
                    raw_conn = await conn.get_raw_connection()
                    await raw_conn.driver_connection.execute(sql_body)

                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
                    return

                progress["processed"] = 1
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
            return

        now = datetime.now()
        log_lines = [
            f"=== Create Named Actives: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Actives created: {len(valid_rows)}",
            "",
            *sql_lines,
            "",
        ]
        log_file = LOG_DIR / f"create_named_actives_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Created %d named actives, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Успешно создано именных активов: {len(valid_rows)}")
