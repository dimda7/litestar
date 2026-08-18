import asyncio
import json
import logging
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path

import openpyxl
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.datastructures import UploadFile
from litestar.response import Template, Response, Redirect

from db_manager import get_session_maker
from models import TrainType, DesignNumber, Models, MileageTrain, CounterActive
from sql_utils import sql_escape
from parser_storage import (
    LOG_DIR,
    load_data as _load_data,
    save_data as _save_data,
    cleanup_old_files as _cleanup_old_files,
)

logger = logging.getLogger("train_parser")

# Validation issues several database queries per row, and on large files
# (thousands of rows) that takes seconds — progress is served through a
# separate poll rather than holding one HTTP request open all that time.
PROGRESS_TTL_SECONDS = 15 * 60
_progress: dict[str, dict] = {}
# asyncio only keeps a weak reference to fire-and-forget tasks — without
# storing it explicitly the task can be garbage collected before it finishes.
_tasks: dict[str, asyncio.Task] = {}


def _cleanup_progress() -> None:
    cutoff = time.time() - PROGRESS_TTL_SECONDS
    stale = [tid for tid, state in _progress.items() if state["created_at"] < cutoff]
    for tid in stale:
        _progress.pop(tid, None)


def _lcn_to_model(lsn: str, id_train_type: int) -> str:
    """Convert an Excel LSN into the model lcn format: 'M{id_train_type}.{lsn_path}'."""
    parts = lsn.split(".")
    if len(parts) == 1:
        return f"M{id_train_type}"
    return f"M{id_train_type}." + ".".join(parts[1:])


def _lcn_to_lcn(lsn: str, id_train: int) -> str:
    """Convert an Excel LSN into the actives lcn format: '{id_train}.{lsn_path}'."""
    parts = lsn.split(".")
    if len(parts) == 1:
        return str(id_train)
    return f"{id_train}." + ".".join(parts[1:])


def _lcn_to_prelcn(lsn: str) -> str:
    """Return the parent LCN by dropping the last segment."""
    parts = lsn.split(".")
    if len(parts) <= 1:
        return ""
    return ".".join(parts[:-1])


def _parse_car_number(position: str) -> int | None:
    """Parse the car number out of the position column: '+100_(01)' -> 1."""
    if not position:
        return None
    import re
    match = re.search(r"_\((\d+)\)", position)
    if match:
        return int(match.group(1))
    return None


def _parse_count_car(lsn: str) -> int | None:
    """Extract count_car — the second lsn segment of the file's last row ('361.5.9.4' -> 5)."""
    parts = lsn.split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


