import logging

from litestar import Controller, post
from litestar.connection.request import Request
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import excel_upload
import sql_tasks
from models import Actives, DesignNumber
from progress_tasks import error_response
from sql_builders import actives as actives_sql

from .common import PREFIX

logger = logging.getLogger("actives_parser")


async def validate_design_number(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
) -> tuple[list[dict], list[tuple[str, int, str]]]:
    """Validate rows for actives.id_design_number update.
    Returns (errors, valid_rows) where valid_rows is [(active_number, design_number_id, design_number), ...]
    """
    errors: list[dict] = []
    valid_rows: list[tuple[str, int, str]] = []
    batch_numbers: set[str] = set()

    if rows and not ({"Новая Позиция ТМЦ", "Новый ТМЦ номер", "Позиция ТМЦ"} & rows[0].keys()):
        errors.append(
            {
                "row": 0,
                "field": "Новая Позиция ТМЦ",
                "message": "В файле не найдена колонка 'Новая Позиция ТМЦ' (или 'Новый ТМЦ номер', 'Позиция ТМЦ')",
            }
        )
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
            errors.append({"row": row_num, "field": "Актив", "message": f"Дубликат внутри файла: '{active_number}'"})
            continue

        result = await db_session.execute(select(Actives.id).where(Actives.active_number == active_number))
        active_id = result.scalar_one_or_none()
        if active_id is None:
            errors.append({"row": row_num, "field": "Актив", "message": f"Актив не найден: '{active_number}'"})
            continue

        if not design_number:
            errors.append(
                {
                    "row": row_num,
                    "field": "Новая Позиция ТМЦ",
                    "message": "Поле 'Новая Позиция ТМЦ' ('Новый ТМЦ номер') пустое",
                }
            )
            continue

        result = await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == design_number))
        design_number_id = result.scalar_one_or_none()
        if design_number_id is None:
            errors.append(
                {"row": row_num, "field": "Новая Позиция ТМЦ", "message": f"Позиция ТМЦ не найдена: '{design_number}'"}
            )
            continue

        batch_numbers.add(active_number)
        valid_rows.append((active_number, design_number_id, design_number))

    return errors, valid_rows


def _build_design_number(valid_rows: list[tuple[str, int, str]]) -> list[str]:
    return actives_sql.update_design_number(valid_rows, with_comment=True)


DESIGN_NUMBER = sql_tasks.SqlOperation(
    validate=validate_design_number,
    build=_build_design_number,
    log_name="update_design_number",
    log_title="id_design_number update",
    summary=lambda total, valid_rows, errors, rowcounts: (
        f"Успешно обновлено id_design_number для {len(valid_rows)} записей",
        [f"Rows updated: {len(valid_rows)}"],
    ),
    empty_message="Нет валидных строк для обновления",
    logger=logger,
)


class DesignNumberController(Controller):
    path = "/actives-parser"

    @post("/design-number/generate-sql/start")
    async def design_number_generate_sql_start(self, request: Request) -> Response:
        """Start background generation of the id_design_number update SQL file; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")
        return sql_tasks.generate_rows(rows, DESIGN_NUMBER)

    @post("/design-number/execute/start")
    async def design_number_execute_start(self, request: Request) -> Response:
        """Start the background id_design_number update in the database; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")
        return sql_tasks.execute_rows(rows, DESIGN_NUMBER)
