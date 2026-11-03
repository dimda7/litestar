import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Redirect, Response, Template
from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

import excel_upload
from db_manager import get_session_maker
from models import (
    Classifier, ClassifierType, GroupType, Priorities, Specification, Techcard, TechcardStatusTypes, WorkType,
)
from parser_storage import LOG_DIR
from progress_tasks import progress_response, start_task
from schemas import SelectSheetRequest
from sql_builders import techcards as techcards_sql

logger = logging.getLogger("techcard_parser")

PREFIX = "techcard_parser"

CLASSIFIER_TYPE_COLUMN = "Классификация техкарты"
MAIN_PTOIR_COLUMN = "Использовать техкарту только в БПТОиР?"
MAIN_PTOIR_VALUES = {"да": True, "нет": False, "": False}
REQUIRED_COLUMNS = ("Номер техкарты", "Название техкарты", "Тип работы", "Статус", CLASSIFIER_TYPE_COLUMN)
# Every column before the classifier ones must be present, required or not: a
# missing 'Бригада' or БПТОиР flag would otherwise silently become NULL / false.
EXPECTED_COLUMNS = (*REQUIRED_COLUMNS, "Полное описание", "Приоритет", "Бригада", MAIN_PTOIR_COLUMN)


class WorkTypeRepository(SQLAlchemyAsyncRepository[WorkType]):
    model_type = WorkType


class GroupTypeRepository(SQLAlchemyAsyncRepository[GroupType]):
    model_type = GroupType


class PrioritiesRepository(SQLAlchemyAsyncRepository[Priorities]):
    model_type = Priorities


class ClassifierTypeRepository(SQLAlchemyAsyncRepository[ClassifierType]):
    model_type = ClassifierType


class ClassifierRepository(SQLAlchemyAsyncRepository[Classifier]):
    model_type = Classifier


class SpecificationRepository(SQLAlchemyAsyncRepository[Specification]):
    model_type = Specification


class TechcardStatusTypesRepository(SQLAlchemyAsyncRepository[TechcardStatusTypes]):
    model_type = TechcardStatusTypes


class TechcardRepository(SQLAlchemyAsyncRepository[Techcard]):
    model_type = Techcard


class _RowError(Exception):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def _cell(row: dict, columns: dict[str, str], name: str) -> str:
    value = row.get(columns.get(name.lower(), ""))
    return "" if value is None else str(value).strip()


def _label(columns: dict[str, str], name: str) -> str:
    return str(columns.get(name.lower(), name)).strip()


async def _one_id(repo: SQLAlchemyAsyncRepository, field: str, value: str, *filters: ColumnElement[bool]) -> int:
    found = await repo.get_many(*filters)
    if not found:
        raise _RowError(field, f"{field}: значение не найдено: '{value}'")
    if len(found) > 1:
        raise _RowError(field, f"{field}: значение '{value}' найдено в базе {len(found)} раз")
    return found[0].id


def _parse_priority(field: str, value: str) -> int:
    try:
        number = Decimal(value.replace(",", "."))
    except InvalidOperation:
        raise _RowError(field, f"{field}: некорректное значение: '{value}'") from None
    if not number.is_finite() or number != number.to_integral_value():
        raise _RowError(field, f"{field}: некорректное значение: '{value}'")
    return int(number)