class TrainParserController(Controller):
    path = "/train-parser"

    async def _validate_train_rows(
        self, db_session: AsyncSession, rows: list[dict], id_type_train: int, id_train: int,
        progress: dict | None = None,
    ) -> tuple[list[dict], list[dict]]:
        errors: list[dict] = []
        valid_rows: list[dict] = []

        key_actives: dict[str, str] = {}
        for el in rows:
            lsn = str(el.get("lsn", "") or "").strip()
            active_number = str(el.get("Актив", "") or "").strip()
            if lsn and active_number:
                key_actives[lsn] = active_number

        # The file's first row is the train's head asset (a single-segment lsn,
        # '361' for instance). Its own id_actives_root is NULL, while every other
        # asset carries the head's active_number (not the other way round).
        root_active_number = str(rows[0].get("Актив", "") or "").strip() if rows else ""

        if progress is not None:
            progress.update(processed=0, total=len(rows), phase="validating")

        for idx, el in enumerate(rows):
            row_num = idx + 1
            if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
                progress["processed"] = row_num
            active_number = str(el.get("Актив", "") or "").strip()
            serial_number = str(el.get("Сер", "") or "").strip()
            itemnum = str(el.get("itemnum", "") or "").strip()
            lsn = str(el.get("lsn", "") or "").strip()
            position = str(el.get("position", "") or "").strip()

            if not lsn or not itemnum:
                errors.append({"row": row_num, "field": "*", "message": "Пустые lsn или itemnum"})
                continue

            lsn_split = lsn.split(".")
            lcn_model = _lcn_to_model(lsn, id_type_train)
            lcn_new = _lcn_to_lcn(lsn, id_train)
            car_number = None if len(lsn_split) == 1 else _parse_car_number(position)

            result = await db_session.execute(
                select(DesignNumber.id, DesignNumber.id_unit_type)
                .where(DesignNumber.number == itemnum)
            )
            dn_row = result.first()
            if dn_row is None:
                errors.append({"row": row_num, "field": "itemnum", "message": f"design_number '{itemnum}' не найден"})
                continue

            id_design_number = dn_row[0]
            id_unit_type = dn_row[1]

            model_result = await db_session.execute(
                text(
                    "SELECT id_car_place FROM public.models "
                    "WHERE id_train_type = :tt AND id_design_number = :dn AND lcn::text = :lcn"
                ),
                {"tt": id_type_train, "dn": id_design_number, "lcn": lcn_model},
            )
            model_row = model_result.first()
            car_place_id = model_row[0] if model_row and model_row[0] is not None else None

            if len(lsn_split) == 1:
                id_actives_parent = None
            else:
                pre_lcn = _lcn_to_prelcn(lsn)
                if pre_lcn in key_actives:
                    id_actives_parent = key_actives[pre_lcn]
                else:
                    parent_result = await db_session.execute(
                        text(
                            "SELECT active_number FROM public.actives "
                            "WHERE lcn::text = :lcn LIMIT 1"
                        ),
                        {"lcn": _lcn_to_lcn(pre_lcn, id_train)},
                    )
                    parent_row = parent_result.first()
                    id_actives_parent = parent_row[0] if parent_row else None

            valid_rows.append({
                "active_number": active_number,
                "serial_number": serial_number,
                "id_unit_type": id_unit_type,
                "id_design_number": id_design_number,
                "car_number": car_number,
                "car_place_id": car_place_id,
                "lcn_new": lcn_new,
                "id_actives_parent": id_actives_parent,
                "is_root": idx == 0,
                "root_number": None if idx == 0 else root_active_number,
            })

        return errors, valid_rows

    @get("/")
    async def index(self, request: Request, page: int = 1, per_page: int = 10) -> Template:
        page = max(page, 1)
        per_page = min(per_page, 200)
        error: str = request.session.pop("train_parser_error", "")
        success: str = request.session.pop("train_parser_success", "")

        session_id = request.session.get("train_parser_session_id", "")
        stored = _load_data(session_id) if session_id else None

        all_rows: list[dict] = stored["rows"] if stored else []
        headers: list[str] = stored["headers"] if stored else []
        filename: str = stored.get("filename", "") if stored else ""

        total = len(all_rows)
        total_pages = max((total + per_page - 1) // per_page, 1)
        if page > total_pages:
            page = total_pages

        offset = (page - 1) * per_page
        rows = all_rows[offset:offset + per_page]

        return Template(
            template_name="train_parser.html",
            context={
                "headers": headers,
                "rows": rows,
                "all_rows": all_rows,
                "filename": filename,
                "error": error,
                "success": success,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "train_parser",
            },
        )

    @post("/upload")
    async def upload(self, request: Request) -> Redirect:
        """Upload of the Excel file holding the train structure."""
        form = await request.form()
        upload_file: UploadFile | None = form.get("file")

        if not upload_file or not upload_file.filename:
            request.session["train_parser_error"] = "Файл не выбран"
            return Redirect("/train-parser")

        suffix = Path(upload_file.filename).suffix.lower()
        if suffix not in (".xlsx", ".xls"):
            request.session["train_parser_error"] = "Поддерживаются только .xlsx и .xls файлы"
            return Redirect("/train-parser")

        try:
            _cleanup_old_files()

            content = await upload_file.read()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

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
            request.session["train_parser_session_id"] = session_id

            return Redirect("/train-parser")
        except Exception as e:
            request.session["train_parser_error"] = f"Ошибка чтения файла: {e}"
            return Redirect("/train-parser")

    @staticmethod
    async def _resolve_type_and_series(
        db_session: AsyncSession, train_type_name: str,
    ) -> tuple[int | None, int | None, str | None]:
        """Resolve id_type_train and its series (train_type.id_train_series). Returns (id_type_train, id_train_series, error)."""
        result = await db_session.execute(
            select(TrainType.id, TrainType.id_train_series).where(TrainType.name == train_type_name)
        )
        type_row = result.first()
        if type_row is None:
            return None, None, f"Тип поезда '{train_type_name}' не найден"
        id_type_train, id_train_series = type_row
        if id_train_series is None:
            return None, None, f"У типа поезда '{train_type_name}' не задана серия (id_train_series)"
        return id_type_train, id_train_series, None

    @staticmethod
    def _build_sql_body(
        id_train: int, id_type_train: int, train_name: str, valid_rows: list[dict], id_train_series: int,
        count_car: int | None = None,
    ) -> list[str]:
        """Build the body of the train insert script (without BEGIN/COMMIT).

        Shared by "Скачать SQL-файл" (wrapped in BEGIN;...COMMIT; and served as
        a file) and "Выполнить в базе данных" (run as a single multi-statement
        query inside the already open session) — both paths build the same SQL
        so their behaviour cannot drift apart.
        """
        now = datetime.utcnow().replace(microsecond=0)
        today = date.today()
        sql_lines: list[str] = []

        sql_lines.append(f"INSERT INTO public.train (id, id_train_type, name, is_active, is_delete) VALUES ({id_train}, {id_type_train}, '{sql_escape(train_name)}', true, false);")
        sql_lines.append(f"INSERT INTO public.mileage_train (id_train, milage, mileage_average, date, date_average) VALUES ({id_train}, 0, 0, '{now}', '{today}');")

        # id_location/id_actives are not precomputed as max(id)+1 — that snapshot
        # could go stale by execution time and collide on the PK. The ids come
        # from the sequence inside the script instead. One nextval() per variable
        # blew DECLARE up to thousands of lines (id1..idN), so a single query
        # collects an array of ids for all rows at once, indexed into as
        # loc_ids[i].
        body_lines: list[str] = []

        for idx, vr in enumerate(valid_rows, start=1):
            loc_ref = f"loc_ids[{idx}]"
            act_ref = f"act_ids[{idx}]"

            sn = vr["serial_number"]
            sn_val = f"'{sql_escape(sn)}'" if sn else "NULL"
            parent_val = f"'{sql_escape(str(vr['id_actives_parent']))}'" if vr["id_actives_parent"] else "NULL"
            root_val = f"'{sql_escape(str(vr['root_number']))}'" if vr["root_number"] else "NULL"
            car_num_val = str(vr["car_number"]) if vr["car_number"] is not None else "NULL"
            cp_val = str(vr["car_place_id"]) if vr["car_place_id"] is not None else "NULL"
            ut_val = str(vr["id_unit_type"]) if vr["id_unit_type"] is not None else "NULL"

            body_lines.append(
                f"    INSERT INTO public.location (id, id_type_location, id_train, car_number, id_car_place) "
                f"VALUES ({loc_ref}, 2, {id_train}, {car_num_val}, {cp_val});"
            )
            body_lines.append(
                f"    INSERT INTO public.actives (id, active_number, id_unit_type, id_design_number, id_location, "
                f"serial_number, lcn, id_actves_parent, id_actives_root) "
                f"VALUES ({act_ref}, '{sql_escape(vr['active_number'])}', {ut_val}, {vr['id_design_number']}, "
                f"{loc_ref}, {sn_val}, '{sql_escape(vr['lcn_new'])}', {parent_val}, {root_val});"
            )

            if vr["is_root"]:
                body_lines.append(f"    UPDATE public.counter_active SET is_train = true WHERE id_active = {act_ref};")

        sql_lines.append("DO $$")
        sql_lines.append("DECLARE")
        sql_lines.append(
            f"    loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') "
            f"FROM generate_series(1, {len(valid_rows)}));"
        )
        sql_lines.append(
            f"    act_ids bigint[] := ARRAY(SELECT nextval('public.actives_id_seq') "
            f"FROM generate_series(1, {len(valid_rows)}));"
        )
        sql_lines.append("BEGIN")
        sql_lines.extend(body_lines)
        sql_lines.append("END $$;")

        sql_lines.append(
            f"UPDATE public.train AS t SET active = act.id "
            f"FROM public.location AS loc LEFT JOIN public.actives act ON act.id_location = loc.id "
            f"WHERE nlevel(act.lcn) = 1 AND loc.id_train = t.id AND t.id = {id_train};"
        )

        sql_lines.append(
            f"UPDATE public.train SET id_train_series = {id_train_series} WHERE id = {id_train};"
        )

        if count_car is not None:
            sql_lines.append(
                f"UPDATE public.train SET count_car = {count_car} WHERE id = {id_train};"
            )

        sql_lines.append("SELECT nextval('public.location_id_seq');")
        sql_lines.append("SELECT nextval('public.actives_id_seq');")

        return sql_lines

    @post("/generate-sql/start")
    async def generate_sql_start(self, request: Request) -> Response:
        """Start background SQL file generation; returns a task_id to poll for progress."""
        form = await request.form()
        train_name = str(form.get("train_name", "")).strip()
        train_type_name = str(form.get("train_type_name", "")).strip()

        if not train_name or not train_type_name:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": "Укажите название поезда и тип поезда"}]}),
                status_code=200,
                media_type="application/json",
            )

        session_id = request.session.get("train_parser_session_id", "")
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
        task = asyncio.ensure_future(self._run_generate(task_id, train_name, train_type_name, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task

        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_generate(self, task_id: str, train_name: str, train_type_name: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                id_type_train, id_train_series, series_error = await self._resolve_type_and_series(session, train_type_name)
                if series_error:
                    progress.update(status="error", errors=[{"row": 0, "field": "train_type", "message": series_error}])
                    return

                # id_train is reserved through nextval rather than max(id)+1, so that
                # by the time the downloaded file is actually run the value cannot have
                # been taken by another train created in the meantime.
                id_train = (await session.execute(text("SELECT nextval('public.train_id_seq')"))).scalar_one()

                errors, valid_rows = await self._validate_train_rows(session, rows, id_type_train, id_train, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        # Without an explicit transaction every statement autocommits on its own —
        # an error halfway through (psql without ON_ERROR_STOP, say) leaves some
        # data inserted and the rest not, or worse, lets the remainder run and
        # attach to the wrong train.id. BEGIN/COMMIT makes the whole file one
        # atomic operation: all of it or none.
        count_car = _parse_count_car(str(rows[-1].get("lsn", "") or "").strip()) if rows else None
        sql_lines = ["BEGIN;"]
        sql_lines.extend(self._build_sql_body(id_train, id_type_train, train_name, valid_rows, id_train_series, count_car))
        sql_lines.append("COMMIT;")

        progress.update(status="done", sql="\n".join(sql_lines), count=len(valid_rows))

    @post("/execute/start")
    async def execute_start(self, request: Request) -> Response:
        """Start the background atomic train insert; returns a task_id to poll for progress."""
        form = await request.form()
        train_name = str(form.get("train_name", "")).strip()
        train_type_name = str(form.get("train_type_name", "")).strip()

        if not train_name or not train_type_name:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": "Укажите название поезда и тип поезда"}]}),
                status_code=200,
                media_type="application/json",
            )

        session_id = request.session.get("train_parser_session_id", "")
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
        task = asyncio.ensure_future(self._run_execute(task_id, train_name, train_type_name, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task

        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_execute(self, task_id: str, train_name: str, train_type_name: str, rows: list[dict]) -> None:
        """Atomic insert of the train data (train, mileage, location, actives, counter_active)."""
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                id_type_train, id_train_series, series_error = await self._resolve_type_and_series(session, train_type_name)
                if series_error:
                    progress.update(status="error", errors=[{"row": 0, "field": "train_type", "message": series_error}])
                    return

                # As in generate_sql, id_train is reserved through nextval rather than
                # max(id)+1, so it cannot collide with another train inserted
                # concurrently between computing the id and using it below.
                id_train = (await session.execute(text("SELECT nextval('public.train_id_seq')"))).scalar_one()

                errors, valid_rows = await self._validate_train_rows(session, rows, id_type_train, id_train, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для вставки"}])
                    return

                # DO $$ ... $$ runs as one whole query — progress inside it cannot be
                # tracked, so the "executing" phase merely signals that writing is
                # under way, without a step-by-step percentage.
                progress.update(processed=0, total=1, phase="executing")

                # The same SQL as in generate_sql (without BEGIN/COMMIT — the session
                # owns the transaction) is run as a single multi-statement query over the
                # raw connection: DO $$ ... $$ containing several statements cannot go
                # through an ordinary parameterised execute(), because asyncpg will not
                # prepare several commands in one prepared statement.
                count_car = _parse_count_car(str(rows[-1].get("lsn", "") or "").strip()) if rows else None
                sql_body = "\n".join(self._build_sql_body(id_train, id_type_train, train_name, valid_rows, id_train_series, count_car))

                try:
                    # IMPORTANT: session.rollback() below also rolls this raw call back
                    # only because the session already opened a real transaction on the
                    # connection earlier (select TrainType.id, nextval(), the queries
                    # inside _validate_train_rows). If this block ever becomes the first
                    # database access, no transaction will be open and the rollback will
                    # undo nothing. Do not remove the session usage before this point.
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
            f"=== Train Parser Execute: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Train: {train_name} (id={id_train}, type={train_type_name})",
            f"Rows processed: {len(valid_rows)}",
            "",
        ]
        log_file = LOG_DIR / f"train_parser_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Train parsed: %s, log saved to %s", train_name, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Поезд '{train_name}' успешно добавлен (id={id_train})")

    @get("/progress/{task_id:str}")
    async def get_progress(self, task_id: str) -> Response:
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
