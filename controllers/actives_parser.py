import asyncio
import json
import logging
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import openpyxl
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Template, Response, Redirect

from db_manager import get_session_maker
from models import (
    Actives, DesignNumber, Storage, StoragePlace, Consignment, Materials,
    IteratorNumberLast, Location,
)
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

ACTIVE_NUMBER_COUNTER_DESCRIPTION = "Номер следующего актива"

# Как и в train_parser: валидация строк ТМЦ дёргает несколько запросов к БД на
# строку, на больших файлах это идёт секундами — прогресс отдаётся через
# отдельный опрос, чтобы не держать один HTTP-запрос открытым всё это время.
PROGRESS_TTL_SECONDS = 15 * 60
_progress: dict[str, dict] = {}
_tasks: dict[str, asyncio.Task] = {}


def _cleanup_progress() -> None:
    cutoff = time.time() - PROGRESS_TTL_SECONDS
    stale = [tid for tid, state in _progress.items() if state["created_at"] < cutoff]
    for tid in stale:
        _progress.pop(tid, None)


class StorageRepository(SQLAlchemyAsyncRepository[Storage]):
    model_type = Storage


class StoragePlaceRepository(SQLAlchemyAsyncRepository[StoragePlace]):
    model_type = StoragePlace


class ConsignmentRepository(SQLAlchemyAsyncRepository[Consignment]):
    model_type = Consignment


class DesignNumberRepository(SQLAlchemyAsyncRepository[DesignNumber]):
    model_type = DesignNumber


