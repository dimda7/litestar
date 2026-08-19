"""sql_builders/train.py executed against a real PostgreSQL — the DO $$ block
that creates a whole train (nextval arrays, ltree lcns, nlevel)."""
from sqlalchemy import text

from sql_builders import train as train_sql
from tests.pg.conftest import run_generated_sql
from tests.pg.factories import TEST_PREFIX, next_id, reference_ids


def row(active_number: str, lcn: str, *, is_root: bool = False, car_number: int | None = 1,
        car_place_id: int | None = None, id_unit_type: int | None = None, id_design_number: int = 1) -> dict:
    return {
        "serial_number": None, "id_actives_parent": None, "root_number": None,
        "car_number": car_number, "car_place_id": car_place_id, "id_unit_type": id_unit_type,
        "active_number": active_number, "id_design_number": id_design_number,
        "lcn_new": lcn, "is_root": is_root,
    }


async def test_insert_train_creates_train_locations_and_actives(pg_session):
    ids = await reference_ids(pg_session)
    id_train = await next_id(pg_session, "train_id_seq")
    rows = [
        row(f"{TEST_PREFIX}-root-{id_train}", str(id_train), is_root=True,
            id_design_number=ids["design_number"], id_unit_type=ids["unit_type"]),
        row(f"{TEST_PREFIX}-child-{id_train}", f"{id_train}.1",
            id_design_number=ids["design_number"], car_place_id=ids["car_place"]),
    ]

    sql = train_sql.insert_train(id_train, ids["train_type"], f"{TEST_PREFIX}-{id_train}",
                                 rows, ids["train_series"], count_car=5)
    await run_generated_sql(pg_session, "\n".join(sql))

    train = (await pg_session.execute(
        text("SELECT name, id_train_type, id_train_series, count_car, active FROM public.train WHERE id = :id"),
        {"id": id_train})).first()
    assert train.name == f"{TEST_PREFIX}-{id_train}"
    assert (train.id_train_type, train.id_train_series, train.count_car) == (ids["train_type"], ids["train_series"], 5)

    created = (await pg_session.execute(
        text("SELECT a.id, a.active_number, a.lcn::text AS lcn, l.id_train, l.id_type_location "
             "FROM public.actives a JOIN public.location l ON l.id = a.id_location "
             "WHERE l.id_train = :id ORDER BY a.lcn::text"),
        {"id": id_train})).all()
    assert [(c.lcn, c.id_type_location) for c in created] == [(str(id_train), 2), (f"{id_train}.1", 2)]

    # train.active must point at the nlevel(lcn) = 1 asset
    assert train.active == next(c.id for c in created if c.lcn == str(id_train))


async def test_insert_train_marks_the_root_counter_as_train(pg_session):
    ids = await reference_ids(pg_session)
    id_train = await next_id(pg_session, "train_id_seq")
    rows = [row(f"{TEST_PREFIX}-root-{id_train}", str(id_train), is_root=True, id_design_number=ids["design_number"])]

    await run_generated_sql(pg_session, "\n".join(
        train_sql.insert_train(id_train, ids["train_type"], f"{TEST_PREFIX}-{id_train}",
                               rows, ids["train_series"], count_car=None)))

    # the counter_active row itself is created by the actives_trgger trigger
    is_train = await pg_session.scalar(
        text("SELECT bool_or(c.is_train) FROM public.counter_active c "
             "JOIN public.actives a ON a.id = c.id_active "
             "JOIN public.location l ON l.id = a.id_location WHERE l.id_train = :id"),
        {"id": id_train})
    assert is_train is True


async def test_insert_train_writes_the_mileage_row(pg_session):
    ids = await reference_ids(pg_session)
    id_train = await next_id(pg_session, "train_id_seq")
    rows = [row(f"{TEST_PREFIX}-root-{id_train}", str(id_train), is_root=True, id_design_number=ids["design_number"])]

    await run_generated_sql(pg_session, "\n".join(
        train_sql.insert_train(id_train, ids["train_type"], f"{TEST_PREFIX}-{id_train}",
                               rows, ids["train_series"], count_car=None)))

    mileage = (await pg_session.execute(
        text("SELECT milage, mileage_average FROM public.mileage_train WHERE id_train = :id"),
        {"id": id_train})).first()
    assert (mileage.milage, mileage.mileage_average) == (0, 0)
