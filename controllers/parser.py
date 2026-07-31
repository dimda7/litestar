import asyncio
import json
import logging
import re
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Template, Response, Redirect

from db_manager import get_session_maker
from models import TrainType, CarPlace, DesignNumber, Models, Train, Storage, Consignment, User
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

# Валидация "set serial='none' lcn" дёргает запрос к БД на каждую уникальную
# id_train_type, на больших файлах это идёт секундами — прогресс отдаётся
# через отдельный опрос, чтобы не держать один HTTP-запрос открытым всё это время.
PROGRESS_TTL_SECONDS = 15 * 60
_progress: dict[str, dict] = {}
# asyncio хранит только слабую ссылку на fire-and-forget задачи — без явного
# хранения задача может быть собрана GC до завершения.
_tasks: dict[str, asyncio.Task] = {}

# Модельный lcn вида 'M9.6.5': цифры после буквенного префикса и до первой
# точки — id_train_type, остаток пути (после первой точки) переносится как есть.
_MODEL_LCN_RE = re.compile(r"^\D*(\d+)(?:\.(.*))?$")

# Excel-даты в остальных парсерах проекта вводятся по московскому времени, в
# БД (relocate.date/date_current) хранятся в UTC — тот же сдвиг, что и в
# ptoir_parser.py (MSK_OFFSET). Здесь дата не берётся из файла, поэтому сдвиг
# применяется один раз к "сейчас" на весь батч перемещения.
MOVE_TZ_SHIFT = timedelta(hours=3)


def _cleanup_progress() -> None:
    cutoff = time.time() - PROGRESS_TTL_SECONDS
    stale = [tid for tid, state in _progress.items() if state["created_at"] < cutoff]
    for tid in stale:
        _progress.pop(tid, None)