async def validate_techcard_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    valid_rows: list[dict] = []

    headers = list(rows[0]) if rows else []
    columns = {str(h).strip().lower(): h for h in headers}
    missing = [c for c in EXPECTED_COLUMNS if rows and c.lower() not in columns]
    if missing:
        return [{"row": 0, "field": c, "message": f"В файле не найдена колонка '{c}'"} for c in missing], []
    classifier_columns = headers[headers.index(columns[CLASSIFIER_TYPE_COLUMN.lower()]) + 1:] if rows else []

    work_types = WorkTypeRepository(session=db_session)
    group_types = GroupTypeRepository(session=db_session)
    priorities = PrioritiesRepository(session=db_session)
    classifier_types = ClassifierTypeRepository(session=db_session)
    classifiers = ClassifierRepository(session=db_session)
    specifications = SpecificationRepository(session=db_session)
    status_types = TechcardStatusTypesRepository(session=db_session)
    techcards = TechcardRepository(session=db_session)
    batch_names: set[str] = set()

    async def resolve(row: dict) -> dict:
        empty = next((c for c in REQUIRED_COLUMNS if not _cell(row, columns, c)), None)
        if empty is not None:
            raise _RowError(_label(columns, empty), f"Поле '{_label(columns, empty)}' пустое")

        def labelled_cell(name: str) -> tuple[str, str]:
            return _label(columns, name), _cell(row, columns, name)

        field, name = labelled_cell("Номер техкарты")
        if name in batch_names:
            raise _RowError(field, f"Техкарта '{name}' уже встречается в файле")
        if await techcards.exists(Techcard.name == name):
            raise _RowError(field, f"Техкарта '{name}' уже есть в базе")

        field, value = labelled_cell(MAIN_PTOIR_COLUMN)
        if value.lower() not in MAIN_PTOIR_VALUES:
            raise _RowError(field, f"{field}: ожидается 'Да', 'Нет' или пусто, а не '{value}'")
        use_only_main_ptoir = MAIN_PTOIR_VALUES[value.lower()]

        field, value = labelled_cell(CLASSIFIER_TYPE_COLUMN)
        id_classifier_type = await _one_id(classifier_types, field, value, ClassifierType.name == value)

        specification_ids = []
        for column in classifier_columns:
            value = "" if row.get(column) is None else str(row[column]).strip()
            if not value:
                continue
            field = str(column).strip()
            found = await classifiers.get_many(
                Classifier.name == field, Classifier.id_classifier_type == id_classifier_type)
            classifier_type = _cell(row, columns, CLASSIFIER_TYPE_COLUMN)
            if not found:
                raise _RowError(field, f"'{field}' не является классификатором типа '{classifier_type}'")
            if len(found) > 1:
                raise _RowError(field, f"Классификатор '{field}' типа '{classifier_type}' "
                                       f"найден в базе {len(found)} раз")
            specification_ids.append(await _one_id(
                specifications, field, value,
                Specification.id_classifier == found[0].id, Specification.description == value,
                Specification.is_delete.is_not(True)))

        field, value = labelled_cell("Тип работы")
        id_work_type = await _one_id(work_types, field, value, WorkType.name == value)
        field, value = labelled_cell("Статус")
        id_status_type = await _one_id(status_types, field, value, TechcardStatusTypes.status == value)

        id_priorities = None
        field, value = labelled_cell("Приоритет")
        if value:
            id_priorities = await _one_id(priorities, field, value,
                                          Priorities.priority == _parse_priority(field, value))

        id_group_type = None
        field, value = labelled_cell("Бригада")
        if value:
            id_group_type = await _one_id(group_types, field, value, GroupType.name == value)

        description_full = row.get(columns.get("полное описание", ""))
        if description_full is not None and not str(description_full).strip():
            description_full = None

        return {
            "name": name,
            "description": _cell(row, columns, "Название техкарты"),
            "description_full": None if description_full is None else str(description_full),
            "id_work_type": id_work_type,
            "id_priorities": id_priorities,
            "id_group_type": id_group_type,
            "use_only_main_ptoir": use_only_main_ptoir,
            "id_classifier_type": id_classifier_type,
            "id_status_type": id_status_type,
            "specification_ids": specification_ids,
        }

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    for idx, row in enumerate(rows):
        if progress is not None and (idx % 20 == 0 or idx + 1 == len(rows)):
            progress["processed"] = idx + 1
        try:
            valid_row = await resolve(row)
            batch_names.add(valid_row["name"])
            valid_rows.append(valid_row)
        except _RowError as e:
            errors.append({"row": idx + 1, "field": e.field, "message": e.message})

    return errors, valid_rows


class TechcardParserController(Controller):
    path = "/techcard-parser"

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
        page = min(page, total_pages)
        offset = (page - 1) * per_page

        return Template(
            template_name="techcard_parser.html",
            context={
                "headers": headers,
                "rows": all_rows[offset:offset + per_page],
                "all_rows": all_rows,
                "filename": filename,
                "error": error,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "techcard_parser",
                "pending_sheets": pending_sheets,
                "pending_filename": pending_filename,
            },
        )

    @post("/upload")
    async def upload(self, request: Request) -> Redirect:
        return await excel_upload.handle_upload(request, PREFIX, "/techcard-parser")

    @post("/select-sheet")
    async def select_sheet(
        self,
        request: Request,
        data: SelectSheetRequest = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        return excel_upload.handle_sheet_choice(request, PREFIX, "/techcard-parser", data.sheet_name)

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
            async with get_session_maker()() as session:
                errors, valid_rows = await validate_techcard_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        if not valid_rows:
            progress.update(status="error",
                            errors=[{"row": 0, "field": "*", "message": "Нет строк для создания техкарт"}])
            return

        full_sql = "\n".join(["BEGIN;", *techcards_sql.create(valid_rows), "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

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
            async with get_session_maker()() as session:
                try:
                    errors, valid_rows = await validate_techcard_rows(session, rows, progress=progress)
                except Exception as e:
                    progress.update(status="error",
                                    errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error",
                                    errors=[{"row": 0, "field": "*", "message": "Нет строк для создания техкарт"}])
                    return

                progress.update(processed=0, total=1, phase="executing")
                sql_lines = techcards_sql.create(valid_rows)
                try:
                    # The raw asyncpg connection, as the tests run it: description_full is
                    # inlined HTML, and a ':1' in it must not be read as a bind parameter.
                    conn = await session.connection()
                    raw_conn = await conn.get_raw_connection()
                    await raw_conn.driver_connection.execute("\n".join(sql_lines))
                    progress["processed"] = 1
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    progress.update(status="error",
                                    errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
                    return
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
            return

        now = datetime.now()
        log_lines = [
            f"=== Execute techcard creation: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Techcards created: {len(valid_rows)}",
            "",
            *sql_lines,
            "",
        ]
        log_file = LOG_DIR / f"create_techcards_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Created %d techcards, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows), message=f"Создано техкарт: {len(valid_rows)}")

    @get("/progress/{task_id:str}")
    async def get_progress(self, task_id: str) -> Response:
        return progress_response(task_id)
