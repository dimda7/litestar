import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.response import Response

from db_manager import get_session_maker
from models import Actives
import excel_upload
from parser_storage import LOG_DIR
from progress_tasks import error_response, start_task
from sql_builders import actives as actives_sql

from .common import PREFIX


logger = logging.getLogger("actives_parser")

LOOKUP_CHUNK = 500


async def validate_serial_number(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[tuple[str, str, str | None]]]:
    """Validate rows for the actives.serial_number + actives.modification update.

    An empty 'Модификация' writes NULL, so the column itself is required — a
    file without it would otherwise wipe the modification of every asset.
    Returns (errors, valid_rows) where valid_rows is [(active_number, serial_number, modification), ...]
    """
    errors: list[dict] = []
    valid_rows: list[tuple[str, str, str | None]] = []
    batch_numbers: set[str] = set()

    serial_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("новый с/н", "новый серийный номер")),
        None,
    )
    modification_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() == "модификация"),
        None,
    )
    if rows and serial_column is None:
        errors.append({
            "row": 0,
            "field": "Новый с/н",
            "message": "В файле не найдена колонка 'Новый с/н' (или 'Новый серийный номер')",
        })
        return errors, valid_rows
    if rows and modification_column is None:
        errors.append({"row": 0, "field": "Модификация", "message": "В файле не найдена колонка 'Модификация'"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    # One SELECT per chunk instead of one per row: the files run to ~50k rows.
    numbers = list({str(row.get("Актив", "") or "").strip() for row in rows} - {""})
    existing: set[str] = set()
    for start in range(0, len(numbers), LOOKUP_CHUNK):
        result = await db_session.execute(
            select(Actives.active_number).where(Actives.active_number.in_(numbers[start:start + LOOKUP_CHUNK]))
        )
        existing.update(result.scalars().all())

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num
        active_number = str(row.get("Актив", "") or "").strip()
        serial_number = str(row.get(serial_column) or "").strip()
        modification = str(row.get(modification_column) or "").strip() or None

        if not active_number:
            errors.append({"row": row_num, "field": "Актив", "message": "Поле 'Актив' пустое"})
            continue

        if active_number in batch_numbers:
            errors.append({"row": row_num, "field": "Актив",
                            "message": f"Дубликат внутри файла: '{active_number}'"})
            continue

        if active_number not in existing:
            errors.append({"row": row_num, "field": "Актив",
                            "message": f"Актив не найден: '{active_number}'"})
            continue

        if not serial_number:
            errors.append({"row": row_num, "field": "Новый с/н", "message": "Поле 'Новый с/н' пустое"})
            continue

        batch_numbers.add(active_number)
        valid_rows.append((active_number, serial_number, modification))

    return errors, valid_rows


class SerialNumberController(Controller):
    path = "/actives-parser"

    @post("/serial-number/generate-sql/start")
    async def serial_number_generate_sql_start(self, request: Request) -> Response:
        """Start background generation of the serial_number update SQL file; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_serial_number_generate(progress, rows))

    async def _run_serial_number_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_serial_number(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}])
            return

        sql_lines = actives_sql.update_serial_number(valid_rows)
        progress.update(status="done", sql="\n".join(sql_lines), count=len(sql_lines))

    @post("/serial-number/execute/start")
    async def serial_number_execute_start(self, request: Request) -> Response:
        """Start the background serial_number update in the database; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_serial_number_execute(progress, rows))

    async def _run_serial_number_execute(self, progress: dict, rows: list[dict]) -> None:
        valid_rows: list[tuple[str, str, str | None]] = []
        updated = 0
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_serial_number(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}])
                    return

                progress.update(processed=0, total=1, phase="executing")

                try:
                    conn = await session.connection()
                    result = await conn.exec_driver_sql(actives_sql.update_serial_number(valid_rows)[0])
                    updated = result.rowcount
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
            f"=== Update serial_number + modification: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows processed: {len(valid_rows)}, actives updated: {updated}",
            "",
        ]
        log_lines.extend(actives_sql.update_serial_number(valid_rows))
        log_lines.append("")

        log_file = LOG_DIR / f"update_serial_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated serial_number for %d actives, log: %s", updated, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=(f"Обновлены serial_number и modification: {updated} активов "
                                 f"(строк файла: {len(valid_rows)}, остальные уже совпадали)"))
