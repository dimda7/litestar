"""sql_builders/actives.py executed against a real PostgreSQL — the DO $$ blocks
that hand out asset numbers and lcns from counters locked FOR UPDATE."""
import pytest
from sqlalchemy import text

from sql_builders import actives as actives_sql
from sql_builders.actives import ACTIVE_NUMBER_COUNTER_DESCRIPTION, ACTIVE_NUMBER_LENGTH, MILEAGE_COUNTER_TYPE_ID
from tests.pg.conftest import run_generated_sql
from tests.pg.factories import TEST_PREFIX, make_active, make_location, make_train, reference_ids

TYPE_ACTIVE = "PG"


async def counter_value(session) -> int:
    return await session.scalar(
        text("SELECT number FROM public.iterator_number_last WHERE description = :d"),
        {"d": ACTIVE_NUMBER_COUNTER_DESCRIPTION})


async def last_lcn(session, id_storage: int) -> int:
    return await session.scalar(text("SELECT last_lcn FROM public.storage WHERE id = :id"), {"id": id_storage})


def expected_number(counter: int) -> str:
    return TYPE_ACTIVE + str(counter).rjust(ACTIVE_NUMBER_LENGTH - len(TYPE_ACTIVE), "0")


async def test_create_actives_numbers_assets_from_the_counter(pg_session):
    ids = await reference_ids(pg_session)
    counter_before = await counter_value(pg_session)
    lcn_before = await last_lcn(pg_session, ids["storage"])
    rows = [
        {"id_storage": ids["storage"], "id_storage_place": None, "id_consignment": ids["consignment"],
         "serial_number": "sn-1", "special_account": None, "type_active": TYPE_ACTIVE,
         "id_design_number": ids["design_number"]},
        {"id_storage": ids["storage"], "id_storage_place": None, "id_consignment": ids["consignment"],
         "serial_number": None, "special_account": "acc", "type_active": TYPE_ACTIVE,
         "id_design_number": ids["design_number"]},
    ]

    await run_generated_sql(pg_session, "\n".join(actives_sql.create_actives(rows)))

    numbers = [expected_number(counter_before + 1), expected_number(counter_before + 2)]
    created = (await pg_session.execute(
        text("SELECT a.active_number, a.serial_number, a.special_account, a.lcn::text AS lcn, "
             "       l.id_type_location, l.id_storage, l.id_consignment "
             "FROM public.actives a JOIN public.location l ON l.id = a.id_location "
             "WHERE a.active_number = ANY(:nums) ORDER BY a.active_number"),
        {"nums": numbers})).all()

    assert [c.active_number for c in created] == numbers
    assert [c.serial_number for c in created] == ["sn-1", None]
    assert [c.special_account for c in created] == [None, "acc"]
    assert [c.lcn for c in created] == [f"S{ids['storage']}.{lcn_before + 1}", f"S{ids['storage']}.{lcn_before + 2}"]
    assert {(c.id_type_location, c.id_storage, c.id_consignment) for c in created} == {
        (1, ids["storage"], ids["consignment"])}


async def test_create_actives_advances_both_counters(pg_session):
    ids = await reference_ids(pg_session)
    counter_before = await counter_value(pg_session)
    lcn_before = await last_lcn(pg_session, ids["storage"])
    rows = [{"id_storage": ids["storage"], "id_storage_place": None, "id_consignment": ids["consignment"],
             "serial_number": None, "special_account": None, "type_active": TYPE_ACTIVE,
             "id_design_number": ids["design_number"]}] * 3

    await run_generated_sql(pg_session, "\n".join(actives_sql.create_actives(rows)))

    assert await counter_value(pg_session) == counter_before + 3
    assert await last_lcn(pg_session, ids["storage"]) == lcn_before + 3


async def test_create_actives_raises_when_the_counter_row_is_missing(pg_session):
    ids = await reference_ids(pg_session)
    rows = [{"id_storage": ids["storage"], "id_storage_place": None, "id_consignment": ids["consignment"],
             "serial_number": None, "special_account": None, "type_active": TYPE_ACTIVE,
             "id_design_number": ids["design_number"]}]

    with pytest.raises(Exception) as excinfo:
        async with pg_session.begin_nested():
            await pg_session.execute(
                text("DELETE FROM public.iterator_number_last WHERE description = :d"),
                {"d": ACTIVE_NUMBER_COUNTER_DESCRIPTION})
            await run_generated_sql(pg_session, "\n".join(actives_sql.create_actives(rows)))

    assert ACTIVE_NUMBER_COUNTER_DESCRIPTION in str(excinfo.value)


async def test_create_named_actives_takes_the_number_from_the_file(pg_session):
    ids = await reference_ids(pg_session)
    lcn_before = await last_lcn(pg_session, ids["storage"])
    counter_before = await counter_value(pg_session)
    number = f"{TEST_PREFIX}-named-{lcn_before}"
    rows = [{"id_storage": ids["storage"], "id_consignment": ids["consignment"],
             "active_number": number, "id_design_number": ids["design_number"]}]

    await run_generated_sql(pg_session, "\n".join(actives_sql.create_named_actives(rows)))

    created = (await pg_session.execute(
        text("SELECT lcn::text AS lcn FROM public.actives WHERE active_number = :n"), {"n": number})).first()
    assert created.lcn == f"S{ids['storage']}.{lcn_before + 1}"
    assert await last_lcn(pg_session, ids["storage"]) == lcn_before + 1
    # the file supplies the number, so the shared counter must stay where it was
    assert await counter_value(pg_session) == counter_before


