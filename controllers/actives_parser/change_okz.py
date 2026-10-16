import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.response import Response

from db_manager import get_session_maker
from models import Actives, CarPlace
import excel_upload
from parser_storage import LOG_DIR
from progress_tasks import error_response, start_task
from sql_builders import actives as actives_sql

from .common import PREFIX


logger = logging.getLogger("actives_parser")


async def validate_change_okz(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[tuple[str, int]]]:
    """Validate rows for 'изменить окз': public.location.id_car_place of each asset.

    The asset is found by 'Актив' (actives.active_number), its location through
    actives.id_location; the car place by its name from 'position' (or 'окз').
    A row with an empty position (the train itself has none) is skipped.
    Returns (errors, valid_rows) where valid_rows is [(active_number, id_car_place), ...]
    """
    errors: list[dict] = []
    valid_rows: list[tuple[str, int]] = []

    position_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("position", "окз")),
        None,
    )
    if rows and position_column is None:
        errors.append({"row": 0, "field": "position",
                       "message": "В файле не найдена колонка 'position' (или 'окз')"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    batch_positions: dict[str, str] = {}
    car_place_ids: dict[str, list[int]] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num
        active_number = str(row.get("Актив", "") or "").strip()
        position = str(row.get(position_column, "") or "").strip()

        if not active_number:
            errors.append({"row": row_num, "field": "Актив", "message": "Поле 'Актив' пустое"})
            continue

        if not position:
            continue

        if active_number in batch_positions:
            if batch_positions[active_number] != position:
                errors.append({"row": row_num, "field": "Актив",
                               "message": (f"Конфликт: актив '{active_number}' уже сопоставлен другому окз "
                                           f"('{batch_positions[active_number]}', а не '{position}')")})
            continue

        result = await db_session.execute(
            select(Actives.id_location).where(Actives.active_number == active_number)
        )
        locations = result.scalars().all()
        if not locations:
            errors.append({"row": row_num, "field": "Актив",
                           "message": f"Актив не найден: '{active_number}'"})
            continue
        if locations[0] is None:
            errors.append({"row": row_num, "field": "Актив",
                           "message": f"У актива '{active_number}' нет location"})
            continue

        if position not in car_place_ids:
            cp_result = await db_session.execute(select(CarPlace.id).where(CarPlace.name == position))
            car_place_ids[position] = list(cp_result.scalars().all())
        matches = car_place_ids[position]
        if len(matches) == 0:
            errors.append({"row": row_num, "field": "position",
                           "message": f"car_place не найден: '{position}'"})
            continue
        if len(matches) > 1:
            errors.append({"row": row_num, "field": "position",
                           "message": (f"car_place неоднозначен: найдено {len(matches)} записей "
                                       f"с именем '{position}' (id: {matches})")})
            continue

        batch_positions[active_number] = position
        valid_rows.append((active_number, matches[0]))

    return errors, valid_rows


class ChangeOkzController(Controller):
    path = "/actives-parser"

    @post("/change-okz/generate-sql/start")
    async def change_okz_generate_sql_start(self, request: Request) -> Response:
        """Start background generation of the location.id_car_place update SQL file; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_change_okz_generate(progress, rows))

    async def _run_change_okz_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_change_okz(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}])
            return

        sql_lines = actives_sql.change_okz(valid_rows)
        progress.update(status="done", sql="\n".join(sql_lines), count=len(valid_rows))

    @post("/change-okz/execute/start")
    async def change_okz_execute_start(self, request: Request) -> Response:
        """Start the background location.id_car_place update in the database; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_change_okz_execute(progress, rows))

    async def _run_change_okz_execute(self, progress: dict, rows: list[dict]) -> None:
        valid_rows: list[tuple[str, int]] = []
        updated = 0
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_change_okz(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}])
                    return

                progress.update(processed=0, total=1, phase="executing")

                try:
                    conn = await session.connection()
                    result = await conn.exec_driver_sql(actives_sql.change_okz(valid_rows)[0])
                    updated = result.rowcount
                    progress["processed"] = 1
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
                    return
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
            return

        now = datetime.now()
        log_lines = [
            f"=== Change okz (location.id_car_place): {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows processed: {len(valid_rows)}, locations updated: {updated}",
            "",
            *actives_sql.change_okz(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"change_okz_actives_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Changed okz for %d assets, log: %s", updated, log_file)

        progress.update(status="done", count=len(valid_rows),
                        message=f"Изменено окз у активов: {updated} (строк файла: {len(valid_rows)})")
