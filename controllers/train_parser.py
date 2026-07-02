import json
import logging
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path

import openpyxl
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Template, Response, Redirect

from models import TrainType, DesignNumber, Models, Train, Location, Actives, MileageTrain, CounterActive
from schemas import GenerateSQLRequest
from sql_utils import sql_escape

PARSER_DATA_DIR = Path(__file__).parent.parent / "parser_data"
PARSER_DATA_DIR.mkdir(exist_ok=True)

LOG_DIR = Path(__file__).parent.parent / "log"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("train_parser")


def _load_data(session_id: str) -> dict | None:
    path = PARSER_DATA_DIR / f"{session_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _save_data(session_id: str, data: dict) -> None:
    path = PARSER_DATA_DIR / f"{session_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


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
                    "SELECT id_car_place FROM grom.models "
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
                            "SELECT active_number FROM grom.actives "
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

            result = await db_session.execute(
                select(TrainType.id).where(TrainType.name == train_type_name)
            )
            id_type_train = result.scalar_one_or_none()
            if id_type_train is None:
                return Response(
                    content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "train_type", "message": f"Тип поезда '{train_type_name}' не найден"}]}),
                    status_code=200,
                    media_type="application/json",
                )

            max_train = (await db_session.execute(select(func.max(Train.id)))).scalar() or 0
            max_location = (await db_session.execute(select(func.max(Location.id)))).scalar() or 0
            max_actives = (await db_session.execute(select(func.max(Actives.id)))).scalar() or 0

            id_train = max_train + 1
            id_location = max_location + 1
            id_actives = max_actives + 1

            errors, valid_rows = await self._validate_train_rows(db_session, rows, id_type_train, id_train)

            if errors:
                return Response(
                    content=json.dumps({"status": "error", "errors": errors}),
                    status_code=200,
                    media_type="application/json",
                )

            now = datetime.utcnow().replace(microsecond=0)
            today = date.today()
            sql_lines: list[str] = []

            sql_lines.append(f"INSERT INTO grom.train (id, id_train_type, name, is_active, is_delete) VALUES ({id_train}, {id_type_train}, '{sql_escape(train_name)}', true, false);")
            sql_lines.append(f"INSERT INTO grom.mileage_train (id_train, milage, mileage_average, date, date_average) VALUES ({id_train}, 0, 0, '{now}', '{today}');")

            for vr in valid_rows:
                sn = vr["serial_number"]
                sn_val = f"'{sql_escape(sn)}'" if sn and sn != "none" else "NULL"
                parent_val = f"'{sql_escape(str(vr['id_actives_parent']))}'" if vr["id_actives_parent"] else "NULL"
                root_val = f"'{sql_escape(str(vr['root_number']))}'" if vr["root_number"] else "NULL"
                car_num_val = str(vr["car_number"]) if vr["car_number"] is not None else "NULL"
                cp_val = str(vr["car_place_id"]) if vr["car_place_id"] is not None else "NULL"
                ut_val = str(vr["id_unit_type"]) if vr["id_unit_type"] is not None else "NULL"

                sql_lines.append(
                    f"INSERT INTO grom.location (id, id_type_location, id_train, car_number, id_car_place) "
                    f"VALUES ({id_location}, 2, {id_train}, {car_num_val}, {cp_val});"
                )
                sql_lines.append(
                    f"INSERT INTO grom.actives (id, active_number, id_unit_type, id_design_number, id_location, "
                    f"serial_number, lcn, id_actves_parent, id_actives_root) "
                    f"VALUES ({id_actives}, '{sql_escape(vr['active_number'])}', {ut_val}, {vr['id_design_number']}, "
                    f"{id_location}, {sn_val}, '{sql_escape(vr['lcn_new'])}', {parent_val}, {root_val});"
                )

                if vr["is_root"]:
                    sql_lines.append(f"UPDATE grom.counter_active SET is_train = true WHERE id_active = {id_actives};")

                id_location += 1
                id_actives += 1

            sql_lines.append(f"SELECT setval('grom.actives_id_seq', {id_actives});")
            sql_lines.append(f"SELECT setval('grom.location_id_seq', {id_location});")
            sql_lines.append(f"SELECT setval('grom.train_id_seq', {id_train});")

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

        result = await db_session.execute(
            select(TrainType.id).where(TrainType.name == train_type_name)
        )
        id_type_train = result.scalar_one_or_none()
        if id_type_train is None:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "train_type", "message": f"Тип поезда '{train_type_name}' не найден"}]}),
                status_code=200,
                media_type="application/json",
            )

        max_train = (await db_session.execute(select(func.max(Train.id)))).scalar() or 0
        max_location = (await db_session.execute(select(func.max(Location.id)))).scalar() or 0
        max_actives = (await db_session.execute(select(func.max(Actives.id)))).scalar() or 0

        id_train = max_train + 1
        id_location = max_location + 1
        id_actives = max_actives + 1

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

        try:
            await db_session.execute(
                text(
                    "INSERT INTO grom.train (id, id_train_type, name, is_active, is_delete) "
                    "VALUES (:id, :tt, :name, true, false)"
                ),
                {"id": id_train, "tt": id_type_train, "name": train_name},
            )

            now = datetime.utcnow().replace(microsecond=0)
            today = date.today()
            await db_session.execute(
                text(
                    "INSERT INTO grom.mileage_train (id_train, milage, mileage_average, date, date_average) "
                    "VALUES (:id_train, 0, 0, :date, :date_average)"
                ),
                {"id_train": id_train, "date": now, "date_average": today},
            )

            for vr in valid_rows:
                sn = vr["serial_number"]
                sn_val = sn if sn and sn != "none" else None

                await db_session.execute(
                    text(
                        "INSERT INTO grom.location (id, id_type_location, id_train, car_number, id_car_place) "
                        "VALUES (:id, 2, :id_train, :car_number, :id_car_place)"
                    ),
                    {"id": id_location, "id_train": id_train, "car_number": vr["car_number"], "id_car_place": vr["car_place_id"]},
                )

                await db_session.execute(
                    text(
                        "INSERT INTO grom.actives (id, active_number, id_unit_type, id_design_number, id_location, "
                        "serial_number, lcn, id_actves_parent, id_actives_root) "
                        "VALUES (:id, :active_number, :id_unit_type, :id_design_number, :id_location, "
                        ":serial_number, :lcn, :parent, :root)"
                    ),
                    {
                        "id": id_actives,
                        "active_number": vr["active_number"],
                        "id_unit_type": vr["id_unit_type"],
                        "id_design_number": vr["id_design_number"],
                        "id_location": id_location,
                        "serial_number": sn_val,
                        "lcn": vr["lcn_new"],
                        "parent": vr["id_actives_parent"],
                        "root": vr["root_number"],
                    },
                )

                if vr["is_root"]:
                    await db_session.execute(
                        text("UPDATE grom.counter_active SET is_train = true WHERE id_active = :id"),
                        {"id": id_actives},
                    )

                id_location += 1
                id_actives += 1

            await db_session.execute(text(f"SELECT setval('grom.actives_id_seq', {id_actives})"))
            await db_session.execute(text(f"SELECT setval('grom.location_id_seq', {id_location})"))
            await db_session.execute(text(f"SELECT setval('grom.train_id_seq', {id_train})"))

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
            f"Next location_id: {id_location}, next actives_id: {id_actives}",
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
