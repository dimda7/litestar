import asyncio
import json
import logging
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Template, Response, Redirect

from db_manager import get_session_maker
from models import (
    Actives, CounterActive, Consignment, DesignNumber, IteratorNumberLast, Location,
    MileageStart, MileageTrain, Relocate, Storage, StoragePlace,
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
# active_number должен быть ровно такой длины: буквенный префикс (Тип актива) + цифры
# счётчика, дополненные нулями слева между префиксом и числом до общей длины.
ACTIVE_NUMBER_LENGTH = 10

# Дата исторической миграции учёта пробега: перемещения актива до неё влияют на
# пересчитываемый mileage_start.milage, пробег поездов суммируется до неё включительно.
MILEAGE_RECOUNT_CUTOFF = date(2022, 5, 13)
# relocate.date хранится без таймзоны (UTC); для сравнения с датой отсечки
# приводится к московскому времени, как в старом peewee-скрипте.
MILEAGE_TZ_SHIFT = timedelta(hours=3)
# counter_type счётчика пробега в counter_active
MILEAGE_COUNTER_TYPE_ID = 3

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


class ActivesRepository(SQLAlchemyAsyncRepository[Actives]):
    model_type = Actives


class MileageStartRepository(SQLAlchemyAsyncRepository[MileageStart]):
    model_type = MileageStart


class CounterActiveRepository(SQLAlchemyAsyncRepository[CounterActive]):
    model_type = CounterActive


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
        self, db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
    ) -> tuple[list[dict], list[tuple[str, int, str]]]:
        """Validate rows for actives.id_design_number update.
        Returns (errors, valid_rows) where valid_rows is [(active_number, design_number_id, design_number), ...]
        """
        errors: list[dict] = []
        valid_rows: list[tuple[str, int, str]] = []
        batch_numbers: set[str] = set()

        if rows and "Новая Позиция ТМЦ" not in rows[0] and "Новый ТМЦ номер" not in rows[0]:
            errors.append({
                "row": 0,
                "field": "Новая Позиция ТМЦ",
                "message": "В файле не найдена колонка 'Новая Позиция ТМЦ' (или 'Новый ТМЦ номер')",
            })
            return errors, valid_rows

        if progress is not None:
            progress.update(processed=0, total=len(rows), phase="validating")

        for idx, row in enumerate(rows):
            row_num = idx + 1
            if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
                progress["processed"] = row_num
            active_number = str(row.get("Актив", "") or "").strip()
            design_number = str(row.get("Новая Позиция ТМЦ") or row.get("Новый ТМЦ номер") or "").strip()

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

    @post("/design-number/generate-sql/start")
    async def design_number_generate_sql_start(self, request: Request) -> Response:
        """Запускает фоновую генерацию SQL-файла обновления id_design_number, возвращает task_id."""
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
        task = asyncio.ensure_future(self._run_design_number_generate(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task

        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_design_number_generate(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await self._validate_design_number(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}])
            return

        sql_lines = [
            f"UPDATE public.actives SET id_design_number = {design_number_id} "
            f"WHERE active_number = '{sql_escape(active_number)}';"
            for active_number, design_number_id, _ in valid_rows
        ]
        progress.update(status="done", sql="\n".join(sql_lines), count=len(sql_lines))

    @post("/design-number/execute/start")
    async def design_number_execute_start(self, request: Request) -> Response:
        """Запускает фоновое обновление id_design_number в БД, возвращает task_id."""
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
        task = asyncio.ensure_future(self._run_design_number_execute(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task

        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_design_number_execute(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        valid_rows: list[tuple[str, int, str]] = []
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await self._validate_design_number(session, rows, progress=progress)

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

        progress.update(status="done", count=len(valid_rows),
                         message=f"Успешно обновлено id_design_number для {len(valid_rows)} записей")

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

    async def _validate_recount_mileage(
        self, db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Валидирует строки файла «пересчитать пробег» (замена update_milage_start + recount_counter).

        Для каждого актива по истории relocate считает поправку total к mileage_start.milage:
        перемещения до MILEAGE_RECOUNT_CUTOFF со склада на поезд прибавляют пробег этого
        поезда (mileage_train.mileage_average за период от перемещения до отсечки), с поезда
        на склад — вычитают. Сами значения milage_const и counter_active.value здесь не
        вычисляются — они читаются/считаются в БД в момент выполнения SQL
        (см. _build_recount_mileage_sql_body).

        Необязательная колонка 'milage_const' (замена update_milage_start_const): непустое
        ненулевое значение перед пересчётом записывается в mileage_start.milage_const;
        для актива без записи mileage_start она создаётся (INSERT) — тогда отсутствие
        записи не считается ошибкой. Значение 0 игнорируется, как в старом скрипте.
        Заголовок колонки сопоставляется без учёта регистра и краевых пробелов (в реальных
        файлах встречается 'mileage_const\\xa0'), допустимы оба написания —
        milage_const и mileage_const.
        """
        actives_repo = ActivesRepository(session=db_session)
        mileage_start_repo = MileageStartRepository(session=db_session)
        counter_repo = CounterActiveRepository(session=db_session)

        errors: list[dict] = []
        valid_rows: list[dict] = []
        batch_numbers: set[str] = set()

        if rows and "Актив" not in rows[0]:
            errors.append({"row": 0, "field": "Актив", "message": "В файле не найдена колонка 'Актив'"})
            return errors, valid_rows
        const_column: str | None = next(
            (k for k in (rows[0] if rows else {})
             if str(k).strip().lower() in ("milage_const", "mileage_const")),
            None,
        )

        if progress is not None:
            progress.update(processed=0, total=len(rows), phase="validating")

        loc_old = aliased(Location)
        loc_new = aliased(Location)

        for idx, row in enumerate(rows):
            row_num = idx + 1
            if progress is not None:
                progress["processed"] = row_num

            active_number = str(row.get("Актив", "") or "").strip()
            if not active_number:
                errors.append({"row": row_num, "field": "Актив", "message": "Поле 'Актив' пустое"})
                continue

            if active_number in batch_numbers:
                errors.append({"row": row_num, "field": "Актив",
                                "message": f"Дубликат внутри файла: '{active_number}'"})
                continue

            active = await actives_repo.get_one_or_none(Actives.active_number == active_number)
            if active is None:
                errors.append({"row": row_num, "field": "Актив",
                                "message": f"Актив не найден: '{active_number}'"})
                continue
            is_train = active.id_unit_type == 1

            milage_const: int | None = None
            if const_column is not None:
                const_raw = row.get(const_column)
                if const_raw is not None and str(const_raw).strip() != "":
                    try:
                        milage_const = int(float(const_raw))
                    except (TypeError, ValueError):
                        errors.append({"row": row_num, "field": "milage_const",
                                        "message": f"Некорректное значение milage_const: '{const_raw}'"})
                        continue
                    if milage_const == 0:
                        milage_const = None

            mileage_start_rows = await mileage_start_repo.get_many(MileageStart.id_active == active.id)
            if len(mileage_start_rows) > 1:
                errors.append({"row": row_num, "field": "Актив",
                                "message": f"Несколько записей mileage_start для актива '{active_number}' — неоднозначно"})
                continue
            # Без milage_const запись mileage_start обязана существовать; с ним она
            # будет создана в SQL (INSERT), поэтому отсутствие — не ошибка.
            if not mileage_start_rows and milage_const is None:
                errors.append({"row": row_num, "field": "Актив",
                                "message": f"Запись mileage_start не найдена для актива '{active_number}'"})
                continue

            if not is_train:
                counters = await counter_repo.get_many(
                    CounterActive.id_active == active.id,
                    CounterActive.id_counter_type == MILEAGE_COUNTER_TYPE_ID,
                )
                if not counters:
                    errors.append({"row": row_num, "field": "Актив",
                                    "message": f"Счётчик пробега (counter_type={MILEAGE_COUNTER_TYPE_ID}) не найден для актива '{active_number}'"})
                    continue
                if len(counters) > 1:
                    errors.append({"row": row_num, "field": "Актив",
                                    "message": f"Несколько счётчиков пробега для актива '{active_number}' — неоднозначно"})
                    continue

            relocates = (await db_session.execute(
                select(
                    Relocate.date,
                    loc_old.id_train.label("id_train_old"),
                    loc_new.id_train.label("id_train_new"),
                )
                .join(loc_old, Relocate.id_location_old == loc_old.id, isouter=True)
                .join(loc_new, Relocate.id_location_new == loc_new.id, isouter=True)
                .where(Relocate.id_active == active.id)
                .order_by(Relocate.date)
            )).all()

            total = 0
            for relocate_date, id_train_old, id_train_new in relocates:
                if relocate_date is None:
                    continue
                if (relocate_date + MILEAGE_TZ_SHIFT).date() >= MILEAGE_RECOUNT_CUTOFF:
                    continue
                # Учитываются только перемещения склад<->поезд; поезд->поезд и
                # склад->склад пробег не меняют (как в старом скрипте).
                if id_train_old is not None and id_train_new is None:
                    train_id, sign = id_train_old, -1
                elif id_train_old is None and id_train_new is not None:
                    train_id, sign = id_train_new, 1
                else:
                    continue

                train_mileage = (await db_session.execute(
                    select(func.sum(MileageTrain.mileage_average)).where(
                        MileageTrain.id_train == train_id,
                        MileageTrain.date_average > relocate_date,
                        MileageTrain.date_average <= MILEAGE_RECOUNT_CUTOFF,
                    )
                )).scalar()
                total += sign * (train_mileage or 0)

            batch_numbers.add(active_number)
            valid_rows.append({
                "row_num": row_num,
                "active_number": active_number,
                "id_active": active.id,
                "total": total,
                "is_train": is_train,
                "milage_const": milage_const,
                "insert_mileage_start": not mileage_start_rows,
            })

        return errors, valid_rows

    @staticmethod
    def _build_recount_mileage_sql_body(valid_rows: list[dict]) -> list[str]:
        """Строит SQL пересчёта пробега (без BEGIN/COMMIT), общий для скачивания и выполнения.

        Порядок обязателен: сначала запись milage_const из файла (UPDATE, либо INSERT
        для активов без mileage_start), затем UPDATE mileage_start.milage — он читает
        milage_const в момент выполнения и должен видеть уже новое значение, затем
        UPDATE counter_active: пересчёт счётчика через function_get_mileage() читает
        mileage_start.milage. Всё в одной транзакции, поэтому скачанный и выполненный
        позже файл не разойдётся с БД. Для поездов (id_unit_type=1) счётчик не
        пересчитывается (как в старом скрипте).
        """
        sql_lines: list[str] = []
        for vr in valid_rows:
            if vr["milage_const"] is None:
                continue
            if vr["insert_mileage_start"]:
                sql_lines.append(
                    f"INSERT INTO public.mileage_start (id_active, milage, milage_const, is_recount) "
                    f"VALUES ({vr['id_active']}, {vr['milage_const']}, {vr['milage_const']}, true); "
                    f"-- '{sql_escape(vr['active_number'])}'"
                )
            else:
                sql_lines.append(
                    f"UPDATE public.mileage_start SET milage_const = {vr['milage_const']}, is_recount = true "
                    f"WHERE id_active = {vr['id_active']}; -- '{sql_escape(vr['active_number'])}'"
                )
        for vr in valid_rows:
            sql_lines.append(
                f"UPDATE public.mileage_start SET milage = COALESCE(milage_const, 0) + ({vr['total']}), "
                f"is_recount = true WHERE id_active = {vr['id_active']}; -- '{sql_escape(vr['active_number'])}'"
            )
        for vr in valid_rows:
            if vr["is_train"]:
                continue
            sql_lines.append(
                f"UPDATE public.counter_active SET value = COALESCE("
                f"(SELECT sum FROM public.function_get_mileage(id_active, date::date)), 0) "
                f"WHERE id_active = {vr['id_active']} AND id_counter_type = {MILEAGE_COUNTER_TYPE_ID}; "
                f"-- '{sql_escape(vr['active_number'])}'"
            )
        return sql_lines

    @post("/recount-mileage/generate-sql/start")
    async def recount_mileage_generate_sql_start(self, request: Request) -> Response:
        """Запускает фоновую генерацию SQL-файла пересчёта пробега, возвращает task_id."""
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
        task = asyncio.ensure_future(self._run_recount_mileage_generate(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task

        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_recount_mileage_generate(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await self._validate_recount_mileage(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для пересчёта"}])
            return

        sql_lines = self._build_recount_mileage_sql_body(valid_rows)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/recount-mileage/execute/start")
    async def recount_mileage_execute_start(self, request: Request) -> Response:
        """Запускает фоновый атомарный пересчёт пробега в БД, возвращает task_id."""
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
        task = asyncio.ensure_future(self._run_recount_mileage_execute(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task

        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_recount_mileage_execute(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        valid_rows: list[dict] = []
        sql_lines: list[str] = []
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await self._validate_recount_mileage(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для пересчёта"}])
                    return

                progress.update(processed=0, total=1, phase="executing")

                sql_lines = self._build_recount_mileage_sql_body(valid_rows)
                sql_body = "\n".join(sql_lines)

                try:
                    # ВАЖНО: как и в create-actives — session.rollback() ниже откатывает и этот
                    # «сырой» вызов только потому, что сессия уже открыла транзакцию раньше
                    # (запросы репозиториев внутри _validate_recount_mileage). Не убирайте
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
            f"=== Recount Mileage: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Actives recounted: {len(valid_rows)}",
            "",
            *sql_lines,
            "",
        ]
        log_file = LOG_DIR / f"recount_mileage_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Recounted mileage for %d actives, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Пробег пересчитан для {len(valid_rows)} активов")

    async def _validate_create_actives_rows(
        self, db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Валидирует строки ТМЦ и строит план создания активов (замена add_active_spcial).

        valid_rows — один элемент на каждый создаваемый актив ('Количество' раскрыто построчно),
        каждому активу соответствует своя собственная запись location.

        Текущие значения storage.last_lcn и iterator_number_last.number здесь намеренно не
        читаются для использования в SQL — только для проверки, что счётчик существует.
        Сами значения читаются и блокируются (FOR UPDATE) внутри DO-блока в момент его
        выполнения (см. _build_create_actives_sql_body), а не снимаются снапшотом на
        Python-стороне при валидации — иначе к моменту реального запуска (особенно для
        скачанного и выполненного позже файла) значение в БД могло уже уйти вперёд.
        """
        storage_repo = StorageRepository(session=db_session)
        storage_place_repo = StoragePlaceRepository(session=db_session)
        consignment_repo = ConsignmentRepository(session=db_session)
        design_number_repo = DesignNumberRepository(session=db_session)
        iterator_repo = IteratorNumberLastRepository(session=db_session)

        errors: list[dict] = []
        valid_rows: list[dict] = []

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
            return errors, valid_rows

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
            type_active = str(row.get("Тип актива") or "").strip()
            if not type_active:
                errors.append({"row": row_num, "field": "Тип актива", "message": "Поле 'Тип актива' пустое"})
                continue
            if len(type_active) >= ACTIVE_NUMBER_LENGTH:
                errors.append({
                    "row": row_num, "field": "Тип актива",
                    "message": (f"'{type_active}' слишком длинный для {ACTIVE_NUMBER_LENGTH}-значного "
                                f"номера актива (префикс + цифры)"),
                })
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
                })

        return errors, valid_rows

    @staticmethod
    def _build_create_actives_sql_body(valid_rows: list[dict]) -> list[str]:
        """Строит тело SQL-скрипта создания активов из ТМЦ (без BEGIN/COMMIT).

        Каждому активу создаётся своя собственная запись location (materials не используется).
        Общий для «Скачать SQL-файл» и «Выполнить в базе данных». Все счётчики читаются и
        расходуются внутри самого DO-блока в момент его выполнения, а не на Python-стороне
        при генерации:
        - id для location/actives — через nextval() (как в train_parser);
        - storage.last_lcn и iterator_number_last.number — через
          `SELECT ... INTO var ... FOR UPDATE`, локальный инкремент переменной и `UPDATE`
          этой же переменной в БД в конце того же блока (FOR UPDATE держит блокировку строки
          до конца транзакции, что защищает от гонки при параллельном выполнении).
        Без этого скачанный, но выполненный позже файл (или два запуска подряд) мог бы разъехаться
        со значением в БД: id из nextval() всегда уникален сам по себе, а last_lcn/number —
        обычные integer-колонки, и старый код на peewee вычислял их один раз на Python-стороне.
        """
        total_actives = len(valid_rows)
        storage_ids = sorted({vr["id_storage"] for vr in valid_rows})

        sql_lines: list[str] = ["DO $$", "DECLARE"]
        sql_lines.append(
            f"    loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') "
            f"FROM generate_series(1, {total_actives}));"
        )
        sql_lines.append(
            f"    act_ids bigint[] := ARRAY(SELECT nextval('public.actives_id_seq') "
            f"FROM generate_series(1, {total_actives}));"
        )
        sql_lines.append("    active_num bigint;")
        for storage_id in storage_ids:
            sql_lines.append(f"    lcn_{storage_id} bigint;")

        sql_lines.append("BEGIN")
        sql_lines.append(
            f"    SELECT number INTO active_num FROM public.iterator_number_last "
            f"WHERE description = '{sql_escape(ACTIVE_NUMBER_COUNTER_DESCRIPTION)}' FOR UPDATE;"
        )
        for storage_id in storage_ids:
            sql_lines.append(
                f"    SELECT last_lcn INTO lcn_{storage_id} FROM public.storage WHERE id = {storage_id} FOR UPDATE;"
            )

        body_lines: list[str] = []

        for i, vr in enumerate(valid_rows, start=1):
            loc_ref = f"loc_ids[{i}]"
            act_ref = f"act_ids[{i}]"
            sp_val = str(vr["id_storage_place"]) if vr["id_storage_place"] is not None else "NULL"

            # Инкременты не трогают строку location, но переставлены перед обоими INSERT,
            # чтобы location и actives всегда шли соседними строками одной парой.
            lcn_var = f"lcn_{vr['id_storage']}"
            body_lines.append(f"    {lcn_var} := {lcn_var} + 1;")
            body_lines.append("    active_num := active_num + 1;")

            body_lines.append(
                f"    INSERT INTO public.location (id, id_type_location, id_storage, id_storage_place, id_consignment) "
                f"VALUES ({loc_ref}, 1, {vr['id_storage']}, {sp_val}, {vr['id_consignment']});"
            )

            sn_val = f"'{sql_escape(vr['serial_number'])}'" if vr["serial_number"] else "NULL"
            sa_val = f"'{sql_escape(vr['special_account'])}'" if vr["special_account"] else "NULL"
            # Номер актива фиксированной длины ACTIVE_NUMBER_LENGTH: буквенный префикс +
            # цифры счётчика, дополненные нулями слева до нужной ширины (валидация выше
            # гарантирует len(type_active) < ACTIVE_NUMBER_LENGTH, так что ширина > 0).
            number_width = ACTIVE_NUMBER_LENGTH - len(vr["type_active"])
            active_number_expr = (
                f"'{sql_escape(vr['type_active'])}' || lpad(active_num::text, {number_width}, '0')"
            )
            lcn_expr = f"('S{vr['id_storage']}.' || {lcn_var})::ltree"

            body_lines.append(
                f"    INSERT INTO public.actives (id, active_number, id_design_number, id_location, "
                f"serial_number, lcn, special_account) "
                f"VALUES ({act_ref}, {active_number_expr}, {vr['id_design_number']}, {loc_ref}, "
                f"{sn_val}, {lcn_expr}, {sa_val});"
            )

        sql_lines.extend(body_lines)

        for storage_id in storage_ids:
            sql_lines.append(f"    UPDATE public.storage SET last_lcn = lcn_{storage_id} WHERE id = {storage_id};")
        sql_lines.append(
            f"    UPDATE public.iterator_number_last SET number = active_num "
            f"WHERE description = '{sql_escape(ACTIVE_NUMBER_COUNTER_DESCRIPTION)}';"
        )

        sql_lines.append("END $$;")

        return sql_lines

    async def _validate_create_named_actives(
        self, db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Валидирует строки создания именных активов (номер актива задан в файле).

        В отличие от создания активов из ТМЦ здесь не нужен счётчик
        iterator_number_last: active_number берётся из колонки 'Актив' и не должен
        существовать в БД. Каждая строка — один актив со своей записью location;
        lcn по-прежнему выдаётся из storage.last_lcn в момент выполнения SQL.
        """
        actives_repo = ActivesRepository(session=db_session)
        storage_repo = StorageRepository(session=db_session)
        consignment_repo = ConsignmentRepository(session=db_session)
        design_number_repo = DesignNumberRepository(session=db_session)

        errors: list[dict] = []
        valid_rows: list[dict] = []
        batch_numbers: set[str] = set()

        required_columns = ("Актив", "ТМЦ", "Положение", "Партия")
        if rows:
            missing = [c for c in required_columns if c not in rows[0]]
            if missing:
                errors.append({
                    "row": 0, "field": ", ".join(missing),
                    "message": f"В файле не найдены колонки: {', '.join(missing)}",
                })
                return errors, valid_rows

        storage_cache: dict[str, int | None] = {}
        consignment_cache: dict[str, int | None] = {}
        design_number_cache: dict[str, int | None] = {}

        if progress is not None:
            progress.update(processed=0, total=len(rows), phase="validating")

        for idx, row in enumerate(rows):
            row_num = idx + 1
            if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
                progress["processed"] = row_num

            active_number = str(row.get("Актив") or "").strip()
            if not active_number:
                errors.append({"row": row_num, "field": "Актив", "message": "Поле 'Актив' пустое"})
                continue

            if active_number in batch_numbers:
                errors.append({"row": row_num, "field": "Актив",
                                "message": f"Дубликат внутри файла: '{active_number}'"})
                continue

            existing = await actives_repo.get_one_or_none(Actives.active_number == active_number)
            if existing is not None:
                errors.append({"row": row_num, "field": "Актив",
                                "message": f"Актив уже существует: '{active_number}'"})
                continue

            design_number = str(row.get("ТМЦ") or "").strip()
            if not design_number:
                errors.append({"row": row_num, "field": "ТМЦ", "message": "Поле 'ТМЦ' пустое"})
                continue
            if design_number not in design_number_cache:
                dn = await design_number_repo.get_one_or_none(DesignNumber.number == design_number)
                design_number_cache[design_number] = dn.id if dn else None
            id_design_number = design_number_cache[design_number]
            if id_design_number is None:
                errors.append({"row": row_num, "field": "ТМЦ", "message": f"ТМЦ не найдена: '{design_number}'"})
                continue

            storage_name = str(row.get("Положение") or "").strip()
            if not storage_name:
                errors.append({"row": row_num, "field": "Положение", "message": "Поле 'Положение' пустое"})
                continue
            if storage_name not in storage_cache:
                storage = await storage_repo.get_one_or_none(Storage.name == storage_name)
                storage_cache[storage_name] = storage.id if storage else None
            id_storage = storage_cache[storage_name]
            if id_storage is None:
                errors.append({"row": row_num, "field": "Положение", "message": f"Склад не найден: '{storage_name}'"})
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

            batch_numbers.add(active_number)
            valid_rows.append({
                "row_num": row_num,
                "active_number": active_number,
                "id_design_number": id_design_number,
                "id_storage": id_storage,
                "id_consignment": id_consignment,
            })

        return errors, valid_rows

    @staticmethod
    def _build_create_named_actives_sql_body(valid_rows: list[dict]) -> list[str]:
        """Строит тело SQL создания именных активов (без BEGIN/COMMIT).

        Как в _build_create_actives_sql_body: id для location/actives — через nextval(),
        storage.last_lcn читается и блокируется FOR UPDATE внутри DO-блока в момент
        выполнения. Отличие — active_number подставляется литералом из файла, счётчик
        iterator_number_last не используется.
        """
        total_actives = len(valid_rows)
        storage_ids = sorted({vr["id_storage"] for vr in valid_rows})

        sql_lines: list[str] = ["DO $$", "DECLARE"]
        sql_lines.append(
            f"    loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') "
            f"FROM generate_series(1, {total_actives}));"
        )
        sql_lines.append(
            f"    act_ids bigint[] := ARRAY(SELECT nextval('public.actives_id_seq') "
            f"FROM generate_series(1, {total_actives}));"
        )
        for storage_id in storage_ids:
            sql_lines.append(f"    lcn_{storage_id} bigint;")

        sql_lines.append("BEGIN")
        for storage_id in storage_ids:
            sql_lines.append(
                f"    SELECT last_lcn INTO lcn_{storage_id} FROM public.storage WHERE id = {storage_id} FOR UPDATE;"
            )

        for i, vr in enumerate(valid_rows, start=1):
            loc_ref = f"loc_ids[{i}]"
            act_ref = f"act_ids[{i}]"
            lcn_var = f"lcn_{vr['id_storage']}"
            sql_lines.append(f"    {lcn_var} := {lcn_var} + 1;")
            sql_lines.append(
                f"    INSERT INTO public.location (id, id_type_location, id_storage, id_consignment) "
                f"VALUES ({loc_ref}, 1, {vr['id_storage']}, {vr['id_consignment']});"
            )
            sql_lines.append(
                f"    INSERT INTO public.actives (id, active_number, id_design_number, id_location, lcn) "
                f"VALUES ({act_ref}, '{sql_escape(vr['active_number'])}', {vr['id_design_number']}, {loc_ref}, "
                f"('S{vr['id_storage']}.' || {lcn_var})::ltree);"
            )

        for storage_id in storage_ids:
            sql_lines.append(f"    UPDATE public.storage SET last_lcn = lcn_{storage_id} WHERE id = {storage_id};")

        sql_lines.append("END $$;")

        return sql_lines

    @post("/create-named-actives/generate-sql/start")
    async def create_named_actives_generate_sql_start(self, request: Request) -> Response:
        """Запускает фоновую генерацию SQL-файла создания именных активов, возвращает task_id."""
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
        task = asyncio.ensure_future(self._run_create_named_actives_generate(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task

        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_create_named_actives_generate(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await self._validate_create_named_actives(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
            return

        sql_lines = self._build_create_named_actives_sql_body(valid_rows)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/create-named-actives/execute/start")
    async def create_named_actives_execute_start(self, request: Request) -> Response:
        """Запускает фоновое атомарное создание именных активов, возвращает task_id."""
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
        task = asyncio.ensure_future(self._run_create_named_actives_execute(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task

        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_create_named_actives_execute(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        valid_rows: list[dict] = []
        sql_lines: list[str] = []
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await self._validate_create_named_actives(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
                    return

                progress.update(processed=0, total=1, phase="executing")

                sql_lines = self._build_create_named_actives_sql_body(valid_rows)
                sql_body = "\n".join(sql_lines)

                try:
                    # ВАЖНО: как и в create-actives — session.rollback() ниже откатывает и этот
                    # «сырой» вызов только потому, что сессия уже открыла транзакцию раньше
                    # (запросы репозиториев внутри _validate_create_named_actives). Не убирайте
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
            f"=== Create Named Actives: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Actives created: {len(valid_rows)}",
            "",
            *sql_lines,
            "",
        ]
        log_file = LOG_DIR / f"create_named_actives_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Created %d named actives, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Успешно создано именных активов: {len(valid_rows)}")

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
                errors, valid_rows = \
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

        sql_lines = self._build_create_actives_sql_body(valid_rows)
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
                errors, valid_rows = \
                    await self._validate_create_actives_rows(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
                    return

                progress.update(processed=0, total=1, phase="executing")

                sql_lines = self._build_create_actives_sql_body(valid_rows)
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

    @get("/progress/{task_id:str}")
    async def task_progress(self, task_id: str) -> Response:
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
