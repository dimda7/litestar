import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from dateutil import parser as date_parser
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Template, Response, Redirect

from db_manager import get_session_maker
from models import Actives, CounterActive, CounterType, Ptoir, PtoirLevelWarning
from schemas import SelectSheetRequest
import excel_upload
from parser_storage import LOG_DIR
from progress_tasks import progress_response, start_task
from sql_builders import ptoir as ptoir_sql

logger = logging.getLogger("ptoir_parser")

PREFIX = "ptoir_parser"

# Excel dates are entered in Moscow time while date_activation/zero_point_value
# are stored in UTC (see the old update_ptoir function) — hence the 3 hour shift
# everywhere.
MSK_OFFSET = timedelta(hours=3)

HOURS_MULTIPLIER = 10


def _find_column(rows: list[dict], name: str) -> str | None:
    return next((k for k in (rows[0] if rows else {}) if str(k).strip().lower() == name), None)


def _parse_hours(raw: object) -> int | None:
    """'18635,2' -> 186352; None unless the value is a non-negative number with at most one decimal."""
    if isinstance(raw, bool):
        return None
    try:
        scaled = Decimal(str(raw).strip().replace(",", ".")) * HOURS_MULTIPLIER
    except InvalidOperation:
        return None
    if not scaled.is_finite() or scaled < 0 or scaled != scaled.to_integral_value():
        return None
    return int(scaled)


