import json
import logging
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Template, Response, Redirect

from db_manager import get_session_maker
from models import Orders
from schemas import SelectSheetRequest
import excel_upload
from parser_storage import LOG_DIR
from progress_tasks import progress_response, start_task
from sql_builders import orders as orders_sql

logger = logging.getLogger("order_parser")

PREFIX = "order_parser"


class OrderParserController(Controller):
    path = "/order-parser"

    @get("/")
    async def index(
        self,
        request: Request,
        page: int = 1,
        per_page: int = 10,
        select_sheet: bool = False,
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
        rows = all_rows[offset:offset + per_page]

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
        data: SelectSheetRequest = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        """Sheet choice for a multi-sheet Excel file — see excel_upload.handle_sheet_choice."""
        return excel_upload.handle_sheet_choice(request, PREFIX, "/order-parser", data.sheet_name, skip_blank_rows=True)

    async def _validate_and_build_rows(
        self, db_session: AsyncSession, rows: list[dict],
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
            errors.append({"row": 0, "field": "Заказ-наряд",
                            "message": "В файле не найдена колонка 'Заказ-наряд'"})
            return errors, valid_rows

        if rows and "Родительский ЗН" not in rows[0]:
            errors.append({"row": 0, "field": "Родительский ЗН",
                            "message": "В файле не найдена колонка 'Родительский ЗН'"})
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
                errors.append({"row": row_num, "field": "Родительский ЗН",
                                "message": f"Заказ-наряд не может быть родителем самому себе: '{child_number}'"})
                continue

            if child_number in batch_children:
                errors.append({"row": row_num, "field": "Заказ-наряд",
                                "message": f"Дубликат внутри файла: '{child_number}'"})
                continue

            result = await db_session.execute(select(Orders.id).where(Orders.order_number == child_number))
            child_id = result.scalar_one_or_none()
            if child_id is None:
                errors.append({"row": row_num, "field": "Заказ-наряд",
                                "message": f"Заказ-наряд не найден: '{child_number}'"})
                continue

            result = await db_session.execute(select(Orders.id).where(Orders.order_number == parent_number))
            parent_id = result.scalar_one_or_none()
            if parent_id is None:
                errors.append({"row": row_num, "field": "Родительский ЗН",
                                "message": f"Заказ-наряд не найден: '{parent_number}'"})
                continue

            batch_children.add(child_number)
            valid_rows.append((child_id, parent_id, child_number, parent_number))

        return errors, valid_rows

    @post("/generate-sql/start")
    async def generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_generate(progress, rows))

    async def _run_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await self._validate_and_build_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = orders_sql.assign_parent_order(valid_rows)
        progress.update(status="done", sql="\n".join(sql_lines), count=len(valid_rows))

    @post("/execute-sql/start")
    async def execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_execute(progress, rows))

    async def _run_execute(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await self._validate_and_build_rows(session, rows, progress=progress)
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}])
                    return

                progress.update(processed=0, total=len(valid_rows), phase="executing")
                try:
                    for i, (child_id, parent_id, child_number, parent_number) in enumerate(valid_rows, start=1):
                        await session.execute(
                            text(
                                "INSERT INTO public.order_to_order (id_parent, id_child) "
                                "VALUES (:parent_id, :child_id) "
                                "ON CONFLICT (id_child) DO UPDATE SET id_parent = EXCLUDED.id_parent"
                            ),
                            {"parent_id": parent_id, "child_id": child_id},
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
            f"=== Execute assign parent order: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
            *orders_sql.assign_parent_order(valid_rows),
            "",
        ]

        log_file = LOG_DIR / f"assign_parent_order_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Assigned parent order for %d rows, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Успешно присвоено {len(valid_rows)} родительских ЗН")

    @get("/progress/{task_id:str}")
    async def get_progress(self, task_id: str) -> Response:
        return progress_response(task_id)