async def test_create_active_from_model_uses_the_lcn_of_the_model_position(pg_session):
    ids = await reference_ids(pg_session)
    id_train = await make_train(pg_session, ids["train_type"])
    counter_before = await counter_value(pg_session)
    rows = [{"car_number": 1, "id_car_place": ids["car_place"], "id_train": id_train,
             "serial_number": "sn", "type_active": TYPE_ACTIVE, "id_design_number": ids["design_number"],
             "lcn": f"{id_train}.1.6"}]

    await run_generated_sql(pg_session, "\n".join(actives_sql.create_active_from_model(rows)))

    created = (await pg_session.execute(
        text("SELECT a.active_number, a.lcn::text AS lcn, l.id_train, l.car_number, l.id_type_location "
             "FROM public.actives a JOIN public.location l ON l.id = a.id_location WHERE l.id_train = :t"),
        {"t": id_train})).first()
    assert created.lcn == f"{id_train}.1.6"
    assert created.active_number == expected_number(counter_before + 1)
    assert (created.car_number, created.id_type_location) == (1, 2)


async def test_delete_actives_removes_the_asset_and_its_counter(pg_session):
    ids = await reference_ids(pg_session)
    id_train = await make_train(pg_session, ids["train_type"])
    id_location = await make_location(pg_session, id_type_location=2, id_train=id_train, car_number=1)
    id_active = await make_active(pg_session, f"{id_train}.1", ids["design_number"], id_location)
    # the trigger on actives creates the counter row that blocks a plain DELETE
    assert await pg_session.scalar(
        text("SELECT count(*) FROM public.counter_active WHERE id_active = :a"), {"a": id_active}) > 0

    rows = [{"id_active": id_active, "active_number": f"{TEST_PREFIX}{id_active}", "id_location": id_location}]
    await run_generated_sql(pg_session, "\n".join(actives_sql.delete_actives(rows)))

    assert await pg_session.scalar(text("SELECT count(*) FROM public.actives WHERE id = :a"), {"a": id_active}) == 0
    assert await pg_session.scalar(
        text("SELECT count(*) FROM public.counter_active WHERE id_active = :a"), {"a": id_active}) == 0
    assert await pg_session.scalar(
        text("SELECT count(*) FROM public.location WHERE id = :l"), {"l": id_location}) == 0


async def test_delete_actives_keeps_a_location_another_asset_still_uses(pg_session):
    ids = await reference_ids(pg_session)
    id_train = await make_train(pg_session, ids["train_type"])
    id_location = await make_location(pg_session, id_type_location=2, id_train=id_train, car_number=1)
    doomed = await make_active(pg_session, f"{id_train}.1", ids["design_number"], id_location)
    await make_active(pg_session, f"{id_train}.2", ids["design_number"], id_location)

    rows = [{"id_active": doomed, "active_number": f"{TEST_PREFIX}{doomed}", "id_location": id_location}]
    await run_generated_sql(pg_session, "\n".join(actives_sql.delete_actives(rows)))

    assert await pg_session.scalar(text("SELECT count(*) FROM public.location WHERE id = :l"), {"l": id_location}) == 1


async def test_recount_mileage_writes_the_constant_and_recomputes_the_counter(pg_session):
    ids = await reference_ids(pg_session)
    id_train = await make_train(pg_session, ids["train_type"])
    id_location = await make_location(pg_session, id_type_location=2, id_train=id_train, car_number=1)
    id_active = await make_active(pg_session, f"{id_train}.1", ids["design_number"], id_location)
    rows = [{"id_active": id_active, "active_number": f"{TEST_PREFIX}{id_active}", "milage_const": 1000,
             "insert_mileage_start": True, "total": 250, "is_train": False}]

    await run_generated_sql(pg_session, "\n".join(actives_sql.recount_mileage(rows)))

    mileage = (await pg_session.execute(
        text("SELECT milage, milage_const, is_recount FROM public.mileage_start WHERE id_active = :a"),
        {"a": id_active})).first()
    assert (mileage.milage_const, mileage.milage, mileage.is_recount) == (1000, 1250, True)
    # counter_active.value is recomputed by function_get_mileage() inside the UPDATE
    assert await pg_session.scalar(
        text("SELECT count(*) FROM public.counter_active WHERE id_active = :a AND id_counter_type = :t "
             "AND value IS NOT NULL"),
        {"a": id_active, "t": MILEAGE_COUNTER_TYPE_ID}) == 1


async def test_update_design_number_and_serial_number(pg_session):
    ids = await reference_ids(pg_session)
    id_train = await make_train(pg_session, ids["train_type"])
    id_active = await make_active(pg_session, f"{id_train}.1", ids["design_number"],
                                  await make_location(pg_session, id_type_location=2, id_train=id_train))
    number = f"{TEST_PREFIX}{id_active}"
    other_dn = await pg_session.scalar(
        text("SELECT id FROM public.design_number WHERE id <> :dn ORDER BY id LIMIT 1"), {"dn": ids["design_number"]})

    await run_generated_sql(pg_session, "\n".join(
        actives_sql.update_design_number([(number, other_dn, "any")])
        + actives_sql.update_serial_number([(number, "SN-42")])))

    updated = (await pg_session.execute(
        text("SELECT id_design_number, serial_number FROM public.actives WHERE id = :a"), {"a": id_active})).first()
    assert (updated.id_design_number, updated.serial_number) == (other_dn, "SN-42")
