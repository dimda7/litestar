"""Tests for the "Корректировка введенного пробега" page.

Two seams: resolve_correction (controllers/mileage_correction.py), which turns a
train and a selected mileage row into everything the SQL needs, and
sql_builders.mileage, the pure function that turns that into SQL text.
"""
from datetime import date, datetime

from controllers.mileage_correction import resolve_correction
from sql_builders import mileage as mileage_sql
from tests.conftest import (
    make_active, make_counter_active, make_mileage_train, make_train, make_train_type,
)


async def train_with_counter(db_session, active_value: int = 0) -> tuple[int, int]:
    """A train whose asset carries a mileage counter: (train_id, active_id)."""
    id_train_type = await make_train_type(db_session)
    id_active = await make_active(db_session)
    id_train = await make_train(db_session, id_train_type, name="ЭС2Г-101", active=id_active)
    await make_counter_active(db_session, id_active, value=active_value)
    return id_train, id_active


async def three_days(db_session, id_train: int) -> list[int]:
    """Mileage rows for 14, 15 and 16 October, newest last."""
    return [
        await make_mileage_train(db_session, id_train, date(2023, 10, 14), 199000, datetime(2023, 10, 14, 9, 0)),
        await make_mileage_train(db_session, id_train, date(2023, 10, 15), 199145, datetime(2023, 10, 15, 9, 0)),
        await make_mileage_train(db_session, id_train, date(2023, 10, 16), 250000, datetime(2023, 10, 16, 9, 0)),
    ]


async def test_resolves_the_correction_for_the_selected_row(db_session):
    id_train, id_active = await train_with_counter(db_session)
    day14, day15, day16 = await three_days(db_session, id_train)

    error, correction = await resolve_correction(db_session, id_train, day16)

    assert error == ""
    assert correction.id_train == id_train
    assert correction.id_active == id_active
    assert correction.counter_date == datetime(2023, 10, 15, 9, 0)
    assert correction.counter_value == 199145
    assert correction.source_row_id == day15
    assert correction.source_date_average == date(2023, 10, 15)
    assert correction.delete_count == 1


async def test_delete_count_covers_rows_the_ten_row_list_never_showed(db_session):
    """The threshold is source_date_average, a date, so it also catches same-day
    rows and rows with a null `date` — which the page's list, filtered on
    `date is not null`, hides."""
    id_train, _ = await train_with_counter(db_session)
    _, _, day16 = await three_days(db_session, id_train)
    await make_mileage_train(db_session, id_train, date(2023, 10, 16), 250100, datetime(2023, 10, 16, 18, 0))
    await make_mileage_train(db_session, id_train, date(2023, 10, 17), 250200, None)

    error, correction = await resolve_correction(db_session, id_train, day16)

    assert error == ""
    assert correction.delete_count == 3


async def test_gap_row_between_the_kept_row_and_the_selected_one_is_also_deleted(db_session):
    """counter_active_trigger fills every day of a gap between two manual
    readings, so a null-date row can sit strictly between the kept row and the
    selected one without ever appearing in the page's ten-row list. Deleting
    from the selected row's own date onward (the old, wrong threshold) would
    miss it; date_average > the kept row's date_average catches it."""
    id_train, _ = await train_with_counter(db_session)
    kept = await make_mileage_train(db_session, id_train, date(2023, 10, 13), 198000, datetime(2023, 10, 13, 9, 0))
    gap_row = await make_mileage_train(db_session, id_train, date(2023, 10, 14), 198500, None)
    selected = await make_mileage_train(db_session, id_train, date(2023, 10, 16), 199000, datetime(2023, 10, 16, 9, 0))

    error, correction = await resolve_correction(db_session, id_train, selected)

    assert error == ""
    assert correction.source_row_id == kept
    assert correction.source_date_average == date(2023, 10, 13)
    assert correction.counter_date == datetime(2023, 10, 13, 9, 0)
    assert correction.counter_value == 198000
    # gap_row (10-14, hidden) and selected (10-16) — 2 rows, not 1.
    assert correction.delete_count == 2
    assert gap_row != kept


async def test_rows_of_another_train_are_not_counted(db_session):
    id_train, _ = await train_with_counter(db_session)
    _, _, day16 = await three_days(db_session, id_train)
    id_other, _ = await train_with_counter(db_session)
    await make_mileage_train(db_session, id_other, date(2023, 10, 20), 300000, datetime(2023, 10, 20, 9, 0))

    error, correction = await resolve_correction(db_session, id_train, day16)

    assert error == ""
    assert correction.delete_count == 1


