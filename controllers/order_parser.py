import logging

from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.params import FromPath, FromQuery, MultipartBody, URLEncodedBody
from litestar.response import Redirect, Response, Template
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import excel_upload
import sql_tasks
from models import Orders
from progress_tasks import progress_response
from schemas import SelectSheetRequest
from sql_builders import orders as orders_sql

logger = logging.getLogger("order_parser")

PREFIX = "order_parser"


async def validate_parent_order_rows(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
) -> tuple[list[dict], list[tuple[int, int, str, str]]]:
    """Validate the Excel rows for assigning a parent work order.

    Returns (errors, valid_rows), where valid_rows is a list of tuples
    (child_id, parent_id, child_number, parent_number).
    When a progress dict is passed, (processed, total, phase="validating")
    is written into it every few rows for the frontend to poll.
    """
    errors: list[dict] = []
    valid_rows: list[tuple[int, int, str, str]] = []
    batch_children: set[str] = set()

    if rows and "Заказ-наряд" not in rows[0]:
        errors.append({"row": 0, "field": "Заказ-наряд", "message": "В файле не найдена колонка 'Заказ-наряд'"})
        return errors, valid_rows

    if rows and "Родительский ЗН" not in rows[0]:
        errors.append({"row": 0, "field": "Родительский ЗН", "message": "В файле не найдена колонка 'Родительский ЗН'"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        child_number = str(row.get("Заказ-наряд", "") or "").strip()
        parent_number = str(row.get("Родительский ЗН", "") or "").strip()

        if not child_number:
            errors.append({"row": row_num, "field": "Заказ-наряд", "message": "Поле 'Заказ-наряд' пустое"})
            continue

        if not parent_number:
            errors.append({"row": row_num, "field": "Родительский ЗН", "message": "Поле 'Родительский ЗН' пустое"})
            continue

        if child_number == parent_number:
            errors.append(
                {
                    "row": row_num,
                    "field": "Родительский ЗН",
                    "message": f"Заказ-наряд не может быть родителем самому себе: '{child_number}'",
                }
            )
            continue

        if child_number in batch_children:
            errors.append(
                {"row": row_num, "field": "Заказ-наряд", "message": f"Дубликат внутри файла: '{child_number}'"}
            )
            continue

        result = await db_session.execute(select(Orders.id).where(Orders.order_number == child_number))
        child_id = result.scalar_one_or_none()
        if child_id is None:
            errors.append(
                {"row": row_num, "field": "Заказ-наряд", "message": f"Заказ-наряд не найден: '{child_number}'"}
            )
            continue

        result = await db_session.execute(select(Orders.id).where(Orders.order_number == parent_number))
        parent_id = result.scalar_one_or_none()
        if parent_id is None:
            errors.append(
                {"row": row_num, "field": "Родительский ЗН", "message": f"Заказ-наряд не найден: '{parent_number}'"}
            )
            continue

        batch_children.add(child_number)
        valid_rows.append((child_id, parent_id, child_number, parent_number))

    return errors, valid_rows


ASSIGN_PARENT_ORDER = sql_tasks.SqlOperation(
    validate=validate_parent_order_rows,
    build=orders_sql.assign_parent_order,
    log_name="assign_parent_order",
    log_title="assign parent order",
    summary=lambda total, valid_rows, errors, rowcounts: (
        f"Успешно присвоено {len(valid_rows)} родительских ЗН",
        [f"Rows updated: {len(valid_rows)}"],
    ),
    empty_message="Нет валидных строк для обновления",
    logger=logger,
)


class OrderParserController(Controller):
    path = "/order-parser"

    @get("/")
    async def index(
        self,
        request: Request,
        page: FromQuery[int] = 1,
        per_page: FromQuery[int] = 10,
        select_sheet: FromQuery[bool] = False,
    ) -> Template:
        page = max(page, 1)
        per_page = min(per_page, 200)
        error: str = request.session.pop(f"{PREFIX}_error", "")

        pending_sheets: list[str] = []
        pending_filename: str = ""
        if select_sheet:
            pending_sheets = request.session.get(f"{PREFIX}_pending_sheets", [])
            pending_filename = request.session.get(f"{PREFIX}_pending_filename", "")

        stored = excel_upload.stored_data(request, PREFIX)

        all_rows: list[dict] = stored["rows"] if stored else []
        headers: list[str] = stored["headers"] if stored else []
        filename: str = stored["filename"] if stored else ""

        total = len(all_rows)
        total_pages = max((total + per_page - 1) // per_page, 1)
        if page > total_pages:
            page = total_pages

        offset = (page - 1) * per_page
        rows = all_rows[offset : offset + per_page]

        return Template(
            template_name="order_parser.html",
            context={
                "headers": headers,
                "rows": rows,
                "all_rows": all_rows,
                "filename": filename,
                "error": error,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "order_parser",
                "pending_sheets": pending_sheets,
                "pending_filename": pending_filename,
            },
        )

    @post("/upload")
    async def upload(self, request: Request) -> Redirect:
        """Upload of an Excel file (.xlsx/.xls) — see excel_upload.handle_upload."""
        return await excel_upload.handle_upload(request, PREFIX, "/order-parser", skip_blank_rows=True)

    @post("/select-sheet")
    async def select_sheet(
        self,
        request: Request,
        data: URLEncodedBody[SelectSheetRequest],
    ) -> Redirect:
        """Sheet choice for a multi-sheet Excel file — see excel_upload.handle_sheet_choice."""
        return excel_upload.handle_sheet_choice(request, PREFIX, "/order-parser", data.sheet_name, skip_blank_rows=True)

    @post("/generate-sql/start")
    async def generate_sql_start(self, data: MultipartBody[dict]) -> Response:
        return sql_tasks.start_generate(data, ASSIGN_PARENT_ORDER)

    @post("/execute-sql/start")
    async def execute_sql_start(self, data: MultipartBody[dict]) -> Response:
        return sql_tasks.start_execute(data, ASSIGN_PARENT_ORDER)

    @get("/progress/{task_id:str}")
    async def get_progress(self, task_id: FromPath[str]) -> Response:
        return progress_response(task_id)
