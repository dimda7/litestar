"""The mileage correction against a real PostgreSQL copy.

SQLite cannot parse ALTER TABLE ... DISABLE TRIGGER, and the whole point of the
script is that the trigger really is off while counter_active is updated: with
it on, counter_active_python would regenerate the mileage_train rows the DELETE
just removed. Everything runs inside the rolled-back transaction, so the
disabled trigger is restored with the rest.

Only the executable variant of the script is run here. The downloadable one ends
in COMMIT, which asyncpg would apply to the fixture's own transaction — the test
data would be committed to the copy and the rollback would fail. That variant is
covered by the text assertions in tests/test_mileage_correction_validation.py.
"""
from datetime import date, datetime

from sqlalchemy import text

from controllers.mileage_correction import resolve_correction
from sql_builders import mileage as mileage_sql
from tests.pg.conftest import run_generated_sql
from tests.pg.factories import make_active, make_train, next_id, reference_ids


async def make_mileage_train(session, id_train, date_average, milage, moment, mileage_average=100):
    id_row = await next_id(session, "mileage_train_id_seq")
    await session.execute(
        text("INSERT INTO public.mileage_train (id, id_train, milage, mileage_average, date, date_average) "
             "VALUES (:id, :train, :milage, :avg, :date, :date_average)"),
        {"id": id_row, "train": id_train, "milage": milage, "avg": mileage_average,
         "date": moment, "date_average": date_average},
    )
    return id_row


async def mileage_design_number(session) -> int:
    """A design number whose counter group carries the mileage counter type.

    actives_trgger creates a counter_active row per counter type of the asset's
    design number, and a UNIQUE index on (id_active, id_counter_type) means the
    test cannot add one of its own.
    """
    return await session.scalar(text(
        "SELECT des.id FROM public.design_number des "
        "JOIN public.counter_type_to_group link ON link.id_counter_group = des.id_counter_group "
        "WHERE link.id_counter_type = :type ORDER BY des.id LIMIT 1"),
        {"type": mileage_sql.MILEAGE_COUNTER_TYPE_ID})


async def adopt_mileage_counter(session, id_active, value, moment) -> int:
    """Turn the counter actives_trgger created into a train mileage counter.

    The row is inserted with is_train = false, and counter_active_trigger's WHEN
    reads the OLD row, so this one update does not fire it.
    """
    id_counter = await session.scalar(
        text("SELECT id FROM public.counter_active WHERE id_active = :active AND id_counter_type = :type"),
        {"active": id_active, "type": mileage_sql.MILEAGE_COUNTER_TYPE_ID})
    await session.execute(
        text("UPDATE public.counter_active SET is_train = true, value = :value, date = :date WHERE id = :id"),
        {"value": value, "date": moment, "id": id_counter},
    )
    return id_counter


async def train_with_counter(session, value: int, moment: datetime) -> tuple[int, int, int]:
    """A train with an asset carrying a mileage counter: (id_train, id_active, id_counter)."""
    ids = await reference_ids(session)
    id_train = await make_train(session, ids["train_type"])
    id_active = await make_active(session, lcn=str(id_train),
                                  id_design_number=await mileage_design_number(session))
    await session.execute(
        text("UPDATE public.train SET active = :active WHERE id = :id"),
        {"active": id_active, "id": id_train},
    )
    id_counter = await adopt_mileage_counter(session, id_active, value, moment)
    return id_train, id_active, id_counter


async def train_with_mileage(session) -> dict:
    """A train, its asset, its mileage counter and three days of mileage."""
    id_train, id_active, id_counter = await train_with_counter(session, 250000, datetime(2023, 10, 16, 9, 0))
    kept = await make_mileage_train(session, id_train, date(2023, 10, 14), 199000, datetime(2023, 10, 14, 9, 0))
    source = await make_mileage_train(session, id_train, date(2023, 10, 15), 199145, datetime(2023, 10, 15, 9, 0))
    bad = await make_mileage_train(session, id_train, date(2023, 10, 16), 250000, datetime(2023, 10, 16, 9, 0))
    generated = await make_mileage_train(session, id_train, date(2023, 10, 17), 250100, None)
    return {"id_train": id_train, "id_active": id_active, "id_counter": id_counter,
            "kept": kept, "source": source, "bad": bad, "generated": generated}


