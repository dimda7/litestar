from decimal import Decimal, InvalidOperation

from litestar import Controller, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from models import Actives, CounterActive, Ptoir
from sql_builders import ptoir as ptoir_sql

from .common import PATH, find_column, logger

HOURS_MULTIPLIER = 10


def _parse_hours(raw: object) -> int | None:
    """'18635,2' -> 186352; None unless the value is a non-negative number with at most one decimal."""
    if isinstance(raw, bool):
        return None
    try:
        scaled = Decimal(str(raw).strip().replace(",", ".")) * HOURS_MULTIPLIER
    except InvalidOperation:
        return None
    if not scaled.is_finite() or scaled < 0 or scaled != scaled.to_integral_value():
        return None
    return int(scaled)


async def validate_change_hours_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'изменение моточасов'.

    'птоир' is ptoir.number_ptoir; 'значение' times HOURS_MULTIPLIER becomes
    counter_active.value of the ПТОиР's asset. The optional 'актив' column is
    only checked against that asset. valid_rows holds one entry per asset and
    carries the old value/value_source for the execution log.
    """
    errors: list[dict] = []
    valid_rows: list[dict] = []

    ptoir_column = find_column(rows, "птоир")
    value_column = find_column(rows, "значение")
    active_column = find_column(rows, "актив")
    if rows and ptoir_column is None:
        errors.append({"row": 0, "field": "птоир", "message": "В файле не найдена колонка 'птоир'"})
        return errors, valid_rows
    if rows and value_column is None:
        errors.append({"row": 0, "field": "значение", "message": "В файле не найдена колонка 'значение'"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    batch_ptoirs: dict[str, int] = {}
    batch_actives: dict[int, tuple[int, str]] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        number_ptoir = str(row.get(ptoir_column, "") or "").strip()
        value_raw = row.get(value_column)
        active_raw = str(row.get(active_column, "") or "").strip() if active_column else ""

        if not number_ptoir:
            errors.append({"row": row_num, "field": "птоир", "message": "Поле 'птоир' пустое"})
            continue

        if value_raw is None or str(value_raw).strip() == "":
            errors.append({"row": row_num, "field": "значение", "message": "Поле 'значение' пустое"})
            continue
        value = _parse_hours(value_raw)
        if value is None:
            errors.append({"row": row_num, "field": "значение",
                           "message": (f"Некорректное значение: '{value_raw}' (ожидается неотрицательное "
                                       f"число, не более одного знака после запятой)")})
            continue

        if number_ptoir in batch_ptoirs:
            if batch_ptoirs[number_ptoir] != value:
                errors.append({"row": row_num, "field": "птоир",
                               "message": (f"Конфликт: ПТОиР '{number_ptoir}' уже сопоставлен другому значению "
                                           f"({batch_ptoirs[number_ptoir]}, а не {value})")})
            continue

        result = await db_session.execute(
            select(Ptoir.id_active, Actives.active_number)
            .outerjoin(Actives, Actives.id == Ptoir.id_active)
            .where(Ptoir.number_ptoir == number_ptoir)
        )
        ptoir_row = result.first()
        if ptoir_row is None:
            errors.append({"row": row_num, "field": "птоир", "message": f"ПТОиР не найден: '{number_ptoir}'"})
            continue
        id_active, active_number = ptoir_row
        if id_active is None:
            errors.append({"row": row_num, "field": "птоир", "message": f"У ПТОиР '{number_ptoir}' нет актива"})
            continue

        if active_raw and active_raw != active_number:
            errors.append({"row": row_num, "field": "актив",
                           "message": (f"Актив '{active_raw}' не соответствует активу ПТОиР "
                                       f"'{number_ptoir}' ('{active_number}')")})
            continue

        if id_active in batch_actives:
            other_value, other_ptoir = batch_actives[id_active]
            if other_value != value:
                errors.append({"row": row_num, "field": "значение",
                               "message": (f"Конфликт: актив '{active_number}' уже получил другое значение "
                                           f"от ПТОиР '{other_ptoir}' ({other_value}, а не {value})")})
                continue
            batch_ptoirs[number_ptoir] = value
            continue

        result = await db_session.execute(
            select(CounterActive.value, CounterActive.value_source).where(
                (CounterActive.id_active == id_active)
                & (CounterActive.id_counter_type == ptoir_sql.HOURS_COUNTER_TYPE_ID)
            )
        )
        counter_row = result.first()
        if counter_row is None:
            errors.append({"row": row_num, "field": "птоир",
                           "message": f"У актива '{active_number}' (ПТОиР '{number_ptoir}') нет счётчика моточасов"})
            continue

        batch_ptoirs[number_ptoir] = value
        batch_actives[id_active] = (value, number_ptoir)
        valid_rows.append({"id_active": id_active, "value": value, "number_ptoir": number_ptoir,
                           "active_number": active_number, "old_value": counter_row[0],
                           "old_value_source": counter_row[1]})

    return errors, valid_rows


def _change_hours_summary(total: int, valid_rows: list[dict], errors: list[dict]) -> tuple[str, list[str]]:
    return (
        f"Изменены моточасы у активов: {len(valid_rows)} (строк файла: {total})",
        [
            f"Rows in file: {total}, counters updated: {len(valid_rows)}",
            "",
            *(
                f"{vr['number_ptoir']} {vr['active_number']}: value {vr['old_value']} "
                f"(value_source {vr['old_value_source']}) -> {vr['value']}"
                for vr in valid_rows
            ),
        ],
    )


# One transaction on the raw asyncpg connection: a plain SET is rolled back with it,
# so a failed UPDATE cannot hand the pooled (pgbouncer) connection back with the
# triggers still off.
CHANGE_HOURS = sql_tasks.SqlOperation(
    validate=validate_change_hours_rows, build=ptoir_sql.change_hours,
    log_name="ptoir_hours", log_title="change-hours update", summary=_change_hours_summary,
    empty_message="Нет валидных строк для изменения", logger=logger,
)


class ChangeHoursController(Controller):
    path = PATH

    @post("/change-hours/generate-sql/start")
    async def generate_sql_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        return sql_tasks.start_generate(data, CHANGE_HOURS)

    @post("/change-hours/execute-sql/start")
    async def execute_sql_start(self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)) -> Response:
        return sql_tasks.start_execute(data, CHANGE_HOURS)
