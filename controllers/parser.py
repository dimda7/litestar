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

from models import TrainType, CarPlace, DesignNumber, Models
from schemas import (
    GenerateSQLRequest, DeleteRowsRequest, SelectSheetRequest,
    GenerateSQLResponse, ExecuteSQLResponse,
)
from sql_utils import sql_escape
from parser_storage import (
    LOG_DIR,
    load_data as _load_data,
    save_data as _save_data,
    cleanup_old_files as _cleanup_old_files,
)

logger = logging.getLogger("parser")


class ParserController(Controller):
    path = "/parser"

    @get("/")
    async def index(self, request: Request, page: int = 1, per_page: int = 10, select_sheet: bool = False) -> Template:
        page = max(page, 1)
        per_page = min(per_page, 200)
        error: str = request.session.pop("parser_error", "")

        pending_sheets: list[str] = []
        pending_filename: str = ""
        if select_sheet:
            pending_sheets = request.session.get("parser_pending_sheets", [])
            pending_filename = request.session.get("parser_pending_filename", "")

        session_id = request.session.get("parser_session_id", "")
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
            template_name="parser.html",
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
                "active_page": "parser",
                "pending_sheets": pending_sheets,
                "pending_filename": pending_filename,
            },
        )

    @post("/upload")
    async def upload(self, request: Request) -> Redirect:
        """Загрузка Excel-файла (.xlsx/.xls).

        Парсит файл, извлекает заголовки и строки.
        Если файл содержит несколько листов, перенаправляет на выбор листа.
        """
        form = await request.form()
        upload_file: UploadFile | None = form.get("file")

        if not upload_file or not upload_file.filename:
            request.session["parser_error"] = "Файл не выбран"
            return Redirect("/parser")

        suffix = Path(upload_file.filename).suffix.lower()
        if suffix not in (".xlsx", ".xls"):
            request.session["parser_error"] = "Поддерживаются только .xlsx и .xls файлы"
            return Redirect("/parser")

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
                request.session["parser_pending_file"] = tmp_path
                request.session["parser_pending_sheets"] = sheet_names
                request.session["parser_pending_filename"] = upload_file.filename
                return Redirect("/parser?select_sheet=1")

            wb = openpyxl.load_workbook(tmp_path, read_only=True)
            ws = wb.active

            rows: list[dict[str, str | None]] = []
            headers: list[str] = []

            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    headers = [str(c) if c else f"col_{i}" for i, c in enumerate(row)]
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
            request.session["parser_session_id"] = session_id

            return Redirect("/parser")
        except Exception as e:
            request.session["parser_error"] = f"Ошибка чтения файла: {e}"
            return Redirect("/parser")

    @post("/select-sheet")
    async def select_sheet(
        self,
        request: Request,
        data: SelectSheetRequest = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        """Выбор листа Excel из.multi-sheet файла.

        Парсит указанный лист и сохраняет данные для дальнейшей обработки.
        """
        sheet_name = data.sheet_name

        tmp_path = request.session.get("parser_pending_file", "")
        filename = request.session.get("parser_pending_filename", "")
        sheet_names = request.session.get("parser_pending_sheets", [])

        if not tmp_path or not Path(tmp_path).exists():
            request.session["parser_error"] = "Временный файл истёк. Загрузите файл заново."
            return Redirect("/parser")

        if sheet_name not in sheet_names:
            request.session["parser_error"] = "Выбранный лист не найден в файле."
            return Redirect("/parser")

        try:
            wb = openpyxl.load_workbook(tmp_path, read_only=True)
            ws = wb[sheet_name]

            rows: list[dict[str, str | None]] = []
            headers: list[str] = []

            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    headers = [str(c) if c else f"col_{i}" for i, c in enumerate(row)]
                    continue
                rows.append({headers[j]: row[j] for j in range(len(row))})

            wb.close()
            Path(tmp_path).unlink(missing_ok=True)

            request.session.pop("parser_pending_file", None)
            request.session.pop("parser_pending_sheets", None)
            request.session.pop("parser_pending_filename", None)

            session_id = uuid.uuid4().hex
            _save_data(session_id, {
                "headers": headers,
                "rows": rows,
                "filename": f"{filename} [{sheet_name}]",
            })
            request.session["parser_session_id"] = session_id

            return Redirect("/parser")
        except Exception as e:
            request.session["parser_error"] = f"Ошибка чтения листа: {e}"
            return Redirect("/parser")

    async def _validate_and_build_rows(self, db_session: AsyncSession, rows: list[dict]) -> tuple[list[dict[str, str]], list[tuple[int, int, int, str, bool]]]:
        existing_rows = await db_session.execute(
            select(Models.id_train_type, Models.lcn, Models.id_car_place,
                   Models.id_design_number, Models.is_default)
        )
        existing_set: set[tuple] = set()
        for er in existing_rows.all():
            existing_set.add((er[0], er[1], er[2], er[3], er[4]))

        existing_default_lcn_car: set[tuple] = set()
        existing_default_car_type_design: set[tuple] = set()
        for er in existing_set:
            if er[4]:
                existing_default_lcn_car.add((er[1], er[2]))
                existing_default_car_type_design.add((er[2], er[0], er[3]))

        errors: list[dict[str, str]] = []
        valid_rows: list[tuple[int, int, int, str, bool]] = []

        batch_full: set[tuple] = set()
        batch_default_lcn_car: set[tuple] = set()
        batch_default_car_type_design: set[tuple] = set()

        for idx, row in enumerate(rows):
            row_num = idx + 1
            model_name = str(row.get("model", "")).strip()
            position = str(row.get("position", "")).strip()
            itemnum = str(row.get("itemnum", "")).strip()
            lcn = str(row.get("lsn", "") or row.get("lcn", "")).strip()
            isdefault = str(row.get("isdefault", "")).strip().lower()
            is_default = isdefault == "true"

            train_type_id: int | None = None
            if model_name:
                result = await db_session.execute(
                    select(TrainType.id).where(TrainType.name == model_name)
                )
                r = result.scalar_one_or_none()
                if r is not None:
                    train_type_id = r
                else:
                    errors.append({"row": row_num, "field": "model",
                                   "message": f"train_type не найден: '{model_name}'"})

            car_place_id: int | None = None
            if position and position != "null":
                result = await db_session.execute(
                    select(CarPlace.id).where(CarPlace.name == position)
                )
                r = result.scalar_one_or_none()
                if r is not None:
                    car_place_id = r
                else:
                    errors.append({"row": row_num, "field": "position",
                                   "message": f"car_place не найден: '{position}'"})

            design_number_id: int | None = None
            if itemnum:
                result = await db_session.execute(
                    select(DesignNumber.id).where(DesignNumber.number == itemnum)
                )
                r = result.scalar_one_or_none()
                if r is not None:
                    design_number_id = r
                else:
                    errors.append({"row": row_num, "field": "itemnum",
                                   "message": f"design_number не найден: '{itemnum}'"})

            if train_type_id is None or car_place_id is None or design_number_id is None:
                continue

            full_tuple = (train_type_id, lcn, car_place_id, design_number_id, is_default)
            if full_tuple in existing_set or full_tuple in batch_full:
                errors.append({
                    "row": row_num, "field": "*",
                    "message": (f"Дубликат: строка (train_type={train_type_id}, lcn='{lcn}', "
                                f"car_place={car_place_id}, design_number={design_number_id}, "
                                f"is_default={is_default}) уже существует"),
                })
                continue

            if is_default:
                if (lcn, car_place_id) in existing_default_lcn_car or (lcn, car_place_id) in batch_default_lcn_car:
                    errors.append({
                        "row": row_num, "field": "lcn",
                        "message": (f"Конфликт unique (lcn, car_place) WHERE is_default=true: "
                                    f"lcn='{lcn}', car_place={car_place_id} уже заняты"),
                    })
                    continue
                if ((car_place_id, train_type_id, design_number_id) in existing_default_car_type_design
                        or (car_place_id, train_type_id, design_number_id) in batch_default_car_type_design):
                    errors.append({
                        "row": row_num, "field": "*",
                        "message": (f"Конфликт unique (car_place, train_type, design_number) WHERE is_default=true: "
                                    f"car_place={car_place_id}, train_type={train_type_id}, "
                                    f"design_number={design_number_id} уже заняты"),
                    })
                    continue

            batch_full.add(full_tuple)
            if is_default:
                batch_default_lcn_car.add((lcn, car_place_id))
                batch_default_car_type_design.add((car_place_id, train_type_id, design_number_id))

            valid_rows.append((train_type_id, car_place_id, design_number_id, lcn, is_default))

        return errors, valid_rows

    @post("/generate-sql")
    async def generate_sql(
        self,
        request: Request,
        db_session: AsyncSession,
        data: GenerateSQLRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Генерация SQL-файла для вставки строк в таблицу grom.models.

        Валидирует данные из Excel, проверяет дубликаты и уникальные ограничения,
        затем возвращает SQL-код для скачивания в виде .sql файла.
        """
        rows: list[dict] = json.loads(data.rows)
        errors, valid_rows = await self._validate_and_build_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines: list[str] = []
        for train_type_id, car_place_id, design_number_id, lcn, is_default in valid_rows:
            isdefault_val = "TRUE" if is_default else "FALSE"
            sql = (
                f"INSERT INTO models (id_train_type, id_car_place, id_design_number, lcn, is_default) "
                f"VALUES ({train_type_id}, {car_place_id}, {design_number_id}, '{sql_escape(lcn)}', {isdefault_val});"
            )
            sql_lines.append(sql)

        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )

    @post("/execute-sql")
    async def execute_sql(
        self,
        request: Request,
        db_session: AsyncSession,
        data: GenerateSQLRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Атомарная вставка строк в таблицу grom.models.

        Валидирует данные, выполняет все INSERT-запросы в одной транзакции.
        При ошибке вся транзакция откатывается. Логирует результат в log/.
        """
        rows: list[dict] = json.loads(data.rows)
        errors, valid_rows = await self._validate_and_build_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps({"status": "error", "errors": ["Нет валидных строк для вставки"]}),
                status_code=200,
                media_type="application/json",
            )

        try:
            for train_type_id, car_place_id, design_number_id, lcn, is_default in valid_rows:
                isdefault_val = "TRUE" if is_default else "FALSE"
                await db_session.execute(
                    text(
                        "INSERT INTO grom.models (id_train_type, id_car_place, id_design_number, lcn, is_default) "
                        "VALUES (:tt, :cp, :dn, :lcn, :def)"
                    ),
                    {"tt": train_type_id, "cp": car_place_id, "dn": design_number_id, "lcn": lcn, "def": is_default},
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
            f"=== Execute SQL: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows inserted: {len(valid_rows)}",
            "",
        ]
        for train_type_id, car_place_id, design_number_id, lcn, is_default in valid_rows:
            isdefault_val = "TRUE" if is_default else "FALSE"
            log_lines.append(
                f"INSERT INTO grom.models (id_train_type, id_car_place, id_design_number, lcn, is_default) "
                f"VALUES ({train_type_id}, {car_place_id}, {design_number_id}, '{sql_escape(lcn)}', {isdefault_val});"
            )
        log_lines.append("")

        log_file = LOG_DIR / f"insert_models_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("SQL executed: %d rows inserted, log saved to %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps({"status": "ok", "count": len(valid_rows), "message": f"Успешно вставлено {len(valid_rows)} строк"}),
            status_code=200,
            media_type="application/json",
        )

    @post("/delete-rows")
    async def delete_rows(
        self,
        request: Request,
        data: DeleteRowsRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Генерация SQL-файла для удаления строк из таблицы grom.models.

        Принимает массив объектов с полем id, возвращает SQL-код для скачивания.
        """
        rows: list[dict] = json.loads(data.rows)

        errors: list[dict[str, str]] = []
        valid_ids: list[int] = []

        for idx, row in enumerate(rows):
            row_num = idx + 1
            row_id = row.get("id")
            if not row_id:
                errors.append({"row": row_num, "field": "id",
                               "message": "Поле 'id' отсутствует или пустое"})
                continue
            valid_ids.append(int(row_id))

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = [f"DELETE FROM models WHERE id = {rid};" for rid in valid_ids]
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )

    @post("/execute-delete")
    async def execute_delete(
        self,
        request: Request,
        db_session: AsyncSession,
        data: DeleteRowsRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Атомарное удаление строк из таблицы grom.models.

        Выполняет DELETE-запросы в одной транзакции. При ошибке откат.
        Логирует результат в log/.
        """
        rows: list[dict] = json.loads(data.rows)

        errors: list[dict[str, str]] = []
        valid_ids: list[int] = []

        for idx, row in enumerate(rows):
            row_num = idx + 1
            row_id = row.get("id")
            if not row_id:
                errors.append({"row": row_num, "field": "id",
                               "message": "Поле 'id' отсутствует или пустое"})
                continue
            valid_ids.append(int(row_id))

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_ids:
            return Response(
                content=json.dumps({"status": "error", "errors": ["Нет валидных строк для удаления"]}),
                status_code=200,
                media_type="application/json",
            )

        try:
            for rid in valid_ids:
                await db_session.execute(
                    text("DELETE FROM grom.models WHERE id = :id"),
                    {"id": rid},
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
            f"=== Execute Delete: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows deleted: {len(valid_ids)}",
            "",
        ]
        for rid in valid_ids:
            log_lines.append(f"DELETE FROM grom.models WHERE id = {rid};")
        log_lines.append("")

        log_file = LOG_DIR / f"delete_models_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("SQL executed: %d rows deleted, log saved to %s", len(valid_ids), log_file)

        return Response(
            content=json.dumps({"status": "ok", "count": len(valid_ids), "message": f"Успешно удалено {len(valid_ids)} строк"}),
            status_code=200,
            media_type="application/json",
        )
