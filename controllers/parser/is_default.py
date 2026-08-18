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
from models import Models
from parser_storage import LOG_DIR
from progress_tasks import start_task
from sql_builders import models as models_sql


logger = logging.getLogger("parser")


async def validate_is_default_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'Изменить серийность в модели' (models.is_default).

    The 'id' column is models.id and 'isdefault' the new value
    (true/false/1/0/да/нет, as in _validate_is_serial_1c in
    design_number_parser.py). Switching to true additionally checks both
    partial UNIQUE indexes on models over is_default=true (see
    validate_insert_rows) — otherwise the conflict surfaces as a bare
    UniqueViolationError during execution instead of a clear per-row error.
    """
    errors: list[dict] = []

    id_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() == "id"),
        None,
    )
    isdefault_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("isdefault", "is_default")),
        None,
    )
    if rows and id_column is None:
        errors.append({"row": 0, "field": "id", "message": "В файле не найдена колонка 'id'"})
        return errors, []
    if rows and isdefault_column is None:
        errors.append({"row": 0, "field": "isdefault", "message": "В файле не найдена колонка 'isdefault'"})
        return errors, []

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    models_result = await db_session.execute(
        select(Models.id, Models.id_train_type, Models.lcn, Models.id_car_place,
               Models.id_design_number, Models.is_default)
    )
    models_by_id = {m[0]: m for m in models_result.all()}

    # First pass: parse the columns and dedupe by id — it does not look at the
    # current default sets, so the order of the file's rows cannot influence the
    # result (which matters for the second pass, see below).
    parsed: dict[int, dict] = {}
    order: list[int] = []

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        id_raw = str(row.get(id_column, "") or "").strip()
        isdefault_raw = str(row.get(isdefault_column, "") or "").strip().lower()

        if not id_raw:
            errors.append({"row": row_num, "field": "id", "message": "Поле 'id' пустое"})
            continue
        try:
            model_id = int(float(id_raw))
        except ValueError:
            errors.append({"row": row_num, "field": "id", "message": f"Некорректный id: '{id_raw}'"})
            continue

        if isdefault_raw not in ("true", "false", "1", "0", "да", "нет"):
            errors.append({"row": row_num, "field": "isdefault",
                           "message": f"Неверное значение isdefault: '{isdefault_raw}' (ожидается true/false)"})
            continue
        is_default = isdefault_raw in ("true", "1", "да")

        if model_id in parsed:
            if parsed[model_id]["is_default"] != is_default:
                errors.append({"row": row_num, "field": "id",
                               "message": (f"Конфликт: id={model_id} уже сопоставлен другому значению isdefault "
                                           f"({parsed[model_id]['is_default']}, а не {is_default})")})
            continue

        if model_id not in models_by_id:
            errors.append({"row": row_num, "field": "id", "message": f"Модель с id={model_id} не найдена"})
            continue

        parsed[model_id] = {"row": row_num, "is_default": is_default}
        order.append(model_id)

    # Second pass: UNIQUE collisions on is_default=true (see
    # validate_insert_rows). Models from this same file count by their NEW
    # value from the file, not the current one in the database — otherwise a file
    # that clears the old default and sets a new one at the same (lcn, car_place)
    # or (car_place, train_type, design_number) would raise a false conflict from
    # the arbitrary row order (the same case that makes models_sql.set_is_default
    # sort FALSE before TRUE).
    existing_default_lcn_car: dict[tuple, int] = {}
    existing_default_car_type_design: dict[tuple, int] = {}
    for mid, m in models_by_id.items():
        if mid in parsed or not m[5]:
            continue
        existing_default_lcn_car[(m[2], m[3])] = mid
        existing_default_car_type_design[(m[3], m[1], m[4])] = mid

    batch_lcn_car: dict[tuple, int] = {}
    batch_car_type_design: dict[tuple, int] = {}
    valid_ids: list[int] = []

    for model_id in order:
        info = parsed[model_id]
        row_num = info["row"]
        is_default = info["is_default"]
        _, id_train_type, lcn, id_car_place, id_design_number, _ = models_by_id[model_id]

        if is_default:
            lcn_car = (lcn, id_car_place)
            car_type_design = (id_car_place, id_train_type, id_design_number)
            owner = existing_default_lcn_car.get(lcn_car, batch_lcn_car.get(lcn_car))
            if owner is not None and owner != model_id:
                errors.append({"row": row_num, "field": "isdefault",
                               "message": (f"Конфликт unique (lcn, car_place) WHERE is_default=true: "
                                           f"lcn='{lcn}', car_place={id_car_place} уже заняты")})
                continue
            owner2 = existing_default_car_type_design.get(car_type_design, batch_car_type_design.get(car_type_design))
            if owner2 is not None and owner2 != model_id:
                errors.append({"row": row_num, "field": "isdefault",
                               "message": (f"Конфликт unique (car_place, train_type, design_number) WHERE is_default=true: "
                                           f"car_place={id_car_place}, train_type={id_train_type}, "
                                           f"design_number={id_design_number} уже заняты")})
                continue
            batch_lcn_car[lcn_car] = model_id
            batch_car_type_design[car_type_design] = model_id

        valid_ids.append(model_id)

    valid_rows = [{"id": mid, "is_default": parsed[mid]["is_default"]} for mid in valid_ids]
    return errors, valid_rows


class IsDefaultController(Controller):
    path = "/parser"

    @post("/is-default/generate-sql/start")
    async def is_default_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start background generation of the 'Изменить серийность в модели' SQL file; returns a task_id."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_is_default_generate(progress, rows))

    async def _run_is_default_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_is_default_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = models_sql.set_is_default(valid_rows)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/is-default/execute-sql/start")
    async def is_default_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start the background atomic is_default change on models; returns a task_id."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_is_default_execute(progress, rows))

    async def _run_is_default_execute(self, progress: dict, rows: list[dict]) -> None:
        total_updated = 0
        valid_rows: list[dict] = []
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await validate_is_default_rows(session, rows, progress=progress)
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для изменения"}])
                    return

                # FALSE rows before TRUE ones (see models_sql.set_is_default) — one
                # UPDATE per model, each its own transaction step, so the order stays
                # predictable for the partial UNIQUE index over is_default=true.
                sql_lines = models_sql.set_is_default(valid_rows)
                progress.update(processed=0, total=len(sql_lines), phase="executing")
                try:
                    for i, line in enumerate(sql_lines, start=1):
                        result = await session.execute(text(line))
                        total_updated += result.rowcount
                        progress["processed"] = i
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
            f"=== Execute is-default update: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows processed: {len(valid_rows)}, models updated: {total_updated}",
            "",
            *models_sql.set_is_default(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"is_default_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Changed is_default for %d models, log: %s", total_updated, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Изменена серийность (is_default) у моделей: {total_updated} (строк файла: {len(valid_rows)})")
