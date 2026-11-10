from datetime import datetime, timedelta, timezone
from typing import Any

from dateutil import parser as date_parser
from litestar import Controller, post
from litestar.params import MultipartBody
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from models import Actives, CounterType, Ptoir, PtoirLevelWarning
from sql_builders import ptoir as ptoir_sql

from .common import PATH, logger

# Excel dates are entered in Moscow time while date_activation/zero_point_value
# are stored in UTC (see the old update_ptoir function) — hence the 3 hour shift
# everywhere.
MSK_OFFSET = timedelta(hours=3)


async def validate_launch_rows(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
) -> tuple[list[dict], list[tuple[int, datetime, int, int, int]]]:
    """Validate the Excel rows for a maintenance (ПТОиР) run.

    Returns (errors, valid_rows), where valid_rows is a list of tuples
    (id_ptoir, date_activation, interval, id_level_warning, zero_point_value).
    When a progress dict is passed, (processed, total, phase="validating")
    is written into it every few rows for the frontend to poll.
    """
    errors: list[dict] = []
    valid_rows: list[tuple[int, datetime, int, int, int]] = []

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num
        number_ptoir = str(row.get("ПТОиР", "") or "").strip()
        active_number = str(row.get("Актив", "") or "").strip()
        type_counter = str(row.get("Тип счетчика", "") or "").strip()
        interval_raw: Any = row.get("Интервал")
        date_raw = row.get("Дата активации")
        service_raw = row.get("Данные последнего обслуживания")

        if not number_ptoir:
            errors.append({"row": row_num, "field": "ПТОиР", "message": "Поле 'ПТОиР' пустое"})
            continue

        result = await db_session.execute(select(Ptoir.id, Ptoir.id_active).where(Ptoir.number_ptoir == number_ptoir))
        ptoir_row = result.first()
        if ptoir_row is None:
            errors.append({"row": row_num, "field": "ПТОиР", "message": f"ПТОиР не найден: '{number_ptoir}'"})
            continue
        ptoir_id, ptoir_active_id = ptoir_row

        if active_number:
            result = await db_session.execute(select(Actives.id).where(Actives.active_number == active_number))
            active_id = result.scalar_one_or_none()
            if active_id is None:
                errors.append({"row": row_num, "field": "Актив", "message": f"Актив не найден: '{active_number}'"})
                continue
            if ptoir_active_id is not None and active_id != ptoir_active_id:
                errors.append(
                    {
                        "row": row_num,
                        "field": "Актив",
                        "message": (f"Актив '{active_number}' не соответствует активу ПТОиР '{number_ptoir}'"),
                    }
                )
                continue

        if not type_counter:
            errors.append({"row": row_num, "field": "Тип счетчика", "message": "Поле 'Тип счетчика' пустое"})
            continue

        result = await db_session.execute(select(CounterType.id).where(CounterType.type == type_counter))
        counter_type_id = result.scalar_one_or_none()
        if counter_type_id is None:
            errors.append(
                {"row": row_num, "field": "Тип счетчика", "message": f"Тип счетчика не найден: '{type_counter}'"}
            )
            continue

        result = await db_session.execute(
            select(PtoirLevelWarning.id).where(
                (PtoirLevelWarning.id_ptoir == ptoir_id) & (PtoirLevelWarning.id_counter_type == counter_type_id)
            )
        )
        level_warning_id = result.scalar_one_or_none()
        if level_warning_id is None:
            errors.append(
                {
                    "row": row_num,
                    "field": "*",
                    "message": (
                        f"Уровень предупреждения не найден для ПТОиР '{number_ptoir}' и типа счетчика '{type_counter}'"
                    ),
                }
            )
            continue

        try:
            if isinstance(date_raw, datetime):
                date_activation = date_raw - MSK_OFFSET
            else:
                date_activation = date_parser.parse(str(date_raw), dayfirst=True) - MSK_OFFSET
        except Exception:
            errors.append(
                {"row": row_num, "field": "Дата активации", "message": f"Некорректная дата активации: '{date_raw}'"}
            )
            continue

        try:
            interval = int(interval_raw)
        except (TypeError, ValueError):
            errors.append({"row": row_num, "field": "Интервал", "message": f"Некорректный интервал: '{interval_raw}'"})
            continue

        if isinstance(service_raw, (int, float)) and not isinstance(service_raw, bool):
            zero_point_value = int(service_raw)
        elif str(service_raw).strip().isdecimal():
            # a number stored as text in Excel
            zero_point_value = int(str(service_raw).strip())
        else:
            try:
                service_dt = date_parser.parse(str(service_raw), dayfirst=True) - MSK_OFFSET
                service_dt = service_dt.replace(tzinfo=timezone.utc)
                zero_point_value = int(service_dt.timestamp())
            except Exception:
                errors.append(
                    {
                        "row": row_num,
                        "field": "Данные последнего обслуживания",
                        "message": f"Некорректное значение обслуживания: '{service_raw}'",
                    }
                )
                continue

        valid_rows.append((ptoir_id, date_activation, interval, level_warning_id, zero_point_value))

    return errors, valid_rows


def _launch_summary(total: int, valid_rows: list, errors: list[dict], rowcounts: list[int]) -> tuple[str, list[str]]:
    return f"Успешно обновлено {len(valid_rows)} ПТОиР", [f"Rows updated: {len(valid_rows)}"]


LAUNCH_PTOIR = sql_tasks.SqlOperation(
    validate=validate_launch_rows,
    build=ptoir_sql.update_ptoir,
    log_name="update_ptoir",
    log_title="PTOиR update",
    summary=_launch_summary,
    empty_message="Нет валидных строк для обновления",
    logger=logger,
)


class LaunchPtoirController(Controller):
    path = PATH

    @post("/generate-sql/start")
    async def generate_sql_start(self, data: MultipartBody[dict]) -> Response:
        return sql_tasks.start_generate(data, LAUNCH_PTOIR)

    @post("/execute-sql/start")
    async def execute_sql_start(self, data: MultipartBody[dict]) -> Response:
        return sql_tasks.start_execute(data, LAUNCH_PTOIR)
