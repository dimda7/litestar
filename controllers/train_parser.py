import json
import logging
import tempfile
import uuid
from datetime import date, datetime
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

from models import TrainType, DesignNumber, Models, MileageTrain, CounterActive
from schemas import GenerateSQLRequest
from sql_utils import sql_escape
from parser_storage import (
    LOG_DIR,
    load_data as _load_data,
    save_data as _save_data,
    cleanup_old_files as _cleanup_old_files,
)

logger = logging.getLogger("train_parser")


def _lcn_to_model(lsn: str, id_train_type: int) -> str:
    """Конвертирует LSN из Excel в формат model lcn: 'M{id_train_type}.{lsn_path}'."""
    parts = lsn.split(".")
    if len(parts) == 1:
        return f"M{id_train_type}"
    return f"M{id_train_type}." + ".".join(parts[1:])


def _lcn_to_lcn(lsn: str, id_train: int) -> str:
    """Конвертирует LSN из Excel в формат actives lcn: '{id_train}.{lsn_path}'."""
    parts = lsn.split(".")
    if len(parts) == 1:
        return str(id_train)
    return f"{id_train}." + ".".join(parts[1:])


def _lcn_to_prelcn(lsn: str) -> str:
    """Получает родительский LCN, убирая последний сегмент."""
    parts = lsn.split(".")
    if len(parts) <= 1:
        return ""
    return ".".join(parts[:-1])


def _parse_car_number(position: str) -> int | None:
    """Парсит номер вагона из колонки position: '+100_(01)' → 1."""
    if not position:
        return None
    import re
    match = re.search(r"_\((\d+)\)", position)
    if match:
        return int(match.group(1))
    return None