class IteratorNumberLastRepository(SQLAlchemyAsyncRepository[IteratorNumberLast]):
    model_type = IteratorNumberLast


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

        if rows and "Новый с/н" not in rows[0] and "Новый Серийный номер" not in rows[0]:
            errors.append({
                "row": 0,
                "field": "Новый с/н",
                "message": "В файле не найдена колонка 'Новый с/н' (или 'Новый Серийный номер')",
            })
            return errors, valid_rows

        for idx, row in enumerate(rows):
            row_num = idx + 1
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

    async def _validate_design_number(
        self, db_session: AsyncSession, rows: list[dict]
    ) -> tuple[list[dict], list[tuple[str, int, str]]]:
        """Validate rows for actives.id_design_number update.
        Returns (errors, valid_rows) where valid_rows is [(active_number, design_number_id, design_number), ...]
        """
        errors: list[dict] = []
        valid_rows: list[tuple[str, int, str]] = []
        batch_numbers: set[str] = set()

        if rows and "Новая Позиция ТМЦ" not in rows[0]:
            errors.append({
                "row": 0,
                "field": "Новая Позиция ТМЦ",
                "message": "В файле не найдена колонка 'Новая Позиция ТМЦ'",
            })
            return errors, valid_rows

        for idx, row in enumerate(rows):
            row_num = idx + 1
            active_number = str(row.get("Актив", "") or "").strip()
            design_number = str(row.get("Новая Позиция ТМЦ") or "").strip()

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
                errors.append({"row": row_num, "field": "Новая Позиция ТМЦ", "message": "Поле 'Новая Позиция ТМЦ' пустое"})
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

    @post("/generate-sql-design-number")
    async def generate_sql_design_number(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_design_number(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = [
            f"UPDATE public.actives SET id_design_number = {design_number_id} "
            f"WHERE active_number = '{sql_escape(active_number)}';"
            for active_number, design_number_id, _ in valid_rows
        ]
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )

    @post("/update-design-number")
    async def update_design_number(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_design_number(db_session, rows)

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
            for active_number, design_number_id, _ in valid_rows:
                await db_session.execute(
                    text("UPDATE public.actives SET id_design_number = :dn_id WHERE active_number = :an"),
                    {"dn_id": design_number_id, "an": active_number},
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
            f"=== Update id_design_number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        for active_number, design_number_id, design_number in valid_rows:
            log_lines.append(
                f"UPDATE public.actives SET id_design_number = {design_number_id} "
                f"WHERE active_number = '{sql_escape(active_number)}'; -- '{sql_escape(design_number)}'"
            )
        log_lines.append("")

        log_file = LOG_DIR / f"update_design_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated id_design_number for %d rows, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps({"status": "ok", "count": len(valid_rows), "message": f"Успешно обновлено id_design_number для {len(valid_rows)} записей"}),
            status_code=200,
            media_type="application/json",
        )

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

    async def _validate_create_actives_rows(
        self, db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
    ) -> tuple[list[dict], list[dict], dict[tuple[int, int], int | None], dict[int, int], int]:
        """Валидирует строки ТМЦ и строит план создания активов (замена add_active_spcial).

        valid_rows — один элемент на каждый создаваемый актив ('Количество' раскрыто построчно).
        materials_plan — (id_design_number, id_storage) -> id существующей записи materials,
        либо None, если для этой пары нужно создать новую. Новая materials создаётся один раз
        на пару и переиспользуется всеми строками файла с той же парой — старый код на peewee
        проверял наличие materials только в живой БД и не видел ещё не выполненные вставки из
        того же файла, поэтому на файлах с повторяющимися (ТМЦ, склад) плодил дубли materials.
        """
        storage_repo = StorageRepository(session=db_session)
        storage_place_repo = StoragePlaceRepository(session=db_session)
        consignment_repo = ConsignmentRepository(session=db_session)
        design_number_repo = DesignNumberRepository(session=db_session)
        iterator_repo = IteratorNumberLastRepository(session=db_session)

        errors: list[dict] = []
        valid_rows: list[dict] = []
        materials_plan: dict[tuple[int, int], int | None] = {}
        storage_last_lcn: dict[int, int] = {}

        storage_cache: dict[str, Storage | None] = {}
        storage_place_cache: dict[str, int | None] = {}
        consignment_cache: dict[str, int | None] = {}
        design_number_cache: dict[str, int | None] = {}

        counter_row = await iterator_repo.get_one_or_none(
            IteratorNumberLast.description == ACTIVE_NUMBER_COUNTER_DESCRIPTION
        )
        if counter_row is None or counter_row.number is None:
            errors.append({
                "row": 0, "field": "*",
                "message": f"Не найден счётчик '{ACTIVE_NUMBER_COUNTER_DESCRIPTION}' в iterator_number_last",
            })
            return errors, valid_rows, materials_plan, storage_last_lcn, 0
        start_active_number = counter_row.number + 1

        if progress is not None:
            progress.update(processed=0, total=len(rows), phase="validating")

        for idx, row in enumerate(rows):
            row_num = idx + 1
            if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
                progress["processed"] = row_num

            design_number_raw = str(row.get("Номер ТМЦ (DU,KP,A2V)") or "").strip()
            if not design_number_raw or design_number_raw == "None":
                continue

            count_raw = row.get("Количество")
            try:
                count = int(count_raw)
            except (TypeError, ValueError):
                errors.append({"row": row_num, "field": "Количество", "message": f"Некорректное количество: '{count_raw}'"})
                continue
            if count < 1:
                errors.append({"row": row_num, "field": "Количество", "message": f"Количество должно быть больше 0: '{count_raw}'"})
                continue

            storage_name = str(row.get("Склад") or "").strip()
            if not storage_name:
                errors.append({"row": row_num, "field": "Склад", "message": "Поле 'Склад' пустое"})
                continue
            if storage_name not in storage_cache:
                storage_cache[storage_name] = await storage_repo.get_one_or_none(Storage.name == storage_name)
            storage = storage_cache[storage_name]
            if storage is None:
                errors.append({"row": row_num, "field": "Склад", "message": f"Склад не найден: '{storage_name}'"})
                continue
            if storage.id not in storage_last_lcn:
                storage_last_lcn[storage.id] = storage.last_lcn or 0

            type_active = str(row.get("Тип актива") or "").strip()
            if not type_active:
                errors.append({"row": row_num, "field": "Тип актива", "message": "Поле 'Тип актива' пустое"})
                continue

            special_raw = row.get("Особый учет")
            special = str(special_raw).strip() if special_raw not in (None, "") else None

            storage_place_name = str(row.get("Ячейка") or "").strip()
            id_storage_place: int | None = None
            if storage_place_name and storage_place_name != "None":
                if storage_place_name not in storage_place_cache:
                    sp = await storage_place_repo.get_one_or_none(StoragePlace.name == storage_place_name)
                    storage_place_cache[storage_place_name] = sp.id if sp else None
                id_storage_place = storage_place_cache[storage_place_name]
                if id_storage_place is None:
                    errors.append({"row": row_num, "field": "Ячейка", "message": f"Ячейка не найдена: '{storage_place_name}'"})
                    continue

            consignment_name = str(row.get("Партия") or "").strip()
            if not consignment_name:
                errors.append({"row": row_num, "field": "Партия", "message": "Поле 'Партия' пустое"})
                continue
            if consignment_name not in consignment_cache:
                c = await consignment_repo.get_one_or_none(Consignment.name == consignment_name)
                consignment_cache[consignment_name] = c.id if c else None
            id_consignment = consignment_cache[consignment_name]
            if id_consignment is None:
                errors.append({"row": row_num, "field": "Партия", "message": f"Партия не найдена: '{consignment_name}'"})
                continue

            if design_number_raw not in design_number_cache:
                dn = await design_number_repo.get_one_or_none(DesignNumber.number == design_number_raw)
                design_number_cache[design_number_raw] = dn.id if dn else None
            id_design_number = design_number_cache[design_number_raw]
            if id_design_number is None:
                errors.append({"row": row_num, "field": "Номер ТМЦ (DU,KP,A2V)", "message": f"ТМЦ не найдена: '{design_number_raw}'"})
                continue

            serial_number = (str(row.get("Серийный номер") or "").strip() if count == 1 else "none")

            materials_key = (id_design_number, storage.id)
            if materials_key not in materials_plan:
                existing_result = await db_session.execute(
                    select(Materials.id)
                    .outerjoin(Location, Materials.id_location == Location.id)
                    .where(Materials.id_design_number == id_design_number, Location.id_storage == storage.id)
                )
                existing_ids = existing_result.scalars().all()
                if len(existing_ids) > 1:
                    errors.append({
                        "row": row_num, "field": "*",
                        "message": (f"Найдено {len(existing_ids)} записей materials для ТМЦ+склад "
                                    f"(design_number={id_design_number}, storage={storage.id}) — требуется ручная проверка"),
                    })
                    continue
                materials_plan[materials_key] = existing_ids[0] if existing_ids else None

            for _ in range(count):
                valid_rows.append({
                    "row_num": row_num,
                    "id_design_number": id_design_number,
                    "id_storage": storage.id,
                    "id_storage_place": id_storage_place,
                    "id_consignment": id_consignment,
                    "type_active": type_active,
                    "special_account": special,
                    "serial_number": serial_number or None,
                    "materials_key": materials_key,
                })

        return errors, valid_rows, materials_plan, storage_last_lcn, start_active_number

    @staticmethod
    def _build_create_actives_sql_body(
        valid_rows: list[dict],
        materials_plan: dict[tuple[int, int], int | None],
        storage_last_lcn: dict[int, int],
        start_active_number: int,
    ) -> tuple[list[str], int]:
        """Строит тело SQL-скрипта создания активов из ТМЦ (без BEGIN/COMMIT).

        Общий для «Скачать SQL-файл» и «Выполнить в базе данных» — id для location/actives/
        materials резервируются через nextval() внутри самого скрипта (как в train_parser),
        а не вычисляются на Python-стороне — иначе скачанный, но так и не запущенный файл
        оставлял бы дыры в последовательностях.
        """
        total_actives = len(valid_rows)
        new_materials_keys = [key for key, existing_id in materials_plan.items() if existing_id is None]

        sql_lines: list[str] = ["DO $$", "DECLARE"]
        sql_lines.append(
            f"    loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') "
            f"FROM generate_series(1, {total_actives}));"
        )
        sql_lines.append(
            f"    act_ids bigint[] := ARRAY(SELECT nextval('public.actives_id_seq') "
            f"FROM generate_series(1, {total_actives}));"
        )
        if new_materials_keys:
            sql_lines.append(
                f"    mat_ids bigint[] := ARRAY(SELECT nextval('public.materials_id_seq') "
                f"FROM generate_series(1, {len(new_materials_keys)}));"
            )
        sql_lines.append("BEGIN")

        body_lines: list[str] = []
        materials_key_ref: dict[tuple[int, int], str] = {}
        next_mat_idx = 1
        # last_lcn хранит уже использованные номера, а не "следующий свободный" — старый
        # код на peewee хранил "следующий свободный" и это давало разрыв в 1 на каждый запуск.
        lcn_counters = dict(storage_last_lcn)
        active_number = start_active_number

        for i, vr in enumerate(valid_rows, start=1):
            loc_ref = f"loc_ids[{i}]"
            act_ref = f"act_ids[{i}]"
            sp_val = str(vr["id_storage_place"]) if vr["id_storage_place"] is not None else "NULL"

            body_lines.append(
                f"    INSERT INTO public.location (id, id_type_location, id_storage, id_storage_place, id_consignment) "
                f"VALUES ({loc_ref}, 1, {vr['id_storage']}, {sp_val}, {vr['id_consignment']});"
            )

            key = vr["materials_key"]
            existing_mat_id = materials_plan[key]
            if existing_mat_id is not None:
                mat_ref = str(existing_mat_id)
            elif key in materials_key_ref:
                mat_ref = materials_key_ref[key]
            else:
                mat_ref = f"mat_ids[{next_mat_idx}]"
                next_mat_idx += 1
                materials_key_ref[key] = mat_ref
                body_lines.append(
                    f"    INSERT INTO public.materials (id, id_design_number, id_location) "
                    f"VALUES ({mat_ref}, {vr['id_design_number']}, {loc_ref});"
                )

            lcn_counters[vr["id_storage"]] = lcn_counters.get(vr["id_storage"], 0) + 1
            lcn = f"S{vr['id_storage']}.{lcn_counters[vr['id_storage']]}"

            active_num_str = f"{vr['type_active']}{active_number}"
            active_number += 1

            sn_val = f"'{sql_escape(vr['serial_number'])}'" if vr["serial_number"] else "NULL"
            sa_val = f"'{sql_escape(vr['special_account'])}'" if vr["special_account"] else "NULL"

            body_lines.append(
                f"    INSERT INTO public.actives (id, active_number, id_design_number, id_location, "
                f"serial_number, lcn, id_materials, special_account) "
                f"VALUES ({act_ref}, '{sql_escape(active_num_str)}', {vr['id_design_number']}, {loc_ref}, "
                f"{sn_val}, '{lcn}', {mat_ref}, {sa_val});"
            )

        sql_lines.extend(body_lines)
        sql_lines.append("END $$;")

        for storage_id, last_val in lcn_counters.items():
            sql_lines.append(f"UPDATE public.storage SET last_lcn = {last_val} WHERE id = {storage_id};")

        sql_lines.append(
            f"UPDATE public.iterator_number_last SET number = {active_number - 1} "
            f"WHERE description = '{sql_escape(ACTIVE_NUMBER_COUNTER_DESCRIPTION)}';"
        )

        return sql_lines, active_number - 1

    @post("/create-actives/generate-sql/start")
    async def create_actives_generate_sql_start(self, request: Request) -> Response:
        """Запускает фоновую генерацию SQL-файла создания активов из ТМЦ, возвращает task_id."""
        session_id = request.session.get(f"{PREFIX}_session_id", "")
        stored = _load_data(session_id) if session_id else None
        if not stored:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": "Данные не загружены"}]}),
                status_code=200,
                media_type="application/json",
            )

        rows: list[dict] = stored["rows"]

        _cleanup_progress()
        task_id = uuid.uuid4().hex
        _progress[task_id] = {"processed": 0, "total": len(rows), "phase": "validating",
                               "status": "running", "created_at": time.time()}
        task = asyncio.ensure_future(self._run_create_actives_generate(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task

        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_create_actives_generate(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows, materials_plan, storage_last_lcn, start_active_number = \
                    await self._validate_create_actives_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
            return

        sql_lines, _ = self._build_create_actives_sql_body(valid_rows, materials_plan, storage_last_lcn, start_active_number)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/create-actives/execute/start")
    async def create_actives_execute_start(self, request: Request) -> Response:
        """Запускает фоновую атомарную вставку активов из ТМЦ, возвращает task_id."""
        session_id = request.session.get(f"{PREFIX}_session_id", "")
        stored = _load_data(session_id) if session_id else None
        if not stored:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": "Данные не загружены"}]}),
                status_code=200,
                media_type="application/json",
            )

        rows: list[dict] = stored["rows"]

        _cleanup_progress()
        task_id = uuid.uuid4().hex
        _progress[task_id] = {"processed": 0, "total": len(rows), "phase": "validating",
                               "status": "running", "created_at": time.time()}
        task = asyncio.ensure_future(self._run_create_actives_execute(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task

        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_create_actives_execute(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        valid_rows: list[dict] = []
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows, materials_plan, storage_last_lcn, start_active_number = \
                    await self._validate_create_actives_rows(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
                    return

                progress.update(processed=0, total=1, phase="executing")

                sql_lines, _ = self._build_create_actives_sql_body(valid_rows, materials_plan, storage_last_lcn, start_active_number)
                sql_body = "\n".join(sql_lines)

                try:
                    # ВАЖНО: как и в train_parser — session.rollback() ниже откатывает и этот
                    # «сырой» вызов только потому, что сессия уже открыла транзакцию раньше
                    # (запросы репозиториев внутри _validate_create_actives_rows). Не убирайте
                    # обращения к session до этой точки.
                    conn = await session.connection()
                    raw_conn = await conn.get_raw_connection()
                    await raw_conn.driver_connection.execute(sql_body)

                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
                    return

                progress["processed"] = 1
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
            return

        now = datetime.now()
        log_lines = [
            f"=== Create Actives From TMC: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Actives created: {len(valid_rows)}",
            "",
        ]
        log_file = LOG_DIR / f"create_actives_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Created %d actives from TMC, log saved to %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Успешно создано активов: {len(valid_rows)}")

    @get("/create-actives/progress/{task_id:str}")
    async def create_actives_progress(self, task_id: str) -> Response:
        state = _progress.get(task_id)
        if state is None:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": "Задача не найдена или устарела"}]}),
                status_code=200,
                media_type="application/json",
            )
        return Response(
            content=json.dumps({k: v for k, v in state.items() if k != "created_at"}),
            status_code=200,
            media_type="application/json",
        )
