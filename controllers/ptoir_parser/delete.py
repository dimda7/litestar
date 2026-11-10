from litestar import Controller, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from models import Ptoir
from sql_builders import ptoir as ptoir_sql

from .common import PATH, cell_numbers, find_column, load_ptoirs, logger, match_rows


async def validate_delete_rows(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'удалить ПТОиР'.

    Unlike the other operations a row error does not block the file: valid_rows
    holds every found ПТОиР (once per number) with the order_number of each
    order that will be detached from it.
    """
    ptoir_column = find_column(rows, "птоир")
    if rows and ptoir_column is None:
        return [{"row": 0, "field": "ПТОиР", "message": "В файле не найдена колонка 'ПТОиР'"}], []

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    numbers = cell_numbers(rows, ptoir_column)
    ptoirs = await load_ptoirs(db_session, Ptoir.number_ptoir, list(dict.fromkeys(n for n in numbers if n)))
    errors, matched = match_rows(numbers, ptoirs, "ПТОиР")

    if progress is not None:
        progress["processed"] = len(rows)
    return errors, [ptoirs[number][0] for number in matched]


def _delete_summary(total: int, valid_rows: list[dict], errors: list[dict]) -> tuple[str, list[str]]:
    order_numbers = [number for vr in valid_rows for number in vr["order_numbers"]]
    return (
        f"Удалено ПТОиР: {len(valid_rows)}, отвязано ЗН: {len(order_numbers)}, пропущено строк: {len(errors)}",
        [
            f"Rows in file: {total}, ptoir deleted: {len(valid_rows)}, "
            f"orders detached: {len(order_numbers)}, rows skipped: {len(errors)}",
            "",
            "Orders with id_ptoir set to NULL:",
            *order_numbers,
        ],
    )


DELETE_PTOIR = sql_tasks.SqlOperation(
    validate=validate_delete_rows,
    build=ptoir_sql.delete,
    log_name="delete_ptoir",
    log_title="PTOIR delete",
    summary=_delete_summary,
    empty_message="Нет ПТОиР для удаления",
    logger=logger,
    partial=True,
)


class DeletePtoirController(Controller):
    path = PATH

    @post("/delete/generate-sql/start")
    async def generate_sql_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        return sql_tasks.start_generate(data, DELETE_PTOIR)

    @post("/delete/execute-sql/start")
    async def execute_sql_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        return sql_tasks.start_execute(data, DELETE_PTOIR)
