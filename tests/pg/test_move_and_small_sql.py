"""The remaining generated SQL against a real PostgreSQL: the asset move DO $$
block, and the one-statement builders whose Postgres syntax SQLite cannot parse
(ON CONFLICT, ltree lcns, timestamp literals)."""
from datetime import datetime

from sql_builders import design_number as design_number_sql
from sql_builders import models as models_sql
from sql_builders import orders as orders_sql
from sql_builders import ptoir as ptoir_sql
from sqlalchemy import text

from tests.pg.conftest import run_generated_sql
from tests.pg.factories import TEST_PREFIX, make_active, make_location, make_train, next_id, reference_ids


async def asset_on_a_train(pg_session) -> tuple[int, str, int]:
    """A real asset currently mounted on a train: (id, active_number, id_location).

    The move fires relocate_triger, whose PL/Python recomputes the mileage counter
    from the asset's own history — a synthetic asset with an empty counter makes it
    fail on its own, so the test moves a genuine one and rolls the move back.
    """
    row = (await pg_session.execute(text(
        "SELECT a.id, a.active_number, a.id_location FROM public.actives a "
        "JOIN public.location l ON l.id = a.id_location "
        "JOIN public.counter_active c ON c.id_active = a.id AND c.id_counter_type = 3 "
        "WHERE l.id_type_location = 2 AND c.date IS NOT NULL AND c.is_train = false "
        "ORDER BY a.id DESC LIMIT 1"))).first()
    return row.id, row.active_number, row.id_location


async def test_move_actives_relocates_the_asset_into_storage(pg_session):
    ids = await reference_ids(pg_session)
    id_active, active_number, old_location = await asset_on_a_train(pg_session)
    lcn_before = await pg_session.scalar(
        text("SELECT last_lcn FROM public.storage WHERE id = :id"), {"id": ids["storage"]})
    rows = [{"row": 1, "active_number": active_number, "id_active": id_active, "id_location_old": old_location}]

    sql = models_sql.move_actives(rows, ids["storage"], ids["consignment"], ids["user_id"],
                                  "перемещение из теста", datetime(2026, 8, 19, 10, 30, 0))
    await run_generated_sql(pg_session, "\n".join(sql))

    moved = (await pg_session.execute(
        text("SELECT a.lcn::text AS lcn, a.id_actves_parent, a.id_actives_root, "
             "       l.id_type_location, l.id_storage, l.id_consignment "
             "FROM public.actives a JOIN public.location l ON l.id = a.id_location WHERE a.id = :a"),
        {"a": id_active})).first()
    assert moved.lcn == f"S{ids['storage']}.{lcn_before + 1}"
    assert (moved.id_type_location, moved.id_storage, moved.id_consignment) == (1, ids["storage"], ids["consignment"])
    assert moved.id_actves_parent is None and moved.id_actives_root is None

    relocate = (await pg_session.execute(
        text("SELECT id_location_old, id_location_new, date, reason, id_user FROM public.relocate "
             "WHERE id_active = :a ORDER BY id DESC LIMIT 1"), {"a": id_active})).first()
    assert relocate.id_location_old == old_location
    assert relocate.date == datetime(2026, 8, 19, 10, 30, 0)
    assert relocate.reason == "перемещение из теста"
    assert relocate.id_user == ids["user_id"]
    assert await pg_session.scalar(
        text("SELECT last_lcn FROM public.storage WHERE id = :id"), {"id": ids["storage"]}) == lcn_before + 1


async def test_move_actives_writes_the_nocm_design_number_when_asked(pg_session):
    ids = await reference_ids(pg_session)
    id_active, active_number, old_location = await asset_on_a_train(pg_session)
    nocm = await pg_session.scalar(text("SELECT id FROM public.design_number WHERE number = 'NOCM'"))
    rows = [{"row": 1, "active_number": active_number, "id_active": id_active, "id_location_old": old_location}]

    await run_generated_sql(pg_session, "\n".join(
        models_sql.move_actives(rows, ids["storage"], ids["consignment"], ids["user_id"],
                                "", datetime(2026, 8, 19, 10, 30, 0), id_design_number=nocm)))

    assert await pg_session.scalar(
        text("SELECT id_design_number FROM public.actives WHERE id = :a"), {"a": id_active}) == nocm


