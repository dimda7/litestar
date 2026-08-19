"""sql_builders/models.py executed against a real PostgreSQL.

These are the paths the SQLite suite cannot reach: ltree columns, the `::text`
cast, and the UNIQUE indexes the two-phase update exists to work around.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sql_builders import models as models_sql
from tests.pg.conftest import run_generated_sql
from tests.pg.factories import lcn_of, make_active, make_location, make_train, next_id, reference_ids


async def make_two_assets(session) -> tuple[dict, int, int, int]:
    """Two assets on one train at <train>.1 and <train>.2."""
    ids = await reference_ids(session)
    id_train = await make_train(session, ids["train_type"])
    first = await make_active(session, f"{id_train}.1", ids["design_number"],
                              await make_location(session, id_type_location=2, id_train=id_train, car_number=1))
    second = await make_active(session, f"{id_train}.2", ids["design_number"],
                               await make_location(session, id_type_location=2, id_train=id_train, car_number=1))
    return ids, id_train, first, second


async def test_two_phase_update_survives_a_chain_of_renames(pg_session):
    ids, id_train, first, second = await make_two_assets(pg_session)
    pairs = [{"old_lcn": f"{id_train}.1", "new_lcn": f"{id_train}.2"},
             {"old_lcn": f"{id_train}.2", "new_lcn": f"{id_train}.3"}]

    await run_generated_sql(pg_session, "\n".join(models_sql.move_no_relocate(pairs)))

    assert await lcn_of(pg_session, first) == f"{id_train}.2"
    assert await lcn_of(pg_session, second) == f"{id_train}.3"


async def test_a_single_update_on_the_same_chain_would_violate_actives_lcn_key(pg_session):
    """Why two_phase_lcn_update exists: the naive one-statement form cannot run."""
    ids, id_train, first, second = await make_two_assets(pg_session)

    with pytest.raises(IntegrityError):
        async with pg_session.begin_nested():
            await pg_session.execute(
                text("UPDATE public.actives AS act SET lcn = v.new_lcn::ltree "
                     "FROM (VALUES (:old1, :new1), (:old2, :new2)) AS v(old_lcn, new_lcn) "
                     "WHERE act.lcn::text = v.old_lcn"),
                {"old1": f"{id_train}.1", "new1": f"{id_train}.2",
                 "old2": f"{id_train}.2", "new2": f"{id_train}.3"},
            )


async def test_move_no_relocate_leaves_location_and_parent_untouched(pg_session):
    ids, id_train, first, _ = await make_two_assets(pg_session)
    before = (await pg_session.execute(
        text("SELECT id_location, id_actves_parent, id_actives_root FROM public.actives WHERE id = :id"),
        {"id": first})).first()

    await run_generated_sql(pg_session, "\n".join(
        models_sql.move_no_relocate([{"old_lcn": f"{id_train}.1", "new_lcn": f"{id_train}.9"}])))

    after = (await pg_session.execute(
        text("SELECT id_location, id_actves_parent, id_actives_root FROM public.actives WHERE id = :id"),
        {"id": first})).first()
    assert after == before
    assert await lcn_of(pg_session, first) == f"{id_train}.9"


async def test_set_serial_none_matches_assets_by_lcn_text(pg_session):
    ids, id_train, first, second = await make_two_assets(pg_session)
    rows = [{"lcn_trains": [f"{id_train}.1"]}]

    await run_generated_sql(pg_session, "\n".join(models_sql.set_serial_none(rows)))

    serials = dict((await pg_session.execute(
        text("SELECT id, serial_number FROM public.actives WHERE id = ANY(:ids)"),
        {"ids": [first, second]})).all())
    assert serials[first] == "none"
    assert serials[second] is None


async def test_change_model_lcn_updates_models_through_the_chain(pg_session):
    ids = await reference_ids(pg_session)
    made = []
    for suffix in ("1", "2"):
        model_id = await next_id(pg_session, "models_id_seq")
        await pg_session.execute(
            text("INSERT INTO public.models (id, id_train_type, lcn, id_car_place, id_design_number, is_default) "
                 "VALUES (:id, :tt, CAST(:lcn AS ltree), :cp, :dn, false)"),
            {"id": model_id, "tt": ids["train_type"], "lcn": f"M{ids['train_type']}.{model_id}.{suffix}",
             "cp": ids["car_place"], "dn": ids["design_number"]},
        )
        made.append((model_id, f"M{ids['train_type']}.{model_id}.{suffix}"))
    (first_id, first_lcn), (second_id, second_lcn) = made

    rows = [{"id": first_id, "new_lcn": second_lcn}, {"id": second_id, "new_lcn": f"{second_lcn}.9"}]
    await run_generated_sql(pg_session, "\n".join(models_sql.change_model_lcn(rows)))

    lcns = dict((await pg_session.execute(
        text("SELECT id, lcn::text FROM public.models WHERE id = ANY(:ids)"),
        {"ids": [first_id, second_id]})).all())
    assert lcns[first_id] == second_lcn
    assert lcns[second_id] == f"{second_lcn}.9"


async def test_insert_and_delete_models_round_trip(pg_session):
    ids = await reference_ids(pg_session)
    lcn = f"M{ids['train_type']}.{await next_id(pg_session, 'models_id_seq')}"
    rows = [(ids["train_type"], ids["car_place"], ids["design_number"], lcn, False)]

    await run_generated_sql(pg_session, "\n".join(models_sql.insert_models(rows)))
    model_id = await pg_session.scalar(
        text("SELECT id FROM public.models WHERE lcn::text = :lcn AND id_design_number = :dn"),
        {"lcn": lcn, "dn": ids["design_number"]})
    assert model_id is not None

    await run_generated_sql(pg_session, "\n".join(models_sql.delete_models([model_id])))
    assert await pg_session.scalar(text("SELECT count(*) FROM public.models WHERE id = :id"), {"id": model_id}) == 0
