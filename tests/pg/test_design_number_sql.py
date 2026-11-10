"""sql_builders/design_number.rename executed against a real PostgreSQL."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.design_number_parser.common import snapshot_design_numbers
from controllers.design_number_parser.create import validate_create_rows
from controllers.design_number_parser.rename import validate_rename_rows
from controllers.design_number_parser.update import validate_update_rows
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


UPDATE_COLUMNS = (
    "description, id_unit_type, is_serial, is_created_1c, id_commodity_group, id_commodity_code, "
    "id_design_number_status, id_measure_type, is_repairable, id_product_group, description_1c, main_number, "
    "id_counter_group, is_serial_1c"
)


async def state_of(session: AsyncSession, id_design_number: int) -> tuple:
    row = (
        await session.execute(
            text(f"SELECT {UPDATE_COLUMNS} FROM public.design_number WHERE id = :id"), {"id": id_design_number}
        )
    ).one()
    links = (
        (
            await session.execute(
                text(
                    "SELECT m.name FROM public.design_number_to_maint_use l JOIN public.maint_use m ON m.id = l.id_maint_use "
                    "WHERE l.id_design_number = :id ORDER BY m.name"
                ),
                {"id": id_design_number},
            )
        )
        .scalars()
        .all()
    )
    return tuple(row), links


async def link_maint_use(session: AsyncSession, id_design_number: int, name: str) -> None:
    await session.execute(
        text(
            "INSERT INTO public.design_number_to_maint_use (id_design_number, id_maint_use) "
            "SELECT :id, id FROM public.maint_use WHERE name = :name"
        ),
        {"id": id_design_number, "name": name},
    )


def full_row(number: str, **overrides) -> dict:
    row = {
        "number": number,
        "description": "NULL",
        "unit_type": "NULL",
        "is_serial": "False",
        "is_created_1c": "NULL",
        "commodity_group": "NULL",
        "commodity_code": "NULL",
        "design_number_status": "NULL",
        "measure_type": "NULL",
        "is_repairable": "NULL",
        "product_group": "NULL",
        "description_1c": "NULL",
        "main_number": "NULL",
        "counter_group": "NULL",
        "maint_use": "NULL",
        "is_serial_1c": "False",
    }
    row.update(overrides)
    return row


async def test_update_full_overwrites_columns_and_syncs_maint_use(pg_session):
    filled = await make_design_number(pg_session, f"{TEST_PREFIX}-UPD-1")
    cleared = await make_design_number(pg_session, f"{TEST_PREFIX}-UPD-2")
    await link_maint_use(pg_session, filled, "CM")
    await link_maint_use(pg_session, filled, "REV")
    await link_maint_use(pg_session, cleared, "CM")
    rows = [
        full_row(
            f"{TEST_PREFIX}-UPD-1",
            description="Ось 'A' \"Б\"",
            is_serial="True",
            is_created_1c="True",
            measure_type="шт",
            is_repairable="False",
            description_1c="ОСЬ",
            main_number="A2V0001",
            counter_group="KM",
            maint_use="CM,TOOLS",
            is_serial_1c="True",
        ),
        full_row(f"{TEST_PREFIX}-UPD-2"),
    ]

    errors, valid_rows = await validate_update_rows(pg_session, rows)
    await run_generated_sql(pg_session, "\n".join(design_number_sql.update_full(valid_rows)))

    assert errors == []
    (description, _, is_serial, is_created_1c, *_, main_number, id_counter_group, is_serial_1c), links = await state_of(
        pg_session, filled
    )
    assert (description, is_serial, is_created_1c, main_number, is_serial_1c) == (
        "Ось 'A' \"Б\"",
        True,
        True,
        "A2V0001",
        True,
    )
    assert id_counter_group is not None
    assert links == ["CM", "TOOLS"]
    assert await state_of(pg_session, cleared) == ((None,) * 2 + (False, None) + (None,) * 9 + (False,), [])


async def test_update_full_of_the_snapshot_rolls_the_update_back(pg_session):
    id_dn = await make_design_number(pg_session, f"{TEST_PREFIX}-UPD-3")
    await pg_session.execute(
        text(
            "UPDATE public.design_number SET description = 'old ''desc''', is_created_1c = true, main_number = 'M-1' "
            "WHERE id = :id"
        ),
        {"id": id_dn},
    )
    await link_maint_use(pg_session, id_dn, "REV")
    before = await state_of(pg_session, id_dn)

    snapshot = await snapshot_design_numbers(pg_session, [id_dn])
    _, valid_rows = await validate_update_rows(
        pg_session, [full_row(f"{TEST_PREFIX}-UPD-3", description="new", maint_use="CM", is_serial="True")]
    )
    await run_generated_sql(pg_session, "\n".join(design_number_sql.update_full(valid_rows)))
    assert await state_of(pg_session, id_dn) != before

    await run_generated_sql(pg_session, "\n".join(design_number_sql.update_full(snapshot)))

    assert await state_of(pg_session, id_dn) == before


async def id_of(session: AsyncSession, number: str) -> int | None:
    return await session.scalar(text("SELECT id FROM public.design_number WHERE number = :n"), {"n": number})


async def test_create_inserts_numbers_with_links_and_the_rollback_removes_them(pg_session):
    max_id = await pg_session.scalar(text("SELECT max(id) FROM public.design_number"))
    rows = [
        full_row(
            f"{TEST_PREFIX}-NEW-1",
            description="Ось 'A' \"Б\"",
            is_serial="True",
            measure_type="шт",
            is_repairable="False",
            counter_group="KM",
            maint_use="CM,TOOLS",
            is_serial_1c="True",
        ),
        full_row(f"{TEST_PREFIX}-NEW-2"),
    ]

    errors, valid_rows = await validate_create_rows(pg_session, rows)
    await run_generated_sql(pg_session, "\n".join(design_number_sql.create(valid_rows)))

    assert errors == []
    first, second = await id_of(pg_session, f"{TEST_PREFIX}-NEW-1"), await id_of(pg_session, f"{TEST_PREFIX}-NEW-2")
    assert first > max_id and second > max_id
    (
        (
            description,
            _,
            is_serial,
            _,
            _,
            _,
            _,
            id_measure_type,
            is_repairable,
            _,
            _,
            _,
            id_counter_group,
            is_serial_1c,
        ),
        links,
    ) = await state_of(pg_session, first)
    assert (description, is_serial, is_repairable, is_serial_1c, links) == (
        "Ось 'A' \"Б\"",
        True,
        False,
        True,
        ["CM", "TOOLS"],
    )
    assert id_measure_type == await pg_session.scalar(text("SELECT max(id) FROM public.measure_type WHERE name = 'шт'"))
    assert id_counter_group == await pg_session.scalar(text("SELECT id FROM public.counter_group WHERE name = 'KM'"))
    assert await state_of(pg_session, second) == ((None,) * 2 + (False, None) + (None,) * 9 + (False,), [])

    await run_generated_sql(pg_session, "\n".join(design_number_sql.delete_created(valid_rows)))

    assert await id_of(pg_session, f"{TEST_PREFIX}-NEW-1") is None
    assert await id_of(pg_session, f"{TEST_PREFIX}-NEW-2") is None
    assert (
        await pg_session.scalar(
            text("SELECT count(*) FROM public.design_number_to_maint_use WHERE id_design_number = :id"), {"id": first}
        )
        == 0
    )


async def test_create_updates_existing_numbers_and_the_rollback_restores_them(pg_session):
    id_existing = await make_design_number(pg_session, f"{TEST_PREFIX}-OLD-1")
    await pg_session.execute(
        text("UPDATE public.design_number SET description = 'old', main_number = 'M-1' WHERE id = :id"),
        {"id": id_existing},
    )
    await link_maint_use(pg_session, id_existing, "REV")
    before = await state_of(pg_session, id_existing)
    rows = [
        full_row(f"{TEST_PREFIX}-OLD-1", description="new", maint_use="CM", is_serial="True"),
        full_row(f"{TEST_PREFIX}-NEW-3", description="created"),
    ]

    errors, valid_rows = await validate_create_rows(pg_session, rows)
    snapshot = await snapshot_design_numbers(pg_session, [r.id for r in valid_rows if r.id is not None])
    await run_generated_sql(pg_session, "\n".join(design_number_sql.create_or_update(valid_rows)))

    assert errors == []
    (description, *_), links = await state_of(pg_session, id_existing)
    assert (description, links) == ("new", ["CM"])
    assert await id_of(pg_session, f"{TEST_PREFIX}-NEW-3") is not None

    await run_generated_sql(pg_session, "\n".join(design_number_sql.undo_create_or_update(valid_rows, snapshot)))

    assert await state_of(pg_session, id_existing) == before
    assert await id_of(pg_session, f"{TEST_PREFIX}-NEW-3") is None