async def test_row_of_another_train_is_rejected(db_session):
    id_train, _ = await train_with_counter(db_session)
    await three_days(db_session, id_train)
    id_other, _ = await train_with_counter(db_session)
    foreign_row = await make_mileage_train(db_session, id_other, date(2023, 10, 16), 1, datetime(2023, 10, 16, 9, 0))

    error, correction = await resolve_correction(db_session, id_train, foreign_row)

    assert correction is None
    assert "не найдена" in error


async def test_unknown_train_is_rejected(db_session):
    error, correction = await resolve_correction(db_session, 4242, 1)

    assert correction is None
    assert "Поезд не найден" in error


async def test_train_without_an_asset_is_rejected(db_session):
    id_train_type = await make_train_type(db_session)
    id_train = await make_train(db_session, id_train_type, name="ЭС2Г-102")
    rows = await three_days(db_session, id_train)

    error, correction = await resolve_correction(db_session, id_train, rows[-1])

    assert correction is None
    assert "не задан актив" in error


async def test_missing_counter_is_rejected(db_session):
    id_train_type = await make_train_type(db_session)
    id_active = await make_active(db_session)
    id_train = await make_train(db_session, id_train_type, name="ЭС2Г-103", active=id_active)
    rows = await three_days(db_session, id_train)

    error, correction = await resolve_correction(db_session, id_train, rows[-1])

    assert correction is None
    assert "Счётчик пробега не найден" in error


async def test_counter_of_another_type_does_not_count(db_session):
    id_train_type = await make_train_type(db_session)
    id_active = await make_active(db_session)
    id_train = await make_train(db_session, id_train_type, name="ЭС2Г-104", active=id_active)
    await make_counter_active(db_session, id_active, id_counter_type=1)
    await make_counter_active(db_session, id_active, is_train=False)
    rows = await three_days(db_session, id_train)

    error, correction = await resolve_correction(db_session, id_train, rows[-1])

    assert correction is None
    assert "Счётчик пробега не найден" in error


async def test_several_counters_are_rejected(db_session):
    id_train, id_active = await train_with_counter(db_session)
    await make_counter_active(db_session, id_active)
    rows = await three_days(db_session, id_train)

    error, correction = await resolve_correction(db_session, id_train, rows[-1])

    assert correction is None
    assert "несколько счётчиков" in error


async def test_oldest_row_has_nothing_to_roll_back_to(db_session):
    id_train, _ = await train_with_counter(db_session)
    day14, _, _ = await three_days(db_session, id_train)

    error, correction = await resolve_correction(db_session, id_train, day14)

    assert correction is None
    assert "более ранней записи" in error


async def test_earlier_row_without_a_mileage_is_rejected(db_session):
    id_train, _ = await train_with_counter(db_session)
    await make_mileage_train(db_session, id_train, date(2023, 10, 15), None, datetime(2023, 10, 15, 9, 0))
    selected = await make_mileage_train(db_session, id_train, date(2023, 10, 16), 250000, datetime(2023, 10, 16, 9, 0))

    error, correction = await resolve_correction(db_session, id_train, selected)

    assert correction is None
    assert "не заполнен пробег" in error


async def test_selected_row_without_a_date_is_rejected(db_session):
    """A null-date row is never a manual reading and the page's list would never
    offer it — a crafted request must not be able to select one anyway."""
    id_train, _ = await train_with_counter(db_session)
    await make_mileage_train(db_session, id_train, date(2023, 10, 15), 199145, datetime(2023, 10, 15, 9, 0))
    selected = await make_mileage_train(db_session, id_train, date(2023, 10, 16), 250000, None)

    error, correction = await resolve_correction(db_session, id_train, selected)

    assert correction is None
    assert "не заполнено время" in error


