from litestar import Controller, post
from litestar.params import MultipartBody
from litestar.response import Response
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from models import Techcard, TechcardWorkOperation
from sql_builders import techcards as techcards_sql

from .common import (
    PATH,
    RowError,
    TechcardRepository,
    TechcardWorkOperationRepository,
    cell,
    header_columns,
    labelled_cell,
    missing_columns,
    one_id,
    operation,
    parse_int,
    require,
    resolve_rows,
)

HEAD_COLUMN = "Техкарта"
TASK_COLUMN = "Номер операции в техкарте"
TASK_NAME_COLUMN = "Наименование операции"
NESTED_COLUMN = "Вложенная техкарта"
NESTED_DESCRIPTION_COLUMN = "Описание вложенной техкарты"

EXPECTED_COLUMNS = (HEAD_COLUMN, TASK_COLUMN, TASK_NAME_COLUMN, NESTED_COLUMN, NESTED_DESCRIPTION_COLUMN)

TASK_NAME_MAX_LENGTH = 256
MAX_INTEGER = 2_147_483_647


async def validate_operation_rows(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    columns = header_columns(rows)
    missing = missing_columns(rows, columns, EXPECTED_COLUMNS)
    if missing:
        return missing, []

    techcards = TechcardRepository(session=db_session)
    operations = TechcardWorkOperationRepository(session=db_session)
    head_has_operations: dict[int, bool] = {}
    batch_tasks: set[tuple[int, int]] = set()

    async def resolve(row: dict) -> dict | None:
        # A row naming only the techcard marks a techcard without operations.
        if not any(cell(row, columns, name) for name in EXPECTED_COLUMNS if name != HEAD_COLUMN):
            return None
        require(row, columns, (HEAD_COLUMN, TASK_COLUMN, TASK_NAME_COLUMN))

        field, head = labelled_cell(row, columns, HEAD_COLUMN)
        found = await techcards.get_many(Techcard.name == head)
        if not found:
            raise RowError(field, f"Техкарта '{head}' не найдена — сначала выполните «Создать техкарты»")
        if len(found) > 1:
            raise RowError(field, f"Техкарта '{head}' найдена в базе {len(found)} раз")
        id_head = found[0].id
        if id_head not in head_has_operations:
            head_has_operations[id_head] = await operations.exists(TechcardWorkOperation.id_techcard_head == id_head)
        if head_has_operations[id_head]:
            raise RowError(field, f"У техкарты '{head}' уже есть операции в базе")

        field, value = labelled_cell(row, columns, TASK_COLUMN)
        task = parse_int(field, value)
        if not 0 < task <= MAX_INTEGER:
            raise RowError(field, f"{field}: ожидается целое число больше нуля, а не '{value}'")
        if (id_head, task) in batch_tasks:
            raise RowError(field, f"Операция {task} техкарты '{head}' уже встречается в файле")

        field, task_name = labelled_cell(row, columns, TASK_NAME_COLUMN)
        if len(task_name) > TASK_NAME_MAX_LENGTH:
            raise RowError(field, f"{field}: длиннее {TASK_NAME_MAX_LENGTH} символов ({len(task_name)})")

        id_nested = None
        field, nested = labelled_cell(row, columns, NESTED_COLUMN)
        description_field, description = labelled_cell(row, columns, NESTED_DESCRIPTION_COLUMN)
        if not nested and description:
            raise RowError(description_field, f"{description_field} заполнено, а '{field}' пустое")
        if nested:
            if nested == head:
                raise RowError(field, f"Техкарта '{head}' не может быть вложенной сама в себя")
            id_nested = await one_id(techcards, field, nested, Techcard.name == nested)
            db_description = (await techcards.get(id_nested)).description or ""
            if description and description != db_description.strip():
                raise RowError(
                    description_field,
                    f"{description_field}: '{description}' не совпадает с описанием "
                    f"техкарты '{nested}' в базе: '{db_description}'",
                )

        batch_tasks.add((id_head, task))
        return {"id_techcard_head": id_head, "task": task, "task_name": task_name, "id_techcard_early": id_nested}

    return await resolve_rows(rows, resolve, progress)


ADD_OPERATIONS = operation(
    validate_operation_rows, techcards_sql.add_operations, "add_techcard_operations", "Добавлено операций: {count}"
)


class AddOperationsController(Controller):
    path = PATH

    @post("/operations/generate-sql/start")
    async def generate_sql_start(self, data: MultipartBody[dict]) -> Response:
        return sql_tasks.start_generate(data, ADD_OPERATIONS)

    @post("/operations/execute-sql/start")
    async def execute_sql_start(self, data: MultipartBody[dict]) -> Response:
        return sql_tasks.start_execute(data, ADD_OPERATIONS)
