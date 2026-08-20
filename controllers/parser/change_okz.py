import json
import logging
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response

from db_manager import get_session_maker
from models import CarPlace
from parser_storage import LOG_DIR
from progress_tasks import start_task
from sql_builders import models as models_sql


logger = logging.getLogger("parser")


async def validate_change_model_okz_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'изменить okz в модели' (the public.models table).

    The 'id' column is models.id (the same scheme as 'Изменить lcn в модели');
    'new_position' is a car_place.name resolved into models.id_car_place. The
    optional 'position' column is never checked against the DB — it only
    decides which rows are a no-op (new_position == position) and are skipped
    before any lookup.
    """
    errors: list[dict] = []
    valid_rows: list[dict] = []

    id_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() == "id"),
        None,
    )
    new_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() == "new_position"),
        None,
    )
    old_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() == "position"),
        None,
    )
    if rows and id_column is None:
        errors.append({"row": 0, "field": "id", "message": "В файле не найдена колонка 'id'"})
        return errors, valid_rows
    if rows and new_column is None:
        errors.append({"row": 0, "field": "new_position", "message": "В файле не найдена колонка 'new_position'"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    batch_ids: dict[int, str] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        id_raw = str(row.get(id_column, "") or "").strip()
        new_raw = str(row.get(new_column, "") or "").strip()
        old_raw = str(row.get(old_column, "") or "").strip() if old_column else ""

        if not id_raw:
            errors.append({"row": row_num, "field": "id", "message": "Поле 'id' пустое"})
            continue
        try:
            model_id = int(float(id_raw))
        except ValueError:
            errors.append({"row": row_num, "field": "id", "message": f"Некорректный id: '{id_raw}'"})
            continue

        if not new_raw:
            errors.append({"row": row_num, "field": "new_position", "message": "Пустой new_position"})
            continue

        if new_raw == old_raw:
            continue

        if model_id in batch_ids and batch_ids[model_id] != new_raw:
            errors.append({"row": row_num, "field": "id",
                           "message": (f"Конфликт: id={model_id} уже сопоставлен другому okz "
                                       f"('{batch_ids[model_id]}', а не '{new_raw}')")})
            continue

        result = await db_session.execute(
            text("SELECT id_car_place FROM public.models WHERE id = :id"), {"id": model_id}
        )
        if result.first() is None:
            errors.append({"row": row_num, "field": "id", "message": f"Модель с id={model_id} не найдена"})
            continue

        cp_result = await db_session.execute(select(CarPlace.id).where(CarPlace.name == new_raw))
        matches = cp_result.scalars().all()
        if len(matches) == 0:
            errors.append({"row": row_num, "field": "new_position",
                           "message": f"car_place не найден: '{new_raw}'"})
            continue
        if len(matches) > 1:
            errors.append({"row": row_num, "field": "new_position",
                           "message": (f"car_place неоднозначен: найдено {len(matches)} записей "
                                       f"с именем '{new_raw}' (id: {matches})")})
            continue

        if model_id not in batch_ids:
            batch_ids[model_id] = new_raw
            valid_rows.append({"id": model_id, "new_car_place_id": matches[0]})

    return errors, valid_rows


class ChangeModelOkzController(Controller):
    path = "/parser"

    @post("/change-okz/generate-sql/start")
    async def change_okz_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start background generation of the 'изменить okz в модели' SQL file; returns a task_id to poll."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_change_okz_generate(progress, rows))

    async def _run_change_okz_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_change_model_okz_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = models_sql.change_model_okz(valid_rows)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/change-okz/execute-sql/start")
    async def change_okz_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start the background atomic okz change on models; returns a task_id to poll."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_change_okz_execute(progress, rows))

    async def _run_change_okz_execute(self, progress: dict, rows: list[dict]) -> None:
        total_updated = 0
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await validate_change_model_okz_rows(session, rows, progress=progress)
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для изменения"}])
                    return

                progress.update(processed=0, total=1, phase="executing")
                try:
                    # Two-phase UPDATE (see models_sql.change_model_okz) — both steps must
                    # run in one transaction, or after the first step the models rows are
                    # left with id_car_place = NULL.
                    sql_lines = models_sql.change_model_okz(valid_rows)
                    await session.execute(text(sql_lines[0]))
                    result = await session.execute(text(sql_lines[1]))
                    total_updated = result.rowcount
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
            f"=== Execute change-okz update: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows processed: {len(valid_rows)}, models updated: {total_updated}",
            "",
            *models_sql.change_model_okz(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"change_okz_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Changed okz for %d models, log: %s", total_updated, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Изменено okz у моделей: {total_updated} (строк файла: {len(valid_rows)})")