async def test_latest_earlier_row_wins_among_several(db_session):
    """Two manual readings can share a date_average (a same-day correction) — the
    later id, not the first one found, is the true last state."""
    id_train, _ = await train_with_counter(db_session)
    await make_mileage_train(db_session, id_train, date(2023, 10, 10), 198000, datetime(2023, 10, 10, 9, 0))
    await make_mileage_train(db_session, id_train, date(2023, 10, 15), 199145, datetime(2023, 10, 15, 9, 0))
    later_same_day = await make_mileage_train(db_session, id_train, date(2023, 10, 15), 199200, datetime(2023, 10, 15, 14, 0))
    selected = await make_mileage_train(db_session, id_train, date(2023, 10, 16), 250000, datetime(2023, 10, 16, 9, 0))

    error, correction = await resolve_correction(db_session, id_train, selected)

    assert error == ""
    assert correction.source_row_id == later_same_day
    assert correction.counter_value == 199200
    assert correction.counter_date == datetime(2023, 10, 15, 14, 0)


async def test_null_date_row_is_not_picked_as_the_previous_state(db_session):
    """A null-date row only exists as the trigger's own interpolation for a
    later manual entry — it must never be mistaken for a surviving prior
    reading, even when it happens to be the closest one by date_average."""
    id_train, _ = await train_with_counter(db_session)
    kept = await make_mileage_train(db_session, id_train, date(2023, 10, 10), 198000, datetime(2023, 10, 10, 9, 0))
    await make_mileage_train(db_session, id_train, date(2023, 10, 15), 199145, None)
    selected = await make_mileage_train(db_session, id_train, date(2023, 10, 16), 250000, datetime(2023, 10, 16, 9, 0))

    error, correction = await resolve_correction(db_session, id_train, selected)

    assert error == ""
    assert correction.source_row_id == kept
    assert correction.counter_value == 198000
    assert correction.counter_date == datetime(2023, 10, 10, 9, 0)


CORRECTION = mileage_sql.Correction(
    id_train=271,
    id_active=401961,
    counter_date=datetime(2026, 7, 31, 0, 0, 0),
    counter_value=7020184,
    source_row_id=2865781,
    source_date_average=date(2026, 7, 31),
    delete_count=2,
)


def test_generated_sql_matches_the_reference_script():
    """The reference case: rows

        2867566  2026-08-03 00:00:00  2026-08-03  7022892  1496
        2867439  2026-08-02 00:00:00  2026-08-02  7021396   606  <- selected
        2865781  2026-07-31 00:00:00  2026-07-31  7020184   811

    Both the delete threshold and the counter's new value/date come from the
    same row — the last surviving manual reading (2026-07-31), not the selected
    one — > not >=, and against the earlier surviving row."""
    sql = "\n".join(mileage_sql.correct_mileage(CORRECTION))

    assert sql == (
        "ALTER TABLE public.counter_active DISABLE TRIGGER counter_active_trigger;\n"
        "\n"
        "DELETE FROM public.mileage_train\n"
        "WHERE id_train = 271 AND date_average > '2026-07-31';\n"
        "\n"
        "-- value from mileage_train.id=2865781, date_average=2026-07-31\n"
        "UPDATE public.counter_active c\n"
        "SET value = 7020184, date = '2026-07-31 00:00:00'\n"
        "WHERE c.id_active = 401961 AND c.id_counter_type = 3 AND c.is_train = true;\n"
        "\n"
        "ALTER TABLE public.counter_active ENABLE TRIGGER counter_active_trigger;"
    )


def test_downloadable_sql_is_wrapped_in_a_complete_transaction():
    lines = mileage_sql.correct_mileage(CORRECTION, wrap_transaction=True)

    assert lines[0] == "BEGIN;"
    assert lines[-1] == "COMMIT;"
    assert "\n".join(lines[1:-1]).strip() == "\n".join(mileage_sql.correct_mileage(CORRECTION))


def test_executed_sql_carries_no_transaction_control():
    """The session owns the transaction when the app executes; a stray COMMIT
    would end it before the app can roll the DISABLE TRIGGER back."""
    sql = "\n".join(mileage_sql.correct_mileage(CORRECTION))

    assert "BEGIN" not in sql
    assert "COMMIT" not in sql


def test_trigger_is_re_enabled_after_the_update():
    lines = mileage_sql.correct_mileage(CORRECTION)
    disable = next(i for i, line in enumerate(lines) if "DISABLE TRIGGER" in line)
    update = next(i for i, line in enumerate(lines) if line.startswith("UPDATE"))
    enable = next(i for i, line in enumerate(lines) if "ENABLE TRIGGER" in line)

    assert disable < update < enable