async def trigger_state(session) -> str:
    return await session.scalar(text(
        "SELECT tgenabled FROM pg_trigger WHERE tgrelid = 'public.counter_active'::regclass "
        "AND tgname = :name"), {"name": mileage_sql.MILEAGE_TRIGGER_NAME})


async def test_generated_sql_rolls_the_mileage_back(pg_session):
    fixture = await train_with_mileage(pg_session)
    error, correction = await resolve_correction(pg_session, fixture["id_train"], fixture["bad"])
    assert error == ""
    assert correction.delete_count == 2

    await run_generated_sql(pg_session, "\n".join(mileage_sql.correct_mileage(correction)))

    remaining = (await pg_session.execute(
        text("SELECT id, milage, mileage_average, date, date_average FROM public.mileage_train "
             "WHERE id_train = :id ORDER BY id"),
        {"id": fixture["id_train"]})).mappings().all()
    assert [dict(r) for r in remaining] == [
        {"id": fixture["kept"], "milage": 199000, "mileage_average": 100,
         "date": datetime(2023, 10, 14, 9, 0), "date_average": date(2023, 10, 14)},
        {"id": fixture["source"], "milage": 199145, "mileage_average": 100,
         "date": datetime(2023, 10, 15, 9, 0), "date_average": date(2023, 10, 15)},
    ]

    counter = (await pg_session.execute(
        text("SELECT value, date FROM public.counter_active WHERE id = :id"),
        {"id": fixture["id_counter"]})).mappings().one()
    assert counter["value"] == 199145
    assert counter["date"] == datetime(2023, 10, 16, 9, 0)


async def test_trigger_is_enabled_again_after_the_script(pg_session):
    fixture = await train_with_mileage(pg_session)
    before = await trigger_state(pg_session)
    _, correction = await resolve_correction(pg_session, fixture["id_train"], fixture["bad"])

    await run_generated_sql(pg_session, "\n".join(mileage_sql.correct_mileage(correction)))

    assert await trigger_state(pg_session) == before


async def test_gap_row_between_kept_and_selected_is_deleted_and_never_used_as_previous(pg_session):
    """The reference case that prompted this fix: a manual reading followed, a
    day or more later, by another one leaves a null-date row in between (the
    trigger fills every day of the gap) that never shows up in the page's list.
    That row must both (a) not be picked as the state to roll back to, and
    (b) be deleted along with the bad reading — rolling back to the *kept*
    reading two days earlier, not to the gap's interpolated value."""
    id_train, id_active, id_counter = await train_with_counter(pg_session, 250000, datetime(2023, 10, 16, 9, 0))
    kept = await make_mileage_train(pg_session, id_train, date(2023, 10, 13), 198000, datetime(2023, 10, 13, 9, 0))
    gap_row = await make_mileage_train(pg_session, id_train, date(2023, 10, 14), 198500, None)
    bad = await make_mileage_train(pg_session, id_train, date(2023, 10, 16), 250000, datetime(2023, 10, 16, 9, 0))

    error, correction = await resolve_correction(pg_session, id_train, bad)
    assert error == ""
    assert correction.source_row_id == kept
    assert correction.counter_value == 198000
    assert correction.delete_count == 2

    await run_generated_sql(pg_session, "\n".join(mileage_sql.correct_mileage(correction)))

    remaining = (await pg_session.execute(
        text("SELECT id FROM public.mileage_train WHERE id_train = :id ORDER BY id"),
        {"id": id_train})).scalars().all()
    assert remaining == [kept]
    assert gap_row not in remaining
    assert bad not in remaining

    counter = (await pg_session.execute(
        text("SELECT value, date FROM public.counter_active WHERE id = :id"),
        {"id": id_counter})).mappings().one()
    assert counter["value"] == 198000
    assert counter["date"] == datetime(2023, 10, 16, 9, 0)


async def test_counters_of_other_assets_are_untouched(pg_session):
    fixture = await train_with_mileage(pg_session)
    other_active = await make_active(pg_session, lcn=f"{fixture['id_train']}.1",
                                     id_design_number=await mileage_design_number(pg_session))
    other_counter = await adopt_mileage_counter(pg_session, other_active, 111,
                                                datetime(2023, 10, 16, 9, 0))
    _, correction = await resolve_correction(pg_session, fixture["id_train"], fixture["bad"])

    await run_generated_sql(pg_session, "\n".join(mileage_sql.correct_mileage(correction)))

    assert await pg_session.scalar(
        text("SELECT value FROM public.counter_active WHERE id = :id"), {"id": other_counter}) == 111