def _parse_model_lcn(lcn: str) -> tuple[int, str] | None:
    """Извлекает (id_train_type, остаток_пути) из lcn вида 'M9.6.5' -> (9, '6.5'); 'M9' -> (9, '')."""
    match = _MODEL_LCN_RE.match(lcn)
    if not match:
        return None
    return int(match.group(1)), match.group(2) or ""


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
                matches = result.scalars().all()
                if len(matches) == 1:
                    car_place_id = matches[0]
                elif len(matches) == 0:
                    errors.append({"row": row_num, "field": "position",
                                   "message": f"car_place не найден: '{position}'"})
                else:
                    errors.append({"row": row_num, "field": "position",
                                   "message": (f"car_place неоднозначен: найдено {len(matches)} записей "
                                               f"с именем '{position}' (id: {matches})")})

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

    async def _validate_and_build_serial_none_rows(
        self, db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Валидирует строки Excel для 'set serial=none lcn'.

        lcn вида 'M9.6.5': 9 — id_train_type, '6.5' — остаток пути. Для каждого
        поезда с этим id_train_type подставляем его id вместо 'M9' и получаем
        список lcn активов ('lcn_trains'), которым нужно проставить serial_number='none'.
        """
        errors: list[dict] = []
        valid_rows: list[dict] = []

        lcn_column: str | None = next(
            (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("lsn", "lcn")),
            None,
        )
        if rows and lcn_column is None:
            errors.append({"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"})
            return errors, valid_rows

        if progress is not None:
            progress.update(processed=0, total=len(rows), phase="validating")

        train_ids_by_type: dict[int, list[int]] = {}

        for idx, row in enumerate(rows):
            row_num = idx + 1
            if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
                progress["processed"] = row_num

            lcn_raw = str(row.get(lcn_column, "") or "").strip()
            if not lcn_raw:
                errors.append({"row": row_num, "field": "lcn", "message": "Пустой lcn"})
                continue

            parsed = _parse_model_lcn(lcn_raw)
            if parsed is None:
                errors.append({"row": row_num, "field": "lcn",
                               "message": f"Не удалось распознать id_train_type в lcn '{lcn_raw}'"})
                continue
            id_train_type, rest = parsed

            if id_train_type not in train_ids_by_type:
                result = await db_session.execute(select(Train.id).where(Train.id_train_type == id_train_type))
                train_ids_by_type[id_train_type] = [r[0] for r in result.all()]
            train_ids = train_ids_by_type[id_train_type]

            if not train_ids:
                errors.append({"row": row_num, "field": "lcn",
                               "message": f"Поезда с id_train_type={id_train_type} не найдены"})
                continue

            lcn_trains = [f"{tid}.{rest}" if rest else str(tid) for tid in train_ids]

            valid_rows.append({
                "row": row_num,
                "lcn": lcn_raw,
                "id_train_type": id_train_type,
                "lcn_trains": lcn_trains,
            })

        return errors, valid_rows

    async def _validate_and_build_change_lcn_rows(
        self, db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Валидирует строки Excel для 'изменить lcn в модели'.

        Каждая строка задаёт старый и новый модельный lcn ('Старый lsn' ->
        'lsn', оба вида 'M9.1.6') для одной и той же позиции. По id_train_type
        (должен совпадать у обоих) находятся все поезда этого типа, для
        каждого строится пара реальных lcn активов (старый -> новый).
        Дублирующиеся/пересекающиеся строки файла схлопываются в одну пару;
        одинаковый старый lcn с разными новыми — конфликт (ошибка).
        """
        errors: list[dict] = []
        pairs: dict[str, str] = {}
        pair_list: list[dict] = []

        new_column: str | None = next(
            (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("lsn", "lcn")),
            None,
        )
        old_column: str | None = next(
            (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("старый lsn", "старый lcn")),
            None,
        )
        if rows and new_column is None:
            errors.append({"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"})
            return errors, pair_list
        if rows and old_column is None:
            errors.append({"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'Старый lsn' (или 'Старый lcn')"})
            return errors, pair_list

        if progress is not None:
            progress.update(processed=0, total=len(rows), phase="validating")

        train_ids_by_type: dict[int, list[int]] = {}

        for idx, row in enumerate(rows):
            row_num = idx + 1
            if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
                progress["processed"] = row_num

            new_raw = str(row.get(new_column, "") or "").strip()
            old_raw = str(row.get(old_column, "") or "").strip()
            if not new_raw:
                errors.append({"row": row_num, "field": "lcn", "message": "Пустой lcn"})
                continue
            if not old_raw:
                errors.append({"row": row_num, "field": "lcn", "message": "Пустой Старый lcn"})
                continue

            new_parsed = _parse_model_lcn(new_raw)
            if new_parsed is None:
                errors.append({"row": row_num, "field": "lcn",
                               "message": f"Не удалось распознать id_train_type в lcn '{new_raw}'"})
                continue
            old_parsed = _parse_model_lcn(old_raw)
            if old_parsed is None:
                errors.append({"row": row_num, "field": "lcn",
                               "message": f"Не удалось распознать id_train_type в Старый lcn '{old_raw}'"})
                continue

            id_train_type_new, new_path = new_parsed
            id_train_type_old, old_path = old_parsed
            if id_train_type_new != id_train_type_old:
                errors.append({"row": row_num, "field": "lcn",
                               "message": (f"id_train_type не совпадает у 'Старый lsn' и 'lsn': "
                                           f"'{old_raw}' ({id_train_type_old}) vs '{new_raw}' ({id_train_type_new})")})
                continue

            if id_train_type_new not in train_ids_by_type:
                result = await db_session.execute(select(Train.id).where(Train.id_train_type == id_train_type_new))
                train_ids_by_type[id_train_type_new] = [r[0] for r in result.all()]
            train_ids = train_ids_by_type[id_train_type_new]

            if not train_ids:
                errors.append({"row": row_num, "field": "lcn",
                               "message": f"Поезда с id_train_type={id_train_type_new} не найдены"})
                continue

            row_pairs: list[tuple[str, str]] = []
            conflict = False
            for tid in train_ids:
                old_lcn = f"{tid}.{old_path}" if old_path else str(tid)
                new_lcn = f"{tid}.{new_path}" if new_path else str(tid)
                if old_lcn in pairs and pairs[old_lcn] != new_lcn:
                    errors.append({"row": row_num, "field": "lcn",
                                   "message": (f"Конфликт: '{old_lcn}' уже сопоставлен другому lcn "
                                               f"('{pairs[old_lcn]}', а не '{new_lcn}')")})
                    conflict = True
                    break
                row_pairs.append((old_lcn, new_lcn))
            if conflict:
                continue

            for old_lcn, new_lcn in row_pairs:
                if old_lcn not in pairs:
                    pairs[old_lcn] = new_lcn
                    pair_list.append({"old_lcn": old_lcn, "new_lcn": new_lcn})

        return errors, pair_list

    @staticmethod
    def _build_two_phase_lcn_update_lines(valid_rows: list[dict], extra_set: str = "") -> list[str]:
        """Двухфазный UPDATE lcn: старое -> временное ('Z'+старое) -> новое.

        Файл часто содержит цепочки (новый lcn одной пары совпадает со старым
        lcn другой — например, дочерняя позиция сдвигается вслед за
        родительской). Обновление в одну FROM(VALUES...)-инструкцию падает с
        UniqueViolationError на actives_lcn_key: пока не все строки
        переставлены, промежуточное состояние содержит дубликат. Временный
        префикс 'Z' гарантированно не пересекается с реальными lcn (train_id,
        'M'-модельные, 'S'-складские) — после первого прохода ни один активный
        lcn не совпадает со старым/новым значением другой пары.

        extra_set — дополнительные ", колонка = значение" в финальном UPDATE
        (например, сброс id_actves_parent/id_actives_root при перемещении).
        """
        if not valid_rows:
            return []
        old_list = ", ".join(f"'{sql_escape(vr['old_lcn'])}'" for vr in valid_rows)
        tmp_values_list = ", ".join(
            f"('Z{sql_escape(vr['old_lcn'])}', '{sql_escape(vr['new_lcn'])}')" for vr in valid_rows
        )
        return [
            f"UPDATE public.actives SET lcn = ('Z' || lcn::text)::ltree WHERE lcn::text IN ({old_list});",
            f"UPDATE public.actives AS act SET lcn = v.new_lcn::ltree{extra_set} "
            f"FROM (VALUES {tmp_values_list}) AS v(tmp_lcn, new_lcn) WHERE act.lcn::text = v.tmp_lcn;",
        ]

    @staticmethod
    def _build_change_lcn_sql_lines(valid_rows: list[dict]) -> list[str]:
        return ParserController._build_two_phase_lcn_update_lines(valid_rows)

    @staticmethod
    def _build_move_no_relocate_sql_lines(valid_rows: list[dict]) -> list[str]:
        """Как _build_change_lcn_sql_lines, но дополнительно сбрасывает
        id_actves_parent/id_actives_root — активная позиция сменилась,
        старые ссылки на родителя/корень больше не актуальны (тот же сброс,
        что и в 'Переместить активы', но без смены id_location/relocate)."""
        return ParserController._build_two_phase_lcn_update_lines(
            valid_rows, extra_set=", id_actves_parent = NULL, id_actives_root = NULL",
        )

    @staticmethod
    def _merge_serial_none_lcns(valid_rows: list[dict]) -> list[str]:
        """Объединяет lcn_trains всех строк файла в один список без дублей.

        Разные строки Excel с одинаковым lsn (или разными lsn, дающими
        пересекающиеся lcn) иначе давали бы несколько одинаковых/пересекающихся
        UPDATE-запросов подряд — вместо этого один запрос на все строки файла.
        """
        seen: set[str] = set()
        merged: list[str] = []
        for vr in valid_rows:
            for l in vr["lcn_trains"]:
                if l not in seen:
                    seen.add(l)
                    merged.append(l)
        return merged

    @staticmethod
    def _build_serial_none_sql_lines(valid_rows: list[dict]) -> list[str]:
        lcns = ParserController._merge_serial_none_lcns(valid_rows)
        if not lcns:
            return []
        lcn_list = ", ".join(f"'{sql_escape(l)}'" for l in lcns)
        return [f"UPDATE public.actives SET serial_number = 'none' WHERE lcn::text IN ({lcn_list});"]

    @staticmethod
    async def _resolve_user_by_fullname(db_session: AsyncSession, fullname: str) -> int | None:
        """Ищет fdw_users по ФИО, построенному так же, как session['fullname'] в auth.py:

        ' '.join(filter(None, [lastname, firstname, middlename])) or username.
        """
        result = await db_session.execute(select(User.id, User.lastname, User.firstname, User.middlename, User.username))
        for uid, lastname, firstname, middlename, username in result.all():
            built = " ".join(filter(None, [lastname, firstname, middlename])) or username or ""
            if built.strip() == fullname:
                return uid
        return None

    async def _validate_and_build_move_rows(
        self, db_session: AsyncSession, rows: list[dict],
        storage_name: str, consignment_name: str, user_fullname: str, set_nocm: bool,
        progress: dict | None = None,
    ) -> tuple[list[dict], list[dict], int | None, int | None, int | None, int | None]:
        """Валидирует строки Excel для 'Переместить активы' (аналог move_active).

        Активы определяются через lsn/lcn — по той же логике, что и кнопка
        "set serial='none' lcn" (_validate_and_build_serial_none_rows): из
        модельного lcn ('M9.6.5') находятся все поезда нужного типа и их lcn
        активов, затем среди них ищутся реально существующие активы. Строк,
        для которых актива по lcn не нашлось, просто не будет в результате —
        не ошибка (позиция могла быть уже снята с части поездов).

        Склад/партия/пользователь — общие для всего файла (заданы один раз в
        модалке). set_nocm — флажок "Установить позицию ТМЦ = 'NOCM'": если
        включён, id_design_number всех перемещаемых активов резолвится по
        design_number.number == 'NOCM' и дополнительно проставляется в UPDATE.
        Возвращает (errors, valid_rows, id_storage, id_consignment, id_user, id_design_number).
        """
        errors: list[dict] = []
        valid_rows: list[dict] = []

        storage_name = storage_name.strip()
        consignment_name = consignment_name.strip()
        user_fullname = user_fullname.strip()

        id_storage: int | None = None
        if not storage_name:
            errors.append({"row": 0, "field": "Склад", "message": "Поле 'Склад' пустое"})
        else:
            id_storage = await db_session.scalar(select(Storage.id).where(Storage.name == storage_name))
            if id_storage is None:
                errors.append({"row": 0, "field": "Склад", "message": f"Склад не найден: '{storage_name}'"})

        id_consignment: int | None = None
        if not consignment_name:
            errors.append({"row": 0, "field": "Партия", "message": "Поле 'Партия' пустое"})
        else:
            id_consignment = await db_session.scalar(select(Consignment.id).where(Consignment.name == consignment_name))
            if id_consignment is None:
                errors.append({"row": 0, "field": "Партия", "message": f"Партия не найдена: '{consignment_name}'"})

        id_user: int | None = None
        if not user_fullname:
            errors.append({"row": 0, "field": "Пользователь", "message": "Поле 'Пользователь' пустое"})
        else:
            id_user = await self._resolve_user_by_fullname(db_session, user_fullname)
            if id_user is None:
                errors.append({"row": 0, "field": "Пользователь", "message": f"Пользователь не найден: '{user_fullname}'"})

        id_design_number: int | None = None
        if set_nocm:
            id_design_number = await db_session.scalar(select(DesignNumber.id).where(DesignNumber.number == "NOCM"))
            if id_design_number is None:
                errors.append({"row": 0, "field": "ТМЦ", "message": "Позиция ТМЦ 'NOCM' не найдена"})

        if errors:
            return errors, valid_rows, id_storage, id_consignment, id_user, id_design_number

        lcn_errors, lcn_rows = await self._validate_and_build_serial_none_rows(db_session, rows, progress=progress)
        if lcn_errors:
            return lcn_errors, valid_rows, id_storage, id_consignment, id_user, id_design_number

        merged_lcns = self._merge_serial_none_lcns(lcn_rows)
        if not merged_lcns:
            errors.append({"row": 0, "field": "lcn", "message": "Не найдено ни одного lcn для перемещения"})
            return errors, valid_rows, id_storage, id_consignment, id_user, id_design_number

        stmt = text(
            "SELECT id, active_number, id_location FROM public.actives WHERE lcn::text IN :lcns"
        ).bindparams(bindparam("lcns", expanding=True))
        result = await db_session.execute(stmt, {"lcns": merged_lcns})
        for row_num, (id_active, active_number, id_location_old) in enumerate(result.all(), start=1):
            valid_rows.append({
                "row": row_num,
                "active_number": active_number,
                "id_active": id_active,
                "id_location_old": id_location_old,
            })

        return errors, valid_rows, id_storage, id_consignment, id_user, id_design_number

    @staticmethod
    def _build_move_actives_sql_body(
        valid_rows: list[dict], id_storage: int, id_consignment: int, id_user: int,
        reason: str, move_date: datetime, id_design_number: int | None = None,
    ) -> list[str]:
        """Строит DO-блок перемещения активов на склад (аналог move_active).

        Один destination-склад на весь батч (в отличие от исходного скрипта,
        где 'Куда' бралось по строкам) — упрощает счётчик lcn до одной
        переменной вместо словаря по id склада. lcn выдаётся из storage.last_lcn
        под FOR UPDATE и пишется обратно в конце DO-блока (в отличие от
        исходного move_active, который инкрементировал счётчик только в памяти
        Python и никогда не сохранял его обратно в БД — повторный запуск
        выдавал бы те же номера lcn повторно).

        id_design_number (флажок "Установить позицию ТМЦ = 'NOCM'") — если
        задан, дополнительно проставляется в том же UPDATE actives.
        """
        total = len(valid_rows)
        date_str = move_date.strftime("%Y-%m-%d %H:%M:%S")
        reason_val = f"'{sql_escape(reason)}'" if reason else "NULL"

        sql_lines: list[str] = ["DO $$", "DECLARE"]
        sql_lines.append(
            f"    loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') "
            f"FROM generate_series(1, {total}));"
        )
        sql_lines.append("    lcn_new bigint;")
        sql_lines.append("BEGIN")
        sql_lines.append(f"    SELECT last_lcn INTO lcn_new FROM public.storage WHERE id = {id_storage} FOR UPDATE;")

        for i, vr in enumerate(valid_rows, start=1):
            loc_ref = f"loc_ids[{i}]"
            old_loc_val = str(vr["id_location_old"]) if vr["id_location_old"] is not None else "NULL"
            sql_lines.append("    lcn_new := lcn_new + 1;")
            sql_lines.append(
                f"    INSERT INTO public.location (id, id_type_location, id_storage, id_consignment) "
                f"VALUES ({loc_ref}, 1, {id_storage}, {id_consignment});"
            )
            sql_lines.append(
                f"    INSERT INTO public.relocate (id_location_old, id_location_new, date, id_user, id_active, "
                f"reason, date_current, id_order) VALUES ({old_loc_val}, {loc_ref}, '{date_str}', {id_user}, "
                f"{vr['id_active']}, {reason_val}, '{date_str}', NULL);"
            )
            design_number_clause = f"id_design_number = {id_design_number}, " if id_design_number is not None else ""
            sql_lines.append(
                f"    UPDATE public.actives SET id_location = {loc_ref}, id_actves_parent = NULL, "
                f"id_actives_root = NULL, {design_number_clause}lcn = ('S{id_storage}.' || lcn_new)::ltree "
                f"WHERE id = {vr['id_active']};"
            )

        sql_lines.append(f"    UPDATE public.storage SET last_lcn = lcn_new WHERE id = {id_storage};")
        sql_lines.append("END $$;")
        return sql_lines

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
        try:
            errors, valid_rows = await self._validate_and_build_rows(db_session, rows)
        except Exception as e:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}]}),
                status_code=200,
                media_type="application/json",
            )

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
        try:
            errors, valid_rows = await self._validate_and_build_rows(db_session, rows)
        except Exception as e:
            return Response(
                content=json.dumps({"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}]}),
                status_code=200,
                media_type="application/json",
            )

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

    @post("/serial-none/generate-sql/start")
    async def serial_none_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Запускает фоновую генерацию SQL-файла 'set serial=none lcn', возвращает task_id для опроса прогресса."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        _cleanup_progress()
        task_id = uuid.uuid4().hex
        _progress[task_id] = {"processed": 0, "total": len(rows), "phase": "validating",
                               "status": "running", "created_at": time.time()}
        task = asyncio.ensure_future(self._run_serial_none_generate(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task
        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_serial_none_generate(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await self._validate_and_build_serial_none_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = self._build_serial_none_sql_lines(valid_rows)
        progress.update(status="done", sql="\n".join(sql_lines), count=len(valid_rows))

    @post("/serial-none/execute-sql/start")
    async def serial_none_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Запускает фоновое атомарное обновление serial_number='none', возвращает task_id для опроса прогресса."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        _cleanup_progress()
        task_id = uuid.uuid4().hex
        _progress[task_id] = {"processed": 0, "total": len(rows), "phase": "validating",
                               "status": "running", "created_at": time.time()}
        task = asyncio.ensure_future(self._run_serial_none_execute(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task
        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_serial_none_execute(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        total_updated = 0
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await self._validate_and_build_serial_none_rows(session, rows, progress=progress)
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}])
                    return

                # Одна общая UPDATE на объединённый (без дублей) список lcn всех
                # строк файла — вместо запроса на каждую строку, что при
                # одинаковых/пересекающихся lsn давало несколько идентичных
                # UPDATE подряд.
                progress.update(processed=0, total=1, phase="executing")
                try:
                    lcns = self._merge_serial_none_lcns(valid_rows)
                    stmt = text(
                        "UPDATE public.actives SET serial_number = 'none' WHERE lcn::text IN :lcns"
                    ).bindparams(bindparam("lcns", expanding=True))
                    result = await session.execute(stmt, {"lcns": lcns})
                    total_updated = result.rowcount
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
            f"=== Execute serial-none update: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows processed: {len(valid_rows)}, actives updated: {total_updated}",
            "",
            *self._build_serial_none_sql_lines(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"serial_none_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Serial-none updated for %d rows (%d actives), log: %s", len(valid_rows), total_updated, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Обновлено активов: {total_updated} (строк файла: {len(valid_rows)})")

    @post("/change-lcn/generate-sql/start")
    async def change_lcn_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Запускает фоновую генерацию SQL-файла 'изменить lcn в модели', возвращает task_id для опроса прогресса."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        _cleanup_progress()
        task_id = uuid.uuid4().hex
        _progress[task_id] = {"processed": 0, "total": len(rows), "phase": "validating",
                               "status": "running", "created_at": time.time()}
        task = asyncio.ensure_future(self._run_change_lcn_generate(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task
        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_change_lcn_generate(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await self._validate_and_build_change_lcn_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = self._build_change_lcn_sql_lines(valid_rows)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/change-lcn/execute-sql/start")
    async def change_lcn_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Запускает фоновое атомарное изменение lcn у активов, возвращает task_id для опроса прогресса."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        _cleanup_progress()
        task_id = uuid.uuid4().hex
        _progress[task_id] = {"processed": 0, "total": len(rows), "phase": "validating",
                               "status": "running", "created_at": time.time()}
        task = asyncio.ensure_future(self._run_change_lcn_execute(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task
        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_change_lcn_execute(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        total_updated = 0
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await self._validate_and_build_change_lcn_rows(session, rows, progress=progress)
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
                    # Двухфазный UPDATE (см. _build_change_lcn_sql_lines) — оба шага
                    # обязаны выполниться в одной транзакции, иначе после первого шага
                    # активы временно останутся с 'Z'-префиксом в lcn.
                    sql_lines = self._build_change_lcn_sql_lines(valid_rows)
                    await session.execute(text(sql_lines[0]))
                    result = await session.execute(text(sql_lines[1]))
                    total_updated = result.rowcount
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
            f"=== Execute change-lcn update: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Pairs processed: {len(valid_rows)}, actives updated: {total_updated}",
            "",
            *self._build_change_lcn_sql_lines(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"change_lcn_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Changed lcn for %d pairs (%d actives), log: %s", len(valid_rows), total_updated, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Изменено lcn у активов: {total_updated} (пар: {len(valid_rows)})")

    @post("/move-no-relocate/generate-sql/start")
    async def move_no_relocate_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Запускает фоновую генерацию SQL-файла 'Переместить активы без relocate', возвращает task_id."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        _cleanup_progress()
        task_id = uuid.uuid4().hex
        _progress[task_id] = {"processed": 0, "total": len(rows), "phase": "validating",
                               "status": "running", "created_at": time.time()}
        task = asyncio.ensure_future(self._run_move_no_relocate_generate(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task
        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_move_no_relocate_generate(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                # Та же валидация/парсинг пар lcn, что и у 'Изменить lcn в модели'
                # (Старый lsn -> lsn, обе колонки резолвятся как в 'set serial=none lcn').
                errors, valid_rows = await self._validate_and_build_change_lcn_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = self._build_move_no_relocate_sql_lines(valid_rows)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/move-no-relocate/execute-sql/start")
    async def move_no_relocate_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Запускает фоновое атомарное перемещение активов без relocate, возвращает task_id."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        _cleanup_progress()
        task_id = uuid.uuid4().hex
        _progress[task_id] = {"processed": 0, "total": len(rows), "phase": "validating",
                               "status": "running", "created_at": time.time()}
        task = asyncio.ensure_future(self._run_move_no_relocate_execute(task_id, rows))
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task
        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_move_no_relocate_execute(self, task_id: str, rows: list[dict]) -> None:
        progress = _progress[task_id]
        total_updated = 0
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await self._validate_and_build_change_lcn_rows(session, rows, progress=progress)
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для перемещения"}])
                    return

                progress.update(processed=0, total=1, phase="executing")
                try:
                    # Двухфазный UPDATE, как в change-lcn, но дополнительно сбрасывает
                    # id_actves_parent/id_actives_root; id_location и relocate не трогаются.
                    sql_lines = self._build_move_no_relocate_sql_lines(valid_rows)
                    await session.execute(text(sql_lines[0]))
                    result = await session.execute(text(sql_lines[1]))
                    total_updated = result.rowcount
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
            f"=== Execute move-no-relocate update: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Pairs processed: {len(valid_rows)}, actives updated: {total_updated}",
            "",
            *self._build_move_no_relocate_sql_lines(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"move_no_relocate_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Moved (no relocate) %d pairs (%d actives), log: %s", len(valid_rows), total_updated, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Перемещено активов: {total_updated} (пар: {len(valid_rows)})")

    @post("/move-actives/generate-sql/start")
    async def move_actives_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Запускает фоновую генерацию SQL-файла перемещения активов, возвращает task_id для опроса прогресса."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        reason = str(data.get("reason", "") or "")
        storage_name = str(data.get("storage_name", "") or "")
        consignment_name = str(data.get("consignment_name", "") or "")
        user_fullname = str(data.get("user_fullname", "") or "")
        set_nocm = str(data.get("set_nocm", "") or "").strip().lower() in ("1", "true", "on")

        _cleanup_progress()
        task_id = uuid.uuid4().hex
        _progress[task_id] = {"processed": 0, "total": len(rows), "phase": "validating",
                               "status": "running", "created_at": time.time()}
        task = asyncio.ensure_future(
            self._run_move_actives_generate(task_id, rows, reason, storage_name, consignment_name, user_fullname, set_nocm)
        )
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task
        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_move_actives_generate(
        self, task_id: str, rows: list[dict],
        reason: str, storage_name: str, consignment_name: str, user_fullname: str, set_nocm: bool,
    ) -> None:
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows, id_storage, id_consignment, id_user, id_design_number = await self._validate_and_build_move_rows(
                    session, rows, storage_name, consignment_name, user_fullname, set_nocm, progress=progress,
                )
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для перемещения"}])
            return

        move_date = datetime.now() - MOVE_TZ_SHIFT
        sql_lines = self._build_move_actives_sql_body(
            valid_rows, id_storage, id_consignment, id_user, reason, move_date, id_design_number,
        )
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/move-actives/execute-sql/start")
    async def move_actives_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Запускает фоновое атомарное перемещение активов, возвращает task_id для опроса прогресса."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        reason = str(data.get("reason", "") or "")
        storage_name = str(data.get("storage_name", "") or "")
        consignment_name = str(data.get("consignment_name", "") or "")
        user_fullname = str(data.get("user_fullname", "") or "")
        set_nocm = str(data.get("set_nocm", "") or "").strip().lower() in ("1", "true", "on")

        _cleanup_progress()
        task_id = uuid.uuid4().hex
        _progress[task_id] = {"processed": 0, "total": len(rows), "phase": "validating",
                               "status": "running", "created_at": time.time()}
        task = asyncio.ensure_future(
            self._run_move_actives_execute(task_id, rows, reason, storage_name, consignment_name, user_fullname, set_nocm)
        )
        task.add_done_callback(lambda t: _tasks.pop(task_id, None))
        _tasks[task_id] = task
        return Response(
            content=json.dumps({"task_id": task_id}),
            status_code=200,
            media_type="application/json",
        )

    async def _run_move_actives_execute(
        self, task_id: str, rows: list[dict],
        reason: str, storage_name: str, consignment_name: str, user_fullname: str, set_nocm: bool,
    ) -> None:
        progress = _progress[task_id]
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows, id_storage, id_consignment, id_user, id_design_number = await self._validate_and_build_move_rows(
                        session, rows, storage_name, consignment_name, user_fullname, set_nocm, progress=progress,
                    )
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для перемещения"}])
                    return

                progress.update(processed=0, total=1, phase="executing")
                move_date = datetime.now() - MOVE_TZ_SHIFT
                sql_body = "\n".join(
                    self._build_move_actives_sql_body(
                        valid_rows, id_storage, id_consignment, id_user, reason, move_date, id_design_number,
                    )
                )
                try:
                    # DO $$ ... $$ с несколькими операторами внутри нельзя выполнить
                    # через обычный execute() (asyncpg не готовит несколько команд в
                    # одном prepared statement) — тот же приём, что и в create_actives/
                    # create_named_actives: сырое соединение, session.rollback() ниже
                    # откатывает и его, т.к. сессия уже открыла транзакцию раньше
                    # (запросы валидации).
                    conn = await session.connection()
                    raw_conn = await conn.get_raw_connection()
                    await raw_conn.driver_connection.execute(sql_body)
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
            f"=== Execute move-actives: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Storage={storage_name} (id={id_storage}), Consignment={consignment_name} (id={id_consignment}), "
            f"User={user_fullname} (id={id_user})",
            f"Rows processed: {len(valid_rows)}",
            "",
            sql_body,
            "",
        ]
        log_file = LOG_DIR / f"move_actives_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Moved %d actives to storage '%s', log: %s", len(valid_rows), storage_name, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Перемещено активов: {len(valid_rows)} (склад: {storage_name})")

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
