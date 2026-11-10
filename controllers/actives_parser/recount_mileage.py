import logging
from datetime import date, timedelta

from litestar import Controller, post
from litestar.connection.request import Request
from litestar.response import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

import excel_upload
import sql_tasks
from models import Actives, CounterActive, Location, MileageStart, MileageTrain, Relocate
from progress_tasks import error_response
from sql_builders import actives as actives_sql
from sql_builders.actives import MILEAGE_COUNTER_TYPE_ID

from .common import PREFIX, ActivesRepository, CounterActiveRepository, MileageStartRepository

logger = logging.getLogger("actives_parser")


# Date of the historical mileage accounting migration: asset moves before it feed
# the recomputed mileage_start.milage, and train mileage is summed up to it inclusive.
MILEAGE_RECOUNT_CUTOFF = date(2022, 5, 13)


# relocate.date is stored without a timezone (UTC); for comparison against the
# cutoff it is converted to Moscow time, as in the old peewee script.
MILEAGE_TZ_SHIFT = timedelta(hours=3)


async def validate_recount_mileage(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the rows of a "пересчитать пробег" file (replaces update_milage_start + recount_counter).

    For each asset it computes a total correction to mileage_start.milage from the
    relocate history: moves before MILEAGE_RECOUNT_CUTOFF from storage onto a train
    add that train's mileage (mileage_train.mileage_average over the period from the
    move to the cutoff), moves from a train into storage subtract it. The
    milage_const and counter_active.value values themselves are not computed here —
    they are read and computed in the database when the SQL runs (see
    actives_sql.recount_mileage).

    The optional 'milage_const' column (replaces update_milage_start_const): a
    non-empty, non-zero value is written into mileage_start.milage_const before the
    recount; for an asset without a mileage_start row one is created (INSERT), in
    which case a missing row is not an error. A value of 0 is ignored, as in the old
    script. Column headers are matched case-insensitively and ignoring edge
    whitespace (real files contain 'mileage_const\\xa0'), and alternative spellings
    are accepted: the asset column may be 'Актив' or 'active_number', the constant
    milage_const or mileage_const.
    """
    actives_repo = ActivesRepository(session=db_session)
    mileage_start_repo = MileageStartRepository(session=db_session)
    counter_repo = CounterActiveRepository(session=db_session)

    errors: list[dict] = []
    valid_rows: list[dict] = []
    batch_numbers: set[str] = set()

    active_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("актив", "active_number")),
        None,
    )
    if rows and active_column is None:
        errors.append(
            {"row": 0, "field": "Актив", "message": "В файле не найдена колонка 'Актив' (или 'active_number')"}
        )
        return errors, valid_rows
    const_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("milage_const", "mileage_const")),
        None,
    )

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    loc_old = aliased(Location)
    loc_new = aliased(Location)

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None:
            progress["processed"] = row_num

        active_number = str(row.get(active_column, "") or "").strip()
        if not active_number:
            errors.append({"row": row_num, "field": "Актив", "message": "Поле 'Актив' пустое"})
            continue

        if active_number in batch_numbers:
            errors.append({"row": row_num, "field": "Актив", "message": f"Дубликат внутри файла: '{active_number}'"})
            continue

        active = await actives_repo.get_one_or_none(Actives.active_number == active_number)
        if active is None:
            errors.append({"row": row_num, "field": "Актив", "message": f"Актив не найден: '{active_number}'"})
            continue
        is_train = active.id_unit_type == 1

        milage_const: int | None = None
        if const_column is not None:
            const_raw = row.get(const_column)
            if const_raw is not None and str(const_raw).strip() != "":
                try:
                    milage_const = int(float(const_raw))
                except (TypeError, ValueError):
                    errors.append(
                        {
                            "row": row_num,
                            "field": "milage_const",
                            "message": f"Некорректное значение milage_const: '{const_raw}'",
                        }
                    )
                    continue
                if milage_const == 0:
                    milage_const = None

        mileage_start_rows = await mileage_start_repo.get_many(MileageStart.id_active == active.id)
        if len(mileage_start_rows) > 1:
            errors.append(
                {
                    "row": row_num,
                    "field": "Актив",
                    "message": f"Несколько записей mileage_start для актива '{active_number}' — неоднозначно",
                }
            )
            continue
        # Without milage_const the mileage_start row must exist; with it the row is
        # created by the SQL (INSERT), so a missing one is not an error.
        if not mileage_start_rows and milage_const is None:
            errors.append(
                {
                    "row": row_num,
                    "field": "Актив",
                    "message": f"Запись mileage_start не найдена для актива '{active_number}'",
                }
            )
            continue

        if not is_train:
            counters = await counter_repo.get_many(
                CounterActive.id_active == active.id,
                CounterActive.id_counter_type == MILEAGE_COUNTER_TYPE_ID,
            )
            if not counters:
                errors.append(
                    {
                        "row": row_num,
                        "field": "Актив",
                        "message": f"Счётчик пробега (counter_type={MILEAGE_COUNTER_TYPE_ID}) не найден для актива '{active_number}'",
                    }
                )
                continue
            if len(counters) > 1:
                errors.append(
                    {
                        "row": row_num,
                        "field": "Актив",
                        "message": f"Несколько счётчиков пробега для актива '{active_number}' — неоднозначно",
                    }
                )
                continue

        relocates = (
            await db_session.execute(
                select(
                    Relocate.date,
                    loc_old.id_train.label("id_train_old"),
                    loc_new.id_train.label("id_train_new"),
                )
                .join(loc_old, Relocate.id_location_old == loc_old.id, isouter=True)
                .join(loc_new, Relocate.id_location_new == loc_new.id, isouter=True)
                .where(Relocate.id_active == active.id)
                .order_by(Relocate.date)
            )
        ).all()

        total = 0
        for relocate_date, id_train_old, id_train_new in relocates:
            if relocate_date is None:
                continue
            if (relocate_date + MILEAGE_TZ_SHIFT).date() >= MILEAGE_RECOUNT_CUTOFF:
                continue
            # Only storage<->train moves count; train->train and storage->storage
            # do not change mileage (as in the old script).
            if id_train_old is not None and id_train_new is None:
                train_id, sign = id_train_old, -1
            elif id_train_old is None and id_train_new is not None:
                train_id, sign = id_train_new, 1
            else:
                continue

            train_mileage = (
                await db_session.execute(
                    select(func.sum(MileageTrain.mileage_average)).where(
                        MileageTrain.id_train == train_id,
                        MileageTrain.date_average > relocate_date,
                        MileageTrain.date_average <= MILEAGE_RECOUNT_CUTOFF,
                    )
                )
            ).scalar()
            total += sign * (train_mileage or 0)

        batch_numbers.add(active_number)
        valid_rows.append(
            {
                "row_num": row_num,
                "active_number": active_number,
                "id_active": active.id,
                "total": total,
                "is_train": is_train,
                "milage_const": milage_const,
                "insert_mileage_start": not mileage_start_rows,
            }
        )

    return errors, valid_rows


RECOUNT_MILEAGE = sql_tasks.SqlOperation(
    validate=validate_recount_mileage,
    build=actives_sql.recount_mileage,
    log_name="recount_mileage",
    log_title="recount mileage",
    summary=lambda total, valid_rows, errors, rowcounts: (
        f"Пробег пересчитан для {len(valid_rows)} активов",
        [f"Actives recounted: {len(valid_rows)}"],
    ),
    empty_message="Нет валидных строк для пересчёта",
    logger=logger,
)


class RecountMileageController(Controller):
    path = "/actives-parser"

    @post("/recount-mileage/generate-sql/start")
    async def recount_mileage_generate_sql_start(self, request: Request) -> Response:
        """Start background generation of the mileage recount SQL file; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")
        return sql_tasks.generate_rows(rows, RECOUNT_MILEAGE)

    @post("/recount-mileage/execute/start")
    async def recount_mileage_execute_start(self, request: Request) -> Response:
        """Start the background mileage recount in the database; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")
        return sql_tasks.execute_rows(rows, RECOUNT_MILEAGE)
