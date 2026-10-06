"""'Изменение моточасов' against a real PostgreSQL copy.

SQLite has neither session_replication_role nor the PL/Python trigger the SQL
exists to bypass: counter_active_hours_jump takes a written value as a source
reading and sums it into the old one. The test first shows the trigger doing
that, then that the generated SQL writes the value as it is.

As with the mileage correction, the downloadable BEGIN; ... COMMIT; variant is
not run here — its COMMIT would end the fixture's transaction.
"""
from sqlalchemy import text

from controllers.ptoir_parser import validate_change_hours_rows
from sql_builders import ptoir as ptoir_sql
from tests.pg.conftest import run_generated_sql
from tests.pg.factories import TEST_PREFIX, make_active, make_train, reference_ids


async def counter_state(session, id_active: int) -> tuple[int, int]:
    row = (await session.execute(
        text("SELECT value, value_source FROM public.counter_active "
             "WHERE id_active = :active AND id_counter_type = :type"),
        {"active": id_active, "type": ptoir_sql.HOURS_COUNTER_TYPE_ID})).one()
    return row.value, row.value_source


async def test_value_is_written_past_the_hours_trigger(pg_session):
    # actives_trgger creates the hours counter for a design number whose counter group carries it
    id_design_number = await pg_session.scalar(text(
        "SELECT des.id FROM public.design_number des "
        "JOIN public.counter_type_to_group link ON link.id_counter_group = des.id_counter_group "
        "WHERE link.id_counter_type = :type ORDER BY des.id LIMIT 1"),
        {"type": ptoir_sql.HOURS_COUNTER_TYPE_ID})
    id_train = await make_train(pg_session, (await reference_ids(pg_session))["train_type"])
    id_active = await make_active(pg_session, lcn=f"{id_train}.1", id_design_number=id_design_number)
    number_ptoir = f"{TEST_PREFIX}-hours"
    await pg_session.execute(
        text("INSERT INTO public.ptoir (number_ptoir, id_active) VALUES (:number, :active)"),
        {"number": number_ptoir, "active": id_active})

    for reading in (1000, 400):
        await pg_session.execute(
            text("UPDATE public.counter_active SET value = :value "
                 "WHERE id_active = :active AND id_counter_type = :type"),
            {"value": reading, "active": id_active, "type": ptoir_sql.HOURS_COUNTER_TYPE_ID})
    inflated = await counter_state(pg_session, id_active)
    assert inflated == (1400, 400)

    errors, valid_rows = await validate_change_hours_rows(
        pg_session, [{"птоир": number_ptoir, "значение": "55,5"}])
    assert errors == []
    assert (valid_rows[0]["old_value"], valid_rows[0]["old_value_source"]) == inflated

    await run_generated_sql(pg_session, "\n".join(ptoir_sql.change_hours(valid_rows)))

    assert await counter_state(pg_session, id_active) == (555, 400)
    assert await pg_session.scalar(text("SHOW session_replication_role")) == "origin"
