import logging

from litestar import Controller, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from sql_builders import models as models_sql

logger = logging.getLogger("parser")


def parse_delete_ids(rows: list[dict], progress: dict | None = None) -> tuple[list[dict], list[int]]:
    errors: list[dict] = []
    valid_ids: list[int] = []
    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")
    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None:
            progress["processed"] = row_num
        row_id = row.get("id")
        if not row_id:
            errors.append({"row": row_num, "field": "id", "message": "Поле 'id' отсутствует или пустое"})
            continue
        valid_ids.append(int(row_id))
    return errors, valid_ids


async def validate_delete_rows(
    session: AsyncSession, rows: list[dict], progress: dict | None = None
) -> tuple[list[dict], list[int]]:
    return parse_delete_ids(rows, progress=progress)


DELETE_MODELS = sql_tasks.SqlOperation(
    validate=validate_delete_rows,
    build=models_sql.delete_models,
    log_name="delete_models",
    log_title="delete models",
    summary=lambda total, valid_ids, errors, rowcounts: (
        f"Успешно удалено {len(valid_ids)} строк",
        [f"Rows deleted: {len(valid_ids)}"],
    ),
    empty_message="Нет валидных строк для удаления",
    logger=logger,
)


class DeleteModelsController(Controller):
    path = "/parser"

    @post("/delete-rows/start")
    async def delete_rows_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        """Start background generation of the models row deletion SQL file; returns a task_id to poll."""
        return sql_tasks.start_generate(data, DELETE_MODELS)

    @post("/execute-delete/start")
    async def execute_delete_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        """Start the background models row deletion in the database; returns a task_id to poll."""
        return sql_tasks.start_execute(data, DELETE_MODELS)
