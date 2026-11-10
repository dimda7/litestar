import logging
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from models import Orders, Ptoir

logger = logging.getLogger("ptoir_parser")

PREFIX = "ptoir_parser"
PATH = "/ptoir-parser"


def find_column(rows: list[dict], name: str) -> str | None:
    return next((k for k in (rows[0] if rows else {}) if str(k).strip().lower() == name), None)


# Bulk lookups go in chunks so a huge file stays under the driver's bind-parameter limit.
LOOKUP_CHUNK = 5000


def chunks(values: list) -> list[list]:
    return [values[start : start + LOOKUP_CHUNK] for start in range(0, len(values), LOOKUP_CHUNK)]


def cell_numbers(rows: list[dict], column: str | None) -> list[str]:
    return [str(row.get(column, "") or "").strip() for row in rows]


def match_rows(numbers: list[str], found: Collection[object], label: str) -> tuple[list[dict], list[str]]:
    """Row errors for empty and unknown numbers, and the found ones in file order, each once."""
    errors: list[dict] = []
    matched: list[str] = []
    seen: set[str] = set()
    for idx, number in enumerate(numbers):
        row_num = idx + 1
        if not number:
            errors.append({"row": row_num, "field": label, "message": f"Поле '{label}' пустое"})
            continue
        if number in seen:
            continue
        seen.add(number)
        if number not in found:
            errors.append({"row": row_num, "field": label, "message": f"{label} не найден: '{number}'"})
            continue
        matched.append(number)
    return errors, matched


async def load_ptoirs(
    db_session: AsyncSession,
    key: InstrumentedAttribute,
    values: list,
) -> dict[object, list[dict]]:
    """ПТОиР whose `key` column is in values, grouped by it, as ptoir_sql.delete takes them:
    id, number_ptoir and the order_number of each order that will be detached."""
    ptoirs: dict[object, list[dict]] = {}
    for chunk in chunks(values):
        result = await db_session.execute(
            select(key, Ptoir.id, Ptoir.number_ptoir).where(key.in_(chunk)).order_by(Ptoir.id)
        )
        for value, id_ptoir, number_ptoir in result.tuples():
            ptoirs.setdefault(value, []).append({"id": id_ptoir, "number_ptoir": number_ptoir, "order_numbers": []})

    by_id = {ptoir["id"]: ptoir for group in ptoirs.values() for ptoir in group}
    for chunk in chunks(list(by_id)):
        result = await db_session.execute(
            select(Orders.id_ptoir, Orders.order_number).where(Orders.id_ptoir.in_(chunk)).order_by(Orders.id)
        )
        for id_ptoir, order_number in result.tuples():
            by_id[id_ptoir]["order_numbers"].append(order_number)
    return ptoirs
