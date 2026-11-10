import logging

from litestar import Controller, post
from litestar.connection.request import Request
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import excel_upload
import sql_tasks
from models import Actives
from progress_tasks import error_response
from sql_builders import actives as actives_sql

from .common import PREFIX

logger = logging.getLogger("actives_parser")

LOOKUP_CHUNK = 500


async def validate_serial_number(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
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
        errors.append(
            {
                "row": 0,
                "field": "Новый с/н",
                "message": "В файле не найдена колонка 'Новый с/н' (или 'Новый серийный номер')",
            }
        )
        return errors, valid_rows
    if rows and modification_column is None:
        errors.append({"row": 0, "field": "Модификация", "message": "В файле не найдена колонка 'Модификация'"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    # One SELECT per chunk instead of one per row: the files run to ~50k rows.
    numbers = list({str(row.get("Актив", "") or "").strip() for row in rows} - {""})
    existing: set[str | None] = set()
    for start in range(0, len(numbers), LOOKUP_CHUNK):
        result = await db_session.execute(
            select(Actives.active_number).where(Actives.active_number.in_(numbers[start : start + LOOKUP_CHUNK]))
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
            errors.append({"row": row_num, "field": "Актив", "message": f"Дубликат внутри файла: '{active_number}'"})
            continue

        if active_number not in existing:
            errors.append({"row": row_num, "field": "Актив", "message": f"Актив не найден: '{active_number}'"})
            continue

        if not serial_number:
            errors.append({"row": row_num, "field": "Новый с/н", "message": "Поле 'Новый с/н' пустое"})
            continue

        batch_numbers.add(active_number)
        valid_rows.append((active_number, serial_number, modification))

    return errors, valid_rows


def _serial_number_summary(
    total: int, valid_rows: list[tuple[str, str, str | None]], errors: list[dict], rowcounts: list[int]
) -> tuple[str, list[str]]:
    updated = sum(rowcounts)
    return (
        f"Обновлены serial_number и modification: {updated} активов "
        f"(строк файла: {len(valid_rows)}, остальные уже совпадали)",
        [f"Rows processed: {len(valid_rows)}, actives updated: {updated}"],
    )


SERIAL_NUMBER = sql_tasks.SqlOperation(
    validate=validate_serial_number,
    build=actives_sql.update_serial_number,
    log_name="update_serial_number",
    log_title="serial_number + modification update",
    summary=_serial_number_summary,
    empty_message="Нет валидных строк для обновления",
    logger=logger,
    per_statement=True,
)


class SerialNumberController(Controller):
    path = "/actives-parser"

    @post("/serial-number/generate-sql/start")
    async def serial_number_generate_sql_start(self, request: Request) -> Response:
        """Start background generation of the serial_number update SQL file; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")
        return sql_tasks.generate_rows(rows, SERIAL_NUMBER)

    @post("/serial-number/execute/start")
    async def serial_number_execute_start(self, request: Request) -> Response:
        """Start the background serial_number update in the database; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")
        return sql_tasks.execute_rows(rows, SERIAL_NUMBER)
