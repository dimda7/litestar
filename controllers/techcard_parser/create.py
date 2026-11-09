from litestar import Controller, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Classifier, ClassifierType, GroupType, Priorities, Specification, Techcard, TechcardStatusTypes, WorkType,
)
import sql_tasks
from sql_builders import techcards as techcards_sql

from .common import (
    PATH, ClassifierRepository, ClassifierTypeRepository, GroupTypeRepository, PrioritiesRepository, RowError,
    SpecificationRepository, TechcardRepository, TechcardStatusTypesRepository, WorkTypeRepository, cell,
    header_columns, labelled_cell, missing_columns, one_id, parse_int, require, resolve_rows,
    operation,
)

CLASSIFIER_TYPE_COLUMN = "Классификация техкарты"
MAIN_PTOIR_COLUMN = "Использовать техкарту только в БПТОиР?"
MAIN_PTOIR_VALUES = {"да": True, "нет": False, "": False}
REQUIRED_COLUMNS = ("Номер техкарты", "Название техкарты", "Тип работы")
# Every non-classifier column must be present, required or not: a
# missing 'Бригада' or БПТОиР flag would otherwise silently become NULL / false.
EXPECTED_COLUMNS = (*REQUIRED_COLUMNS, CLASSIFIER_TYPE_COLUMN, "Статус", "Полное описание", "Приоритет", "Бригада", MAIN_PTOIR_COLUMN)


async def validate_techcard_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    columns = header_columns(rows)
    missing = missing_columns(rows, columns, EXPECTED_COLUMNS)
    if missing:
        return missing, []
    # Any other column is a classifier. Not "the columns after 'Классификация
    # техкарты'": the page posts the rows through Jinja's tojson, which sorts the keys.
    known = {c.lower() for c in EXPECTED_COLUMNS}
    classifier_columns = [h for key, h in columns.items() if key not in known]

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
        require(row, columns, REQUIRED_COLUMNS)

        field, name = labelled_cell(row, columns, "Номер техкарты")
        if name in batch_names:
            raise RowError(field, f"Техкарта '{name}' уже встречается в файле")
        if await techcards.exists(Techcard.name == name):
            raise RowError(field, f"Техкарта '{name}' уже есть в базе")

        field, value = labelled_cell(row, columns, MAIN_PTOIR_COLUMN)
        if value.lower() not in MAIN_PTOIR_VALUES:
            raise RowError(field, f"{field}: ожидается 'Да', 'Нет' или пусто, а не '{value}'")
        use_only_main_ptoir = MAIN_PTOIR_VALUES[value.lower()]

        # An empty 'Классификация техкарты' is stored as NULL; its classifiers are
        # looked up within that type, so none of them may be filled then.
        field, value = labelled_cell(row, columns, CLASSIFIER_TYPE_COLUMN)
        id_classifier_type = None
        if value:
            id_classifier_type = await one_id(classifier_types, field, value, ClassifierType.name == value)
        else:
            filled = sorted(str(c).strip() for c in classifier_columns
                            if row.get(c) is not None and str(row[c]).strip())
            if filled:
                raise RowError(field, f"Поле '{field}' пустое, а классификаторы заполнены: {', '.join(filled)}")

        specification_ids = []
        for column in classifier_columns:
            value = "" if row.get(column) is None else str(row[column]).strip()
            if not value:
                continue
            field = str(column).strip()
            found = await classifiers.get_many(
                Classifier.name == field, Classifier.id_classifier_type == id_classifier_type)
            classifier_type = cell(row, columns, CLASSIFIER_TYPE_COLUMN)
            if not found:
                raise RowError(field, f"'{field}' не является классификатором типа '{classifier_type}'")
            if len(found) > 1:
                raise RowError(field, f"Классификатор '{field}' типа '{classifier_type}' "
                                       f"найден в базе {len(found)} раз")
            specification_ids.append(await one_id(
                specifications, field, value,
                Specification.id_classifier == found[0].id, Specification.description == value,
                Specification.is_delete.is_not(True)))

        field, value = labelled_cell(row, columns, "Тип работы")
        id_work_type = await one_id(work_types, field, value, WorkType.name == value)
        id_status_type = None
        field, value = labelled_cell(row, columns, "Статус")
        if value:
            id_status_type = await one_id(status_types, field, value, TechcardStatusTypes.status == value)

        id_priorities = None
        field, value = labelled_cell(row, columns, "Приоритет")
        if value:
            id_priorities = await one_id(priorities, field, value,
                                          Priorities.priority == parse_int(field, value))

        id_group_type = None
        field, value = labelled_cell(row, columns, "Бригада")
        if value:
            id_group_type = await one_id(group_types, field, value, GroupType.name == value)

        description_full = row.get(columns.get("полное описание", ""))
        if description_full is not None and not str(description_full).strip():
            description_full = None

        batch_names.add(name)
        return {
            "name": name,
            "description": cell(row, columns, "Название техкарты"),
            "description_full": None if description_full is None else str(description_full),
            "id_work_type": id_work_type,
            "id_priorities": id_priorities,
            "id_group_type": id_group_type,
            "use_only_main_ptoir": use_only_main_ptoir,
            "id_classifier_type": id_classifier_type,
            "id_status_type": id_status_type,
            "specification_ids": specification_ids,
        }

    return await resolve_rows(rows, resolve, progress)


CREATE_TECHCARDS = operation(validate_techcard_rows, techcards_sql.create,
                             "create_techcards", "Создано техкарт: {count}")


class CreateTechcardsController(Controller):
    path = PATH

    @post("/generate-sql/start")
    async def generate_sql_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        return sql_tasks.start_generate(data, CREATE_TECHCARDS)

    @post("/execute-sql/start")
    async def execute_sql_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        return sql_tasks.start_execute(data, CREATE_TECHCARDS)
