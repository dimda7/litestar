import json
import logging
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import openpyxl
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Template, Response, Redirect

from models import Actives
from schemas import SelectSheetRequest
from sql_utils import sql_escape
from parser_storage import (
    LOG_DIR,
    load_data as _load_data,
    save_data as _save_data,
    cleanup_old_files as _cleanup_old_files,
)

logger = logging.getLogger("actives_parser")

PREFIX = "actives_parser"


class ActivesParserController(Controller):
    path = "/actives-parser"

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

        session_id = request.session.get(f"{PREFIX}_session_id", "")
        stored = _load_data(session_id) if session_id else None

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
            template_name="actives_parser.html",
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
                "active_page": "actives_parser",
                "pending_sheets": pending_sheets,
                "pending_filename": pending_filename,
            },
        )

    @post("/upload")
    async def upload(self, request: Request) -> Redirect:
        form = await request.form()
        upload_file: UploadFile | None = form.get("file")

        if not upload_file or not upload_file.filename:
            request.session[f"{PREFIX}_error"] = "Файл не выбран"
            return Redirect("/actives-parser")

        suffix = Path(upload_file.filename).suffix.lower()
        if suffix not in (".xlsx", ".xls"):
            request.session[f"{PREFIX}_error"] = "Поддерживаются только .xlsx и .xls файлы"
            return Redirect("/actives-parser")

        try:
            _cleanup_old_files()

            content = await upload_file.read()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            wb = openpyxl.load_workbook(tmp_path, read_only=True)
            sheet_names = wb.sheetnames
            wb.close()

            if len(sheet_names) > 1:
                request.session[f"{PREFIX}_pending_file"] = tmp_path
                request.session[f"{PREFIX}_pending_sheets"] = sheet_names
                request.session[f"{PREFIX}_pending_filename"] = upload_file.filename
                return Redirect("/actives-parser?select_sheet=1")

            wb = openpyxl.load_workbook(tmp_path, read_only=True)
            ws = wb.active

            rows: list[dict[str, str | None]] = []
            headers: list[str] = []

            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    headers = [str(c) if c else f"col_{i}" for i, c in enumerate(row)]
                    continue
                if all(c is None or str(c).strip() == "" for c in row):
                    continue
                rows.append({headers[j]: row[j] for j in range(len(row))})

            wb.close()
            Path(tmp_path).unlink(missing_ok=True)

            session_id = uuid.uuid4().hex
            _save_data(session_id, {
                "headers": headers,
                "rows": rows,
                "filename": upload_file.filename,
            })
            request.session[f"{PREFIX}_session_id"] = session_id

            return Redirect("/actives-parser")
        except Exception as e:
            request.session[f"{PREFIX}_error"] = f"Ошибка чтения файла: {e}"
            return Redirect("/actives-parser")

    @post("/select-sheet")
    async def select_sheet(
        self,
        request: Request,
        data: SelectSheetRequest = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        sheet_name = data.sheet_name

        tmp_path = request.session.get(f"{PREFIX}_pending_file", "")
        filename = request.session.get(f"{PREFIX}_pending_filename", "")
        sheet_names = request.session.get(f"{PREFIX}_pending_sheets", [])

        if not tmp_path or not Path(tmp_path).exists():
            request.session[f"{PREFIX}_error"] = "Временный файл истёк. Загрузите файл заново."
            return Redirect("/actives-parser")

        if sheet_name not in sheet_names:
            request.session[f"{PREFIX}_error"] = "Выбранный лист не найден в файле."
            return Redirect("/actives-parser")

        try:
            wb = openpyxl.load_workbook(tmp_path, read_only=True)
            ws = wb[sheet_name]

            rows: list[dict[str, str | None]] = []
            headers: list[str] = []

            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    headers = [str(c) if c else f"col_{i}" for i, c in enumerate(row)]
                    continue
                if all(c is None or str(c).strip() == "" for c in row):
                    continue
                rows.append({headers[j]: row[j] for j in range(len(row))})

            wb.close()
            Path(tmp_path).unlink(missing_ok=True)

            request.session.pop(f"{PREFIX}_pending_file", None)
            request.session.pop(f"{PREFIX}_pending_sheets", None)
            request.session.pop(f"{PREFIX}_pending_filename", None)

            session_id = uuid.uuid4().hex
            _save_data(session_id, {
                "headers": headers,
                "rows": rows,
                "filename": f"{filename} [{sheet_name}]",
            })
            request.session[f"{PREFIX}_session_id"] = session_id

            return Redirect("/actives-parser")
        except Exception as e:
            request.session[f"{PREFIX}_error"] = f"Ошибка чтения листа: {e}"
            return Redirect("/actives-parser")

    async def _validate_serial_number(
        self, db_session: AsyncSession, rows: list[dict]
    ) -> tuple[list[dict], list[tuple[str, str]]]:
        """Validate rows for actives.serial_number update.
        Returns (errors, valid_rows) where valid_rows is [(active_number, serial_number), ...]
        """
        errors: list[dict] = []
        valid_rows: list[tuple[str, str]] = []
        batch_numbers: set[str] = set()

        for idx, row in enumerate(rows):
            row_num = idx + 1
            active_number = str(row.get("Актив", "") or "").strip()
            serial_number = str(row.get("Новый с/н", "") or "").strip()

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

    @post("/generate-sql-serial-number")
    async def generate_sql_serial_number(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_serial_number(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = [
            f"UPDATE public.actives SET serial_number = '{sql_escape(serial_number)}' "
            f"WHERE active_number = '{sql_escape(active_number)}';"
            for active_number, serial_number in valid_rows
        ]
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )

    @post("/update-serial-number")
    async def update_serial_number(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_serial_number(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}]}),
                status_code=200,
                media_type="application/json",
            )

        try:
            for active_number, serial_number in valid_rows:
                await db_session.execute(
                    text("UPDATE public.actives SET serial_number = :sn WHERE active_number = :an"),
                    {"sn": serial_number, "an": active_number},
                )
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]}),
                status_code=200,
                media_type="application/json",
            )

        now = datetime.now()
        log_lines = [
            f"=== Update serial_number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        for active_number, serial_number in valid_rows:
            log_lines.append(
                f"UPDATE public.actives SET serial_number = '{sql_escape(serial_number)}' "
                f"WHERE active_number = '{sql_escape(active_number)}';"
            )
        log_lines.append("")

        log_file = LOG_DIR / f"update_serial_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated serial_number for %d rows, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps({"status": "ok", "count": len(valid_rows), "message": f"Успешно обновлено serial_number для {len(valid_rows)} записей"}),
            status_code=200,
            media_type="application/json",
        )