async def make_order(pg_session, number: str) -> int:
    order_id = await next_id(pg_session, "orders_id_seq")
    await pg_session.execute(
        text("INSERT INTO public.orders (id, order_number) VALUES (:id, :number)"),
        {"id": order_id, "number": number})
    return order_id


async def test_assign_parent_order_is_idempotent_through_on_conflict(pg_session):
    child = await make_order(pg_session, f"{TEST_PREFIX}-child")
    first_parent = await make_order(pg_session, f"{TEST_PREFIX}-parent-1")
    second_parent = await make_order(pg_session, f"{TEST_PREFIX}-parent-2")

    await run_generated_sql(pg_session, "\n".join(
        orders_sql.assign_parent_order([(child, first_parent, "child", "parent-1")])))
    assert await pg_session.scalar(
        text("SELECT id_parent FROM public.order_to_order WHERE id_child = :c"), {"c": child}) == first_parent

    # a second run with another parent must update the row, not fail on the unique key
    await run_generated_sql(pg_session, "\n".join(
        orders_sql.assign_parent_order([(child, second_parent, "child", "parent-2")])))
    assert await pg_session.scalar(
        text("SELECT id_parent FROM public.order_to_order WHERE id_child = :c"), {"c": child}) == second_parent
    assert await pg_session.scalar(
        text("SELECT count(*) FROM public.order_to_order WHERE id_child = :c"), {"c": child}) == 1


async def test_update_ptoir_activates_the_maintenance_and_its_warning_level(pg_session):
    ids = await reference_ids(pg_session)
    id_train = await make_train(pg_session, ids["train_type"])
    id_active = await make_active(pg_session, f"{id_train}.1", ids["design_number"],
                                  await make_location(pg_session, id_type_location=2, id_train=id_train))
    ptoir_id = await next_id(pg_session, "ptoir_id_seq")
    await pg_session.execute(
        text("INSERT INTO public.ptoir (id, number_ptoir, id_active, is_active) VALUES (:id, :n, :a, false)"),
        {"id": ptoir_id, "n": f"{TEST_PREFIX}-{ptoir_id}", "a": id_active})
    warning_id = await next_id(pg_session, "ptoir_level_warning_id_seq")
    await pg_session.execute(
        text("INSERT INTO public.ptoir_level_warning (id, level, id_ptoir, id_counter_type, zero_point_value) "
             "VALUES (:id, 'YELLOW', :p, 3, 0)"),
        {"id": warning_id, "p": ptoir_id})

    await run_generated_sql(pg_session, "\n".join(
        ptoir_sql.update_ptoir([(ptoir_id, datetime(2026, 5, 6, 7, 8, 9), 30, warning_id, 12345)])))

    updated = (await pg_session.execute(
        text("SELECT date_activation, interval, is_active FROM public.ptoir WHERE id = :id"), {"id": ptoir_id})).first()
    assert (updated.date_activation, updated.interval, updated.is_active) == (datetime(2026, 5, 6, 7, 8, 9), 30, True)
    assert await pg_session.scalar(
        text("SELECT zero_point_value FROM public.ptoir_level_warning WHERE id = :id"), {"id": warning_id}) == 12345


async def test_design_number_updates_hit_the_row_matched_by_number(pg_session):
    number = await pg_session.scalar(text("SELECT number FROM public.design_number ORDER BY id LIMIT 1"))
    dn_id = await pg_session.scalar(text("SELECT id FROM public.design_number WHERE number = :n"), {"n": number})
    group_id = await pg_session.scalar(text("SELECT id FROM public.counter_group ORDER BY id LIMIT 1"))
    unit_id = await pg_session.scalar(text("SELECT id FROM public.unit_type ORDER BY id LIMIT 1"))

    await run_generated_sql(pg_session, "\n".join(
        design_number_sql.update_counter_group([(dn_id, number, group_id)])
        + design_number_sql.update_unit_type([(dn_id, number, unit_id)])
        + design_number_sql.update_is_serial_1c([(dn_id, number, True)])))

    updated = (await pg_session.execute(
        text("SELECT id_counter_group, id_unit_type, is_serial_1c FROM public.design_number WHERE id = :id"),
        {"id": dn_id})).first()
    assert (updated.id_counter_group, updated.id_unit_type, updated.is_serial_1c) == (group_id, unit_id, True)
