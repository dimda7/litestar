"""sql_builders/design_number.rename executed against a real PostgreSQL."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.design_number_parser import validate_rename_rows
from sql_builders import design_number as design_number_sql
from tests.pg.conftest import run_generated_sql
from tests.pg.factories import TEST_PREFIX, make_design_number


async def number_of(session: AsyncSession, id_design_number: int) -> str:
    return await session.scalar(
        text("SELECT number FROM public.design_number WHERE id = :id"), {"id": id_design_number}
    )


async def test_rename_keeps_the_id_and_fixes_invisible_characters(pg_session):
    spaced = await make_design_number(pg_session, f"{TEST_PREFIX}-DN-1 ")
    nbsp = await make_design_number(pg_session, f"{TEST_PREFIX}-DN_\xa0R1")
    quoted = await make_design_number(pg_session, f"{TEST_PREFIX}-DN'3")
    rows = [
        {"Старый ТМЦ": f"{TEST_PREFIX}-DN-1 ", "Новый ТМЦ": f"{TEST_PREFIX}-DN-1"},
        {"Старый ТМЦ": f"{TEST_PREFIX}-DN_\xa0R1", "Новый ТМЦ": f"{TEST_PREFIX}-DN_R1"},
        {"Старый ТМЦ": f"{TEST_PREFIX}-DN'3", "Новый ТМЦ": f"{TEST_PREFIX}-DN'3-new"},
    ]

    errors, valid_rows = await validate_rename_rows(pg_session, rows)
    await run_generated_sql(pg_session, "\n".join(design_number_sql.rename(valid_rows)))

    assert errors == []
    assert await number_of(pg_session, spaced) == f"{TEST_PREFIX}-DN-1"
    assert await number_of(pg_session, nbsp) == f"{TEST_PREFIX}-DN_R1"
    assert await number_of(pg_session, quoted) == f"{TEST_PREFIX}-DN'3-new"
