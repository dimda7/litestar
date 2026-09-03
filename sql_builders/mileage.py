from dataclasses import dataclass
from datetime import date, datetime

from sql_builders.actives import MILEAGE_COUNTER_TYPE_ID

# counter_active_trigger fires on UPDATE of a counter of MILEAGE_COUNTER_TYPE_ID
# with is_train = true, and its PL/Python body is what generates the
# mileage_train rows day by day. Rolling mileage back means updating the counter
# with the trigger switched off, or it would regenerate what the DELETE just
# removed.
MILEAGE_TRIGGER_NAME = "counter_active_trigger"


@dataclass(frozen=True)
class Correction:
    """A resolved rollback: what to delete and what to reset the counter to.

    counter_date and counter_value both come from the same row — the last
    surviving manual reading (source_row_id/source_date_average), not from the
    selected (deleted) one: the counter is reset to exactly the state of that
    earlier reading, not a mix of one row's value and another's timestamp.

    The delete threshold is source_date_average, not the selected row's own
    date_average: counter_active_trigger fills every day of a gap between two
    manual readings, leaving intermediate mileage_train rows with a null date
    that never show up in the page's list. Deleting only from the selected row
    onward would leave those hidden rows behind; date_average strictly after
    the last surviving row's date_average catches them too.
    """

    id_train: int
    id_active: int
    counter_date: datetime
    counter_value: int
    source_row_id: int
    source_date_average: date
    delete_count: int


def correct_mileage(correction: Correction, wrap_transaction: bool = False) -> list[str]:
    """SQL rolling a train's mileage back to the state after `source_date_average`.

    wrap_transaction adds BEGIN/COMMIT for the downloadable file; when the app
    executes the statements itself the session already owns the transaction, and
    a COMMIT here would end it before a failure could roll the DISABLE back.
    """
    lines = [
        f"ALTER TABLE public.counter_active DISABLE TRIGGER {MILEAGE_TRIGGER_NAME};",
        "",
        "DELETE FROM public.mileage_train",
        f"WHERE id_train = {correction.id_train} "
        f"AND date_average > '{correction.source_date_average:%Y-%m-%d}';",
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