class TrainParserController(Controller):
    path = "/train-parser"

    async def _validate_train_rows(
        self, db_session: AsyncSession, rows: list[dict], id_type_train: int, id_train: int,
    ) -> tuple[list[dict], list[dict]]:
        errors: list[dict] = []
        valid_rows: list[dict] = []

        key_actives: dict[str, str] = {}
        for el in rows:
            lsn = str(el.get("lsn", "") or "").strip()
            active_number = str(el.get("Актив", "") or "").strip()
            if lsn and active_number:
                key_actives[lsn] = active_number

        for idx, el in enumerate(rows):
            row_num = idx + 1
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
                "root_number": active_number if idx == 0 else None,
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
        """Загрузка Excel-файла со структурой поезда."""
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
    def _build_sql_body(
        id_train: int, id_type_train: int, train_name: str, valid_rows: list[dict], id_train_series: int,
    ) -> list[str]:
        """Строит тело SQL-скрипта вставки поезда (без BEGIN/COMMIT).

        Общий для «Скачать SQL-файл» (оборачивается в BEGIN;...COMMIT; и
        отдаётся как файл) и «Выполнить в базе данных» (выполняется как один
        multi-statement запрос в рамках уже открытой сессии) — оба пути
        строят один и тот же SQL, чтобы не расходиться в поведении.
        """
        now = datetime.utcnow().replace(microsecond=0)
        today = date.today()
        sql_lines: list[str] = []

        sql_lines.append(f"INSERT INTO public.train (id, id_train_type, name, is_active, is_delete) VALUES ({id_train}, {id_type_train}, '{sql_escape(train_name)}', true, false);")
        sql_lines.append(f"INSERT INTO public.mileage_train (id_train, milage, mileage_average, date, date_average) VALUES ({id_train}, 0, 0, '{now}', '{today}');")

        # id_location/id_actives не считаются заранее как max(id)+1 — этот
        # снимок мог устареть к моменту выполнения и вызывать конфликт PK.
        # Вместо этого id получают из sequence прямо в скрипте. Один
        # nextval() на переменную раздувал DECLARE до тысяч строк (id1..idN)
        # — вместо этого одним запросом набираем массив id сразу на все
        # строки и обращаемся к нему по индексу (loc_ids[i]).
        body_lines: list[str] = []

        for idx, vr in enumerate(valid_rows, start=1):
            loc_ref = f"loc_ids[{idx}]"
            act_ref = f"act_ids[{idx}]"

            sn = vr["serial_number"]
            sn_val = f"'{sql_escape(sn)}'" if sn and sn != "none" else "NULL"
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

        sql_lines.append("SELECT nextval('public.location_id_seq');")
        sql_lines.append("SELECT nextval('public.actives_id_seq');")

        return sql_lines

    @post("/generate-sql")
    async def generate_sql(
        self,
        request: Request,
        db_session: AsyncSession,
    ) -> Response:
        """Генерация SQL-файла для вставки данных поезда."""
        try:
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

            # Серия берётся из train_type.id_train_series — сама она у нас на
            # странице не выбирается, только "Тип поезда" (train_type.name).
            result = await db_session.execute(
                select(TrainType.id, TrainType.id_train_series).where(TrainType.name == train_type_name)
            )
            type_row = result.first()
            if type_row is None:
                return Response(
                    content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "train_type", "message": f"Тип поезда '{train_type_name}' не найден"}]}),
                    status_code=200,
                    media_type="application/json",
                )
            id_type_train, id_train_series = type_row
            if id_train_series is None:
                return Response(
                    content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "train_type", "message": f"У типа поезда '{train_type_name}' не задана серия (id_train_series)"}]}),
                    status_code=200,
                    media_type="application/json",
                )

            # id_train резервируется сразу через nextval — а не max(id)+1 — чтобы
            # к моменту реального запуска скачанного файла значение не могло
            # оказаться занятым другим поездом, созданным за это время.
            id_train = (await db_session.execute(text("SELECT nextval('public.train_id_seq')"))).scalar_one()

            errors, valid_rows = await self._validate_train_rows(db_session, rows, id_type_train, id_train)

            if errors:
                return Response(
                    content=json.dumps({"status": "error", "errors": errors}),
                    status_code=200,
                    media_type="application/json",
                )

            # Без явной транзакции каждая строка автокоммитится отдельно — при
            # ошибке где-то в середине (например, psql без ON_ERROR_STOP) часть
            # данных уже вставится, а остальные — нет, или (хуже) продолжат
            # выполняться и привяжутся не к тому train.id. BEGIN/COMMIT делает
            # весь файл одной атомарной операцией: либо всё, либо ничего.
            sql_lines = ["BEGIN;"]
            sql_lines.extend(self._build_sql_body(id_train, id_type_train, train_name, valid_rows, id_train_series))
            sql_lines.append("COMMIT;")

            content = "\n".join(sql_lines)
            return Response(
                content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
                status_code=200,
                media_type="application/json",
            )
        except Exception as e:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка: {e}"}]}),
                status_code=200,
                media_type="application/json",
            )

    @post("/execute")
    async def execute(
        self,
        request: Request,
        db_session: AsyncSession,
    ) -> Response:
        """Атомарная вставка данных поезда в БД (train, mileage, location, actives, counter_active)."""
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

        # Серия берётся из train_type.id_train_series — сама она у нас на
        # странице не выбирается, только "Тип поезда" (train_type.name).
        result = await db_session.execute(
            select(TrainType.id, TrainType.id_train_series).where(TrainType.name == train_type_name)
        )
        type_row = result.first()
        if type_row is None:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "train_type", "message": f"Тип поезда '{train_type_name}' не найден"}]}),
                status_code=200,
                media_type="application/json",
            )
        id_type_train, id_train_series = type_row
        if id_train_series is None:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "train_type", "message": f"У типа поезда '{train_type_name}' не задана серия (id_train_series)"}]}),
                status_code=200,
                media_type="application/json",
            )

        # Как и в generate_sql — резервируем id_train через nextval, а не
        # max(id)+1, чтобы не столкнуться с чужим поездом, вставленным
        # конкурентно между вычислением id и его использованием ниже.
        id_train = (await db_session.execute(text("SELECT nextval('public.train_id_seq')"))).scalar_one()

        errors, valid_rows = await self._validate_train_rows(db_session, rows, id_type_train, id_train)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для вставки"}]}),
                status_code=200,
                media_type="application/json",
            )

        # Тот же SQL, что и в generate_sql (без BEGIN/COMMIT — транзакцией
        # управляет сама сессия), выполняется одним multi-statement запросом
        # через «сырое» соединение: DO $$ ... $$ с несколькими операторами
        # внутри нельзя выполнить через обычный execute() с параметрами
        # (asyncpg не готовит несколько команд в одном prepared statement).
        sql_body = "\n".join(self._build_sql_body(id_train, id_type_train, train_name, valid_rows, id_train_series))

        try:
            # ВАЖНО: db_session.rollback() ниже откатывает и этот «сырой» вызов
            # только потому, что сессия уже открыла реальную транзакцию на
            # соединении раньше (select TrainType.id, nextval(), запросы внутри
            # _validate_train_rows). Если когда-нибудь этот блок станет первым
            # обращением к БД в запросе — транзакция не будет открыта и rollback
            # ничего не отменит. Не убирайте обращения к db_session до этой точки.
            conn = await db_session.connection()
            raw_conn = await conn.get_raw_connection()
            await raw_conn.driver_connection.execute(sql_body)

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
            f"=== Train Parser Execute: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Train: {train_name} (id={id_train}, type={train_type_name})",
            f"Rows processed: {len(valid_rows)}",
            "",
        ]
        log_file = LOG_DIR / f"train_parser_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Train parsed: %s, log saved to %s", train_name, log_file)

        return Response(
            content=json.dumps({"status": "ok", "count": len(valid_rows),
                                "message": f"Поезд '{train_name}' успешно добавлен (id={id_train})"}),
            status_code=200,
            media_type="application/json",
        )
