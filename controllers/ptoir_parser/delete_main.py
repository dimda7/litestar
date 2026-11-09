from litestar import Controller, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from models import MainPtoir, Ptoir
from sql_builders import ptoir as ptoir_sql

from .common import PATH, cell_numbers, chunks, find_column, load_ptoirs, logger, match_rows


async def validate_delete_main_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'удалить БПТОиР'.

    As for 'удалить ПТОиР' a row error does not block the file. valid_rows holds
    every found БПТОиР (once per number) with all its ПТОиР, in the shape
    ptoir_sql.delete takes them.
    """
    main_column = find_column(rows, "бптоир")
    if rows and main_column is None:
        return [{"row": 0, "field": "БПТОиР", "message": "В файле не найдена колонка 'БПТОиР'"}], []

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    numbers = cell_numbers(rows, main_column)
    found: dict[str, int] = {}
    for chunk in chunks(list(dict.fromkeys(n for n in numbers if n))):
        result = await db_session.execute(
            select(MainPtoir.number_ptoir, MainPtoir.id).where(MainPtoir.number_ptoir.in_(chunk)))
        found.update(result.tuples().all())
    errors, matched = match_rows(numbers, found, "БПТОиР")
    ptoirs = await load_ptoirs(db_session, Ptoir.id_main_ptoir, [found[number] for number in matched])

    if progress is not None:
        progress["processed"] = len(rows)
    return errors, [{"id": found[number], "number_ptoir": number, "ptoirs": ptoirs.get(found[number], [])}
                    for number in matched]


def _delete_main_summary(total: int, valid_rows: list[dict], errors: list[dict]) -> tuple[str, list[str]]:
    ptoirs = [ptoir for vr in valid_rows for ptoir in vr["ptoirs"]]
    order_numbers = [number for ptoir in ptoirs for number in ptoir["order_numbers"]]
    return (
        f"Удалено БПТОиР: {len(valid_rows)}, ПТОиР: {len(ptoirs)}, отвязано ЗН: {len(order_numbers)}, "
        f"пропущено строк: {len(errors)}",
        [
            f"Rows in file: {total}, main_ptoir deleted: {len(valid_rows)}, ptoir deleted: {len(ptoirs)}, "
            f"orders detached: {len(order_numbers)}, rows skipped: {len(errors)}",
            "",
            "Orders with id_ptoir set to NULL:",
            *order_numbers,
        ],
    )


DELETE_MAIN_PTOIR = sql_tasks.SqlOperation(
    validate=validate_delete_main_rows, build=ptoir_sql.delete_main,
    log_name="delete_main_ptoir", log_title="main PTOIR delete", summary=_delete_main_summary,
    empty_message="Нет БПТОиР для удаления", logger=logger, partial=True,
)


class DeleteMainPtoirController(Controller):
    path = PATH

    @post("/delete-main/generate-sql/start")
    async def generate_sql_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        return sql_tasks.start_generate(data, DELETE_MAIN_PTOIR)

    @post("/delete-main/execute-sql/start")
    async def execute_sql_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        return sql_tasks.start_execute(data, DELETE_MAIN_PTOIR)