async def validate_change_hours_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'изменение моточасов'.

    'птоир' is ptoir.number_ptoir; 'значение' times HOURS_MULTIPLIER becomes
    counter_active.value of the ПТОиР's asset. The optional 'актив' column is
    only checked against that asset. valid_rows holds one entry per asset and
    carries the old value/value_source for the execution log.
    """
    errors: list[dict] = []
    valid_rows: list[dict] = []

    ptoir_column = _find_column(rows, "птоир")
    value_column = _find_column(rows, "значение")
    active_column = _find_column(rows, "актив")
    if rows and ptoir_column is None:
        errors.append({"row": 0, "field": "птоир", "message": "В файле не найдена колонка 'птоир'"})
        return errors, valid_rows
    if rows and value_column is None:
        errors.append({"row": 0, "field": "значение", "message": "В файле не найдена колонка 'значение'"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    batch_ptoirs: dict[str, int] = {}
    batch_actives: dict[int, tuple[int, str]] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        number_ptoir = str(row.get(ptoir_column, "") or "").strip()
        value_raw = row.get(value_column)
        active_raw = str(row.get(active_column, "") or "").strip() if active_column else ""

        if not number_ptoir:
            errors.append({"row": row_num, "field": "птоир", "message": "Поле 'птоир' пустое"})
            continue

        if value_raw is None or str(value_raw).strip() == "":
            errors.append({"row": row_num, "field": "значение", "message": "Поле 'значение' пустое"})
            continue
        value = _parse_hours(value_raw)
        if value is None:
            errors.append({"row": row_num, "field": "значение",
                           "message": (f"Некорректное значение: '{value_raw}' (ожидается неотрицательное "
                                       f"число, не более одного знака после запятой)")})
            continue

        if number_ptoir in batch_ptoirs:
            if batch_ptoirs[number_ptoir] != value:
                errors.append({"row": row_num, "field": "птоир",
                               "message": (f"Конфликт: ПТОиР '{number_ptoir}' уже сопоставлен другому значению "
                                           f"({batch_ptoirs[number_ptoir]}, а не {value})")})
            continue

        result = await db_session.execute(
            select(Ptoir.id_active, Actives.active_number)
            .outerjoin(Actives, Actives.id == Ptoir.id_active)
            .where(Ptoir.number_ptoir == number_ptoir)
        )
        ptoir_row = result.first()
        if ptoir_row is None:
            errors.append({"row": row_num, "field": "птоир", "message": f"ПТОиР не найден: '{number_ptoir}'"})
            continue
        id_active, active_number = ptoir_row
        if id_active is None:
            errors.append({"row": row_num, "field": "птоир", "message": f"У ПТОиР '{number_ptoir}' нет актива"})
            continue

        if active_raw and active_raw != active_number:
            errors.append({"row": row_num, "field": "актив",
                           "message": (f"Актив '{active_raw}' не соответствует активу ПТОиР "
                                       f"'{number_ptoir}' ('{active_number}')")})
            continue

        if id_active in batch_actives:
            other_value, other_ptoir = batch_actives[id_active]
            if other_value != value:
                errors.append({"row": row_num, "field": "значение",
                               "message": (f"Конфликт: актив '{active_number}' уже получил другое значение "
                                           f"от ПТОиР '{other_ptoir}' ({other_value}, а не {value})")})
                continue
            batch_ptoirs[number_ptoir] = value
            continue

        result = await db_session.execute(
            select(CounterActive.value, CounterActive.value_source).where(
                (CounterActive.id_active == id_active)
                & (CounterActive.id_counter_type == ptoir_sql.HOURS_COUNTER_TYPE_ID)
            )
        )
        counter_row = result.first()
        if counter_row is None:
            errors.append({"row": row_num, "field": "птоир",
                           "message": f"У актива '{active_number}' (ПТОиР '{number_ptoir}') нет счётчика моточасов"})
            continue

        batch_ptoirs[number_ptoir] = value
        batch_actives[id_active] = (value, number_ptoir)
        valid_rows.append({"id_active": id_active, "value": value, "number_ptoir": number_ptoir,
                           "active_number": active_number, "old_value": counter_row[0],
                           "old_value_source": counter_row[1]})

    return errors, valid_rows


class PtoirParserController(Controller):
    path = "/ptoir-parser"

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
            template_name="ptoir_parser.html",
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
                "active_page": "ptoir_parser",
                "pending_sheets": pending_sheets,
                "pending_filename": pending_filename,
            },
        )

    @post("/upload")
    async def upload(self, request: Request) -> Redirect:
        """Upload of an Excel file (.xlsx/.xls) — see excel_upload.handle_upload."""
        return await excel_upload.handle_upload(request, PREFIX, "/ptoir-parser", skip_blank_rows=True)

    @post("/select-sheet")
    async def select_sheet(
        self,
        request: Request,
        data: SelectSheetRequest = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        """Sheet choice for a multi-sheet Excel file — see excel_upload.handle_sheet_choice."""
        return excel_upload.handle_sheet_choice(request, PREFIX, "/ptoir-parser", data.sheet_name, skip_blank_rows=True)

    async def _validate_and_build_rows(
        self, db_session: AsyncSession, rows: list[dict],
        progress: dict | None = None,
    ) -> tuple[list[dict], list[tuple[int, datetime, int, int, int]]]:
        """Validate the Excel rows for a maintenance (ПТОиР) run.

        Returns (errors, valid_rows), where valid_rows is a list of tuples
        (id_ptoir, date_activation, interval, id_level_warning, zero_point_value).
        When a progress dict is passed, (processed, total, phase="validating")
        is written into it every few rows for the frontend to poll.
        """
        errors: list[dict] = []
        valid_rows: list[tuple[int, datetime, int, int, int]] = []

        if progress is not None:
            progress.update(processed=0, total=len(rows), phase="validating")

        for idx, row in enumerate(rows):
            row_num = idx + 1
            if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
                progress["processed"] = row_num
            number_ptoir = str(row.get("ПТОиР", "") or "").strip()
            active_number = str(row.get("Актив", "") or "").strip()
            type_counter = str(row.get("Тип счетчика", "") or "").strip()
            interval_raw = row.get("Интервал")
            date_raw = row.get("Дата активации")
            service_raw = row.get("Данные последнего обслуживания")

            if not number_ptoir:
                errors.append({"row": row_num, "field": "ПТОиР", "message": "Поле 'ПТОиР' пустое"})
                continue

            result = await db_session.execute(
                select(Ptoir.id, Ptoir.id_active).where(Ptoir.number_ptoir == number_ptoir)
            )
            ptoir_row = result.first()
            if ptoir_row is None:
                errors.append({"row": row_num, "field": "ПТОиР",
                                "message": f"ПТОиР не найден: '{number_ptoir}'"})
                continue
            ptoir_id, ptoir_active_id = ptoir_row

            if active_number:
                result = await db_session.execute(
                    select(Actives.id).where(Actives.active_number == active_number)
                )
                active_id = result.scalar_one_or_none()
                if active_id is None:
                    errors.append({"row": row_num, "field": "Актив",
                                    "message": f"Актив не найден: '{active_number}'"})
                    continue
                if ptoir_active_id is not None and active_id != ptoir_active_id:
                    errors.append({"row": row_num, "field": "Актив",
                                    "message": (f"Актив '{active_number}' не соответствует "
                                                f"активу ПТОиР '{number_ptoir}'")})
                    continue

            if not type_counter:
                errors.append({"row": row_num, "field": "Тип счетчика", "message": "Поле 'Тип счетчика' пустое"})
                continue

            result = await db_session.execute(
                select(CounterType.id).where(CounterType.type == type_counter)
            )
            counter_type_id = result.scalar_one_or_none()
            if counter_type_id is None:
                errors.append({"row": row_num, "field": "Тип счетчика",
                                "message": f"Тип счетчика не найден: '{type_counter}'"})
                continue

            result = await db_session.execute(
                select(PtoirLevelWarning.id).where(
                    (PtoirLevelWarning.id_ptoir == ptoir_id)
                    & (PtoirLevelWarning.id_counter_type == counter_type_id)
                )
            )
            level_warning_id = result.scalar_one_or_none()
            if level_warning_id is None:
                errors.append({"row": row_num, "field": "*",
                                "message": (f"Уровень предупреждения не найден для ПТОиР "
                                            f"'{number_ptoir}' и типа счетчика '{type_counter}'")})
                continue

            try:
                if isinstance(date_raw, datetime):
                    date_activation = date_raw - MSK_OFFSET
                else:
                    date_activation = date_parser.parse(str(date_raw), dayfirst=True) - MSK_OFFSET
            except Exception:
                errors.append({"row": row_num, "field": "Дата активации",
                                "message": f"Некорректная дата активации: '{date_raw}'"})
                continue

            try:
                interval = int(interval_raw)
            except (TypeError, ValueError):
                errors.append({"row": row_num, "field": "Интервал",
                                "message": f"Некорректный интервал: '{interval_raw}'"})
                continue

            if isinstance(service_raw, (int, float)) and not isinstance(service_raw, bool):
                zero_point_value = int(service_raw)
            else:
                try:
                    service_dt = date_parser.parse(str(service_raw), dayfirst=True) - MSK_OFFSET
                    service_dt = service_dt.replace(tzinfo=timezone.utc)
                    zero_point_value = int(service_dt.timestamp())
                except Exception:
                    errors.append({"row": row_num, "field": "Данные последнего обслуживания",
                                    "message": f"Некорректное значение обслуживания: '{service_raw}'"})
                    continue

            valid_rows.append((ptoir_id, date_activation, interval, level_warning_id, zero_point_value))

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

        sql_lines = ptoir_sql.update_ptoir(valid_rows)
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
                    for i, (ptoir_id, date_activation, interval, level_warning_id, zero_point_value) in enumerate(valid_rows, start=1):
                        await session.execute(
                            text(
                                "UPDATE public.ptoir SET date_activation = :da, interval = :iv, is_active = TRUE "
                                "WHERE id = :id"
                            ),
                            {"da": date_activation, "iv": interval, "id": ptoir_id},
                        )
                        await session.execute(
                            text("UPDATE public.ptoir_level_warning SET zero_point_value = :zp WHERE id = :id"),
                            {"zp": zero_point_value, "id": level_warning_id},
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
            f"=== Execute PTOиR update: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
            *ptoir_sql.update_ptoir(valid_rows),
            "",
        ]

        log_file = LOG_DIR / f"update_ptoir_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated ptoir for %d rows, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Успешно обновлено {len(valid_rows)} ПТОиР")

    @post("/change-hours/generate-sql/start")
    async def change_hours_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_change_hours_generate(progress, rows))

    async def _run_change_hours_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_change_hours_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        full_sql = "\n".join(["BEGIN;", *ptoir_sql.change_hours(valid_rows), "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/change-hours/execute-sql/start")
    async def change_hours_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_change_hours_execute(progress, rows))

    async def _run_change_hours_execute(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await validate_change_hours_rows(session, rows, progress=progress)
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для изменения"}])
                    return

                progress.update(processed=0, total=1, phase="executing")
                try:
                    # One transaction on the raw asyncpg connection: a plain SET is
                    # rolled back with it, so a failed UPDATE cannot hand the pooled
                    # (pgbouncer) connection back with the triggers still off.
                    conn = await session.connection()
                    raw_conn = await conn.get_raw_connection()
                    await raw_conn.driver_connection.execute("\n".join(ptoir_sql.change_hours(valid_rows)))
                    progress["processed"] = 1
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
            f"=== Execute change-hours update: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows in file: {len(rows)}, counters updated: {len(valid_rows)}",
            "",
            *(
                f"{vr['number_ptoir']} {vr['active_number']}: value {vr['old_value']} "
                f"(value_source {vr['old_value_source']}) -> {vr['value']}"
                for vr in valid_rows
            ),
            "",
            *ptoir_sql.change_hours(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"ptoir_hours_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Changed hours for %d actives, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Изменены моточасы у активов: {len(valid_rows)} (строк файла: {len(rows)})")

    @get("/progress/{task_id:str}")
    async def get_progress(self, task_id: str) -> Response:
        return progress_response(task_id)
