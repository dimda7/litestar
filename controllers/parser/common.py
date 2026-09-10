import re

from sqlalchemy.ext.asyncio import AsyncSession


PREFIX = "parser"


# A model lcn like 'M9.6.5': the digits after the letter prefix and before the
# first dot are id_train_type; the rest of the path is carried over as is.
_MODEL_LCN_RE = re.compile(r"^\D*(\d+)(?:\.(.*))?$")


def parse_model_lcn(lcn: str) -> tuple[int, str] | None:
    """Extract (id_train_type, rest_of_path) from an lcn like 'M9.6.5' -> (9, '6.5'); 'M9' -> (9, '')."""
    match = _MODEL_LCN_RE.match(lcn)
    if not match:
        return None
    return int(match.group(1)), match.group(2) or ""


async def execute_sql_lines(
    session: AsyncSession, sql_lines: list[str], progress: dict | None = None,
) -> tuple[list[str], int]:
    """Run a builder's statements one by one; return the car place names its
    INSERTs created (the only statements returning rows) and the last statement's rowcount.

    exec_driver_sql, not text(): a typed car place name is inlined into the SQL,
    and text() would read a ':1' in it as a bind parameter.
    """
    conn = await session.connection()
    created: list[str] = []
    rowcount = 0
    for i, line in enumerate(sql_lines, start=1):
        result = await conn.exec_driver_sql(line)
        if result.returns_rows:
            created.extend(result.scalars().all())
        rowcount = result.rowcount
        if progress is not None:
            progress["processed"] = i
    return created, rowcount
