import logging
from datetime import datetime

from sqlalchemy import select, text
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


async def validate_serial_number(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """Validate rows for actives.serial_number update.
    Returns (errors, valid_rows) where valid_rows is [(active_number, serial_number), ...]
    """
    errors: list[dict] = []
    valid_rows: list[tuple[str, str]] = []
    batch_numbers: set[str] = set()

    if rows and "Новый с/н" not in rows[0] and "Новый Серийный номер" not in rows[0]:
        errors.append({
            "row": 0,
            "field": "Новый с/н",
            "message": "В файле не найдена колонка 'Новый с/н' (или 'Новый Серийный номер')",
        })
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num
        active_number = str(row.get("Актив", "") or "").strip()
        serial_number = str(row.get("Новый с/н") or row.get("Новый Серийный номер") or "").strip()

        if not active_number:
            errors.append({"row": row_num, "field": "Актив", "message": "Поле 'Актив' пустое"})
            continue

        if active_number in batch_numbers:
            errors.append({"row": row_num, "field": "Актив",
                            "message": f"Дубликат внутри файла: '{active_number}'"})
            continue

        result = await db_session.execute(
            select(Actives.id).where(Actives.active_number == active_number)
        )
        active_id = result.scalar_one_or_none()
        if active_id is None:
            errors.append({"row": row_num, "field": "Актив",
                            "message": f"Актив не найден: '{active_number}'"})
            continue

        if not serial_number:
            errors.append({"row": row_num, "field": "Новый с/н", "message": "Поле 'Новый с/н' пустое"})
            continue

        batch_numbers.add(active_number)
        valid_rows.append((active_number, serial_number))

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
        valid_rows: list[tuple[str, str]] = []
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

                progress.update(processed=0, total=len(valid_rows), phase="executing")

                try:
                    for i, (active_number, serial_number) in enumerate(valid_rows, start=1):
                        await session.execute(
                            text("UPDATE public.actives SET serial_number = :sn WHERE active_number = :an"),
                            {"sn": serial_number, "an": active_number},
                        )
                        if i % 20 == 0 or i == len(valid_rows):
                            progress["processed"] = i
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
            f"=== Update serial_number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        log_lines.extend(actives_sql.update_serial_number(valid_rows))
        log_lines.append("")

        log_file = LOG_DIR / f"update_serial_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated serial_number for %d rows, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Успешно обновлено serial_number для {len(valid_rows)} записей")
