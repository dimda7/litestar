import json
import logging
from datetime import datetime

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response

from db_manager import get_session_maker
from models import Train
from parser_storage import LOG_DIR
from progress_tasks import start_task
from sql_builders import models as models_sql

from .common import parse_model_lcn


logger = logging.getLogger("parser")


async def validate_serial_none_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'set serial=none lcn'.

    In an lcn like 'M9.6.5', 9 is id_train_type and '6.5' is the rest of the
    path. For every train of that id_train_type its own id replaces 'M9',
    yielding the list of asset lcns ('lcn_trains') that need
    serial_number='none'.
    """
    errors: list[dict] = []
    valid_rows: list[dict] = []

    lcn_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("lsn", "lcn")),
        None,
    )
    if rows and lcn_column is None:
        errors.append({"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    train_ids_by_type: dict[int, list[int]] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        lcn_raw = str(row.get(lcn_column, "") or "").strip()
        if not lcn_raw:
            errors.append({"row": row_num, "field": "lcn", "message": "Пустой lcn"})
            continue

        parsed = parse_model_lcn(lcn_raw)
        if parsed is None:
            errors.append({"row": row_num, "field": "lcn",
                           "message": f"Не удалось распознать id_train_type в lcn '{lcn_raw}'"})
            continue
        id_train_type, rest = parsed

        if id_train_type not in train_ids_by_type:
            result = await db_session.execute(select(Train.id).where(Train.id_train_type == id_train_type))
            train_ids_by_type[id_train_type] = [r[0] for r in result.all()]
        train_ids = train_ids_by_type[id_train_type]

        if not train_ids:
            errors.append({"row": row_num, "field": "lcn",
                           "message": f"Поезда с id_train_type={id_train_type} не найдены"})
            continue

        lcn_trains = [f"{tid}.{rest}" if rest else str(tid) for tid in train_ids]

        valid_rows.append({
            "row": row_num,
            "lcn": lcn_raw,
            "id_train_type": id_train_type,
            "lcn_trains": lcn_trains,
        })

    return errors, valid_rows


class SerialNoneController(Controller):
    path = "/parser"

    @post("/serial-none/generate-sql/start")
    async def serial_none_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start background generation of the 'set serial=none lcn' SQL file; returns a task_id to poll."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_serial_none_generate(progress, rows))

    async def _run_serial_none_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_serial_none_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = models_sql.set_serial_none(valid_rows)
        progress.update(status="done", sql="\n".join(sql_lines), count=len(valid_rows))

    @post("/serial-none/execute-sql/start")
    async def serial_none_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start the background atomic update of serial_number='none'; returns a task_id to poll."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_serial_none_execute(progress, rows))

    async def _run_serial_none_execute(self, progress: dict, rows: list[dict]) -> None:
        total_updated = 0
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await validate_serial_none_rows(session, rows, progress=progress)
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}])
                    return

                # One shared UPDATE over the merged, duplicate-free list of lcns from
                # every file row — instead of a query per row, which with identical or
                # overlapping lsns produced several identical UPDATEs in a row.
                progress.update(processed=0, total=1, phase="executing")
                try:
                    lcns = models_sql.merge_serial_none_lcns(valid_rows)
                    stmt = text(
                        "UPDATE public.actives SET serial_number = 'none' WHERE lcn::text IN :lcns"
                    ).bindparams(bindparam("lcns", expanding=True))
                    result = await session.execute(stmt, {"lcns": lcns})
                    total_updated = result.rowcount
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
            f"=== Execute serial-none update: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows processed: {len(valid_rows)}, actives updated: {total_updated}",
            "",
            *models_sql.set_serial_none(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"serial_none_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Serial-none updated for %d rows (%d actives), log: %s", len(valid_rows), total_updated, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Обновлено активов: {total_updated} (строк файла: {len(valid_rows)})")
