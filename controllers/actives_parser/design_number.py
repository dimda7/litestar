import logging
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.response import Response

from db_manager import get_session_maker
from models import Actives, DesignNumber
import excel_upload
from parser_storage import LOG_DIR
from progress_tasks import error_response, start_task
from sql_builders import actives as actives_sql

from .common import PREFIX


logger = logging.getLogger("actives_parser")


async def validate_design_number(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[tuple[str, int, str]]]:
    """Validate rows for actives.id_design_number update.
    Returns (errors, valid_rows) where valid_rows is [(active_number, design_number_id, design_number), ...]
    """
    errors: list[dict] = []
    valid_rows: list[tuple[str, int, str]] = []
    batch_numbers: set[str] = set()

    if rows and not ({"Новая Позиция ТМЦ", "Новый ТМЦ номер", "Позиция ТМЦ"} & rows[0].keys()):
        errors.append({
            "row": 0,
            "field": "Новая Позиция ТМЦ",
            "message": "В файле не найдена колонка 'Новая Позиция ТМЦ' (или 'Новый ТМЦ номер', 'Позиция ТМЦ')",
        })
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num
        active_number = str(row.get("Актив", "") or "").strip()
        design_number = str(
            row.get("Новая Позиция ТМЦ") or row.get("Новый ТМЦ номер") or row.get("Позиция ТМЦ") or ""
        ).strip()

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

        if not design_number:
            errors.append({"row": row_num, "field": "Новая Позиция ТМЦ", "message": "Поле 'Новая Позиция ТМЦ' ('Новый ТМЦ номер') пустое"})
            continue

        result = await db_session.execute(
            select(DesignNumber.id).where(DesignNumber.number == design_number)
        )
        design_number_id = result.scalar_one_or_none()
        if design_number_id is None:
            errors.append({"row": row_num, "field": "Новая Позиция ТМЦ",
                            "message": f"Позиция ТМЦ не найдена: '{design_number}'"})
            continue

        batch_numbers.add(active_number)
        valid_rows.append((active_number, design_number_id, design_number))

    return errors, valid_rows


class DesignNumberController(Controller):
    path = "/actives-parser"

    @post("/design-number/generate-sql/start")
    async def design_number_generate_sql_start(self, request: Request) -> Response:
        """Start background generation of the id_design_number update SQL file; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_design_number_generate(progress, rows))

    async def _run_design_number_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_design_number(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}])
            return

        sql_lines = actives_sql.update_design_number(valid_rows)
        progress.update(status="done", sql="\n".join(sql_lines), count=len(sql_lines))

    @post("/design-number/execute/start")
    async def design_number_execute_start(self, request: Request) -> Response:
        """Start the background id_design_number update in the database; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_design_number_execute(progress, rows))

    async def _run_design_number_execute(self, progress: dict, rows: list[dict]) -> None:
        valid_rows: list[tuple[str, int, str]] = []
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_design_number(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}])
                    return

                progress.update(processed=0, total=len(valid_rows), phase="executing")

                try:
                    for i, (active_number, design_number_id, _) in enumerate(valid_rows, start=1):
                        await session.execute(
                            text("UPDATE public.actives SET id_design_number = :dn_id WHERE active_number = :an"),
                            {"dn_id": design_number_id, "an": active_number},
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
            f"=== Update id_design_number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        log_lines.extend(actives_sql.update_design_number(valid_rows, with_comment=True))
        log_lines.append("")

        log_file = LOG_DIR / f"update_design_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated id_design_number for %d rows, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Успешно обновлено id_design_number для {len(valid_rows)} записей")
