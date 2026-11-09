from litestar import Controller, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from models import Orders, Ptoir
from sql_builders import ptoir as ptoir_sql

from .common import PATH, find_column, logger

# Bulk lookups go in chunks so a huge file stays under the driver's bind-parameter limit.
LOOKUP_CHUNK = 5000


async def validate_delete_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'удалить ПТОиР'.

    Unlike the other operations a row error does not block the file: valid_rows
    holds every found ПТОиР (once per number) with the order_number of each
    order that will be detached from it.
    """
    errors: list[dict] = []
    valid_rows: list[dict] = []

    ptoir_column = find_column(rows, "птоир")
    if rows and ptoir_column is None:
        errors.append({"row": 0, "field": "ПТОиР", "message": "В файле не найдена колонка 'ПТОиР'"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    numbers = [str(row.get(ptoir_column, "") or "").strip() for row in rows]
    distinct_numbers = list(dict.fromkeys(n for n in numbers if n))
    found: dict[str, int] = {}
    for start in range(0, len(distinct_numbers), LOOKUP_CHUNK):
        chunk = distinct_numbers[start:start + LOOKUP_CHUNK]
        result = await db_session.execute(select(Ptoir.number_ptoir, Ptoir.id).where(Ptoir.number_ptoir.in_(chunk)))
        found.update(result.tuples().all())

    order_numbers: dict[int, list[str]] = {}
    found_ids = list(found.values())
    for start in range(0, len(found_ids), LOOKUP_CHUNK):
        result = await db_session.execute(
            select(Orders.id_ptoir, Orders.order_number)
            .where(Orders.id_ptoir.in_(found_ids[start:start + LOOKUP_CHUNK]))
            .order_by(Orders.id)
        )
        for id_ptoir, order_number in result.tuples():
            order_numbers.setdefault(id_ptoir, []).append(order_number)

    seen: set[str] = set()
    for idx, number_ptoir in enumerate(numbers):
        row_num = idx + 1
        if not number_ptoir:
            errors.append({"row": row_num, "field": "ПТОиР", "message": "Поле 'ПТОиР' пустое"})
            continue
        if number_ptoir in seen:
            continue
        seen.add(number_ptoir)
        if number_ptoir not in found:
            errors.append({"row": row_num, "field": "ПТОиР", "message": f"ПТОиР не найден: '{number_ptoir}'"})
            continue
        id_ptoir = found[number_ptoir]
        valid_rows.append({"id": id_ptoir, "number_ptoir": number_ptoir,
                           "order_numbers": order_numbers.get(id_ptoir, [])})

    if progress is not None:
        progress["processed"] = len(rows)
    return errors, valid_rows


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
    validate=validate_delete_rows, build=ptoir_sql.delete,
    log_name="delete_ptoir", log_title="PTOIR delete", summary=_delete_summary,
    empty_message="Нет ПТОиР для удаления", logger=logger, partial=True,
)


class DeletePtoirController(Controller):
    path = PATH

    @post("/delete/generate-sql/start")
    async def generate_sql_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        return sql_tasks.start_generate(data, DELETE_PTOIR)

    @post("/delete/execute-sql/start")
    async def execute_sql_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        return sql_tasks.start_execute(data, DELETE_PTOIR)
