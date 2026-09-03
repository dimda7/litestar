from dataclasses import dataclass
from datetime import date, datetime

# counter_active_trigger fires on UPDATE of a counter of this type with
# is_train = true, and its PL/Python body is what generates the mileage_train
# rows day by day. Rolling mileage back means updating the counter with the
# trigger switched off, or it would regenerate what the DELETE just removed.
MILEAGE_COUNTER_TYPE_ID = 3
MILEAGE_TRIGGER_NAME = "counter_active_trigger"


@dataclass(frozen=True)
class Correction:
    """A resolved rollback: what to delete and what to reset the counter to.

    counter_date comes from the selected (deleted) row while counter_value comes
    from the last surviving one — the two are deliberately from different rows,
    reproducing the correction operators run by hand.
    """

    id_train: int
    id_active: int
    boundary: date
    counter_date: datetime
    counter_value: int
    source_row_id: int
    source_date_average: date
    delete_count: int


def correct_mileage(correction: Correction, wrap_transaction: bool = False) -> list[str]:
    """SQL rolling a train's mileage back to the state before `boundary`.

    wrap_transaction adds BEGIN/COMMIT for the downloadable file; when the app
    executes the statements itself the session already owns the transaction, and
    a COMMIT here would end it before a failure could roll the DISABLE back.
    """
    lines = [
        f"ALTER TABLE public.counter_active DISABLE TRIGGER {MILEAGE_TRIGGER_NAME};",
        "",
        "DELETE FROM public.mileage_train",
        f"WHERE id_train = {correction.id_train} "
        f"AND date_average >= '{correction.boundary:%Y-%m-%d}';",
        "",
        f"-- value from mileage_train.id={correction.source_row_id}, "
        f"date_average={correction.source_date_average:%Y-%m-%d}",
        "UPDATE public.counter_active c",
        f"SET value = {correction.counter_value}, "
        f"date = '{correction.counter_date:%Y-%m-%d %H:%M:%S}'",
        f"WHERE c.id_active = {correction.id_active} "
        f"AND c.id_counter_type = {MILEAGE_COUNTER_TYPE_ID} AND c.is_train = true;",
        "",
        f"ALTER TABLE public.counter_active ENABLE TRIGGER {MILEAGE_TRIGGER_NAME};",
    ]

    if wrap_transaction:
        return ["BEGIN;", "", *lines, "", "COMMIT;"]
    return lines
