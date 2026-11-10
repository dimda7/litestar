# `/mileage-correction` — Корректировка введенного пробега

Design decisions behind this page's operations. Moved from CLAUDE.md; keep them up to date when changing the operation.

## Overview

"Корректировка введенного пробега" (`controllers/mileage_correction.py`) rolls a train's mileage back to the state of the last manually-entered reading before a selected one, and resets the counter — **with `counter_active_trigger` disabled**, because that trigger's PL/Python body (`counter_active_python`) is what generates the `mileage_train` rows in the first place: with it on, the `UPDATE` regenerates what the `DELETE` just removed. The counter row is addressed by `id_active = train.active AND id_counter_type = 3 AND is_train = true`, never by `counter_active.id`.

## Which row is kept, and the DELETE boundary

The trigger fills **every calendar day** of a gap between two manual readings, and only the day matching the newly-entered reading gets a non-null `date` — the interior days get `date IS NULL` and never appear in the page's list (filtered on `date is not null`). `resolve_correction` therefore rolls back to the last row with a non-null `date` before the selected one (never the closest row by `date_average` alone, which could be one of those hidden interior rows — see `test_null_date_row_is_not_picked_as_the_previous_state`), and the generated `DELETE` uses `date_average > <that kept row's date_average>` (strictly, against the *kept* row), not `>=` against the selected row's own date — otherwise a hidden interior row between the kept and the selected one survives the delete untouched. The counter's new `value` **and** `date` both come from that same kept row (never the selected one) — `SET value, date` restores the counter to exactly that row's own state, not a mix of one row's value and another's timestamp.

## Schema facts behind the tests

Two schema facts shape the tests: a UNIQUE index on `counter_active (id_active, id_counter_type)` makes the "several counters" branch unreachable in production (defensive only, covered on SQLite), and `actives_trgger` creates the `counter_active` rows itself on asset INSERT with `is_train = false` — so `tests/pg/` adopts the trigger-made row instead of inserting one, in a single `UPDATE` that `counter_active_trigger`'s `WHEN` (which reads `OLD`) does not fire. The downloadable `BEGIN; … COMMIT;` variant is never run in `tests/pg/`: its `COMMIT` would end the fixture's own transaction and commit the test data.
