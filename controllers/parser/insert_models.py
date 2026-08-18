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
from models import TrainType, CarPlace, DesignNumber, Models
from parser_storage import LOG_DIR
from progress_tasks import start_task
from sql_builders import models as models_sql


logger = logging.getLogger("parser")


async def validate_insert_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict[str, str]], list[tuple[int, int, int, str, bool]]]:
    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    existing_rows = await db_session.execute(
        select(Models.id_train_type, Models.lcn, Models.id_car_place,
               Models.id_design_number, Models.is_default)
    )
    existing_set: set[tuple] = set()
    for er in existing_rows.all():
        existing_set.add((er[0], er[1], er[2], er[3], er[4]))

    existing_default_lcn_car: set[tuple] = set()
    existing_default_car_type_design: set[tuple] = set()
    for er in existing_set:
        if er[4]:
            existing_default_lcn_car.add((er[1], er[2]))
            existing_default_car_type_design.add((er[2], er[0], er[3]))

    errors: list[dict[str, str]] = []
    valid_rows: list[tuple[int, int, int, str, bool]] = []

    batch_full: set[tuple] = set()
    batch_default_lcn_car: set[tuple] = set()
    batch_default_car_type_design: set[tuple] = set()

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None:
            progress["processed"] = row_num
        model_name = str(row.get("model", "")).strip()
        position = str(row.get("position", "")).strip()
        itemnum = str(row.get("itemnum", "")).strip()
        lcn = str(row.get("lsn", "") or row.get("lcn", "")).strip()
        isdefault = str(row.get("isdefault", "")).strip().lower()
        is_default = isdefault == "true"

        train_type_id: int | None = None
        if model_name:
            result = await db_session.execute(
                select(TrainType.id).where(TrainType.name == model_name)
            )
            r = result.scalar_one_or_none()
            if r is not None:
                train_type_id = r
            else:
                errors.append({"row": row_num, "field": "model",
                               "message": f"train_type не найден: '{model_name}'"})

        car_place_id: int | None = None
        if position and position != "null":
            result = await db_session.execute(
                select(CarPlace.id).where(CarPlace.name == position)
            )
            matches = result.scalars().all()
            if len(matches) == 1:
                car_place_id = matches[0]
            elif len(matches) == 0:
                errors.append({"row": row_num, "field": "position",
                               "message": f"car_place не найден: '{position}'"})
            else:
                errors.append({"row": row_num, "field": "position",
                               "message": (f"car_place неоднозначен: найдено {len(matches)} записей "
                                           f"с именем '{position}' (id: {matches})")})

        design_number_id: int | None = None
        if itemnum:
            result = await db_session.execute(
                select(DesignNumber.id).where(DesignNumber.number == itemnum)
            )
            r = result.scalar_one_or_none()
            if r is not None:
                design_number_id = r
            else:
                errors.append({"row": row_num, "field": "itemnum",
                               "message": f"design_number не найден: '{itemnum}'"})

        if train_type_id is None or car_place_id is None or design_number_id is None:
            continue

        full_tuple = (train_type_id, lcn, car_place_id, design_number_id, is_default)
        if full_tuple in existing_set or full_tuple in batch_full:
            errors.append({
                "row": row_num, "field": "*",
                "message": (f"Дубликат: строка (train_type={train_type_id}, lcn='{lcn}', "
                            f"car_place={car_place_id}, design_number={design_number_id}, "
                            f"is_default={is_default}) уже существует"),
            })
            continue

        if is_default:
            if (lcn, car_place_id) in existing_default_lcn_car or (lcn, car_place_id) in batch_default_lcn_car:
                errors.append({
                    "row": row_num, "field": "lcn",
                    "message": (f"Конфликт unique (lcn, car_place) WHERE is_default=true: "
                                f"lcn='{lcn}', car_place={car_place_id} уже заняты"),
                })
                continue
            if ((car_place_id, train_type_id, design_number_id) in existing_default_car_type_design
                    or (car_place_id, train_type_id, design_number_id) in batch_default_car_type_design):
                errors.append({
                    "row": row_num, "field": "*",
                    "message": (f"Конфликт unique (car_place, train_type, design_number) WHERE is_default=true: "
                                f"car_place={car_place_id}, train_type={train_type_id}, "
                                f"design_number={design_number_id} уже заняты"),
                })
                continue

        batch_full.add(full_tuple)
        if is_default:
            batch_default_lcn_car.add((lcn, car_place_id))
            batch_default_car_type_design.add((car_place_id, train_type_id, design_number_id))

        valid_rows.append((train_type_id, car_place_id, design_number_id, lcn, is_default))

    return errors, valid_rows


class InsertModelsController(Controller):
    path = "/parser"

    @post("/generate-sql/start")
    async def generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start background generation of the SQL file inserting rows into models; returns a task_id to poll."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_insert_generate(progress, rows))

    async def _run_insert_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_insert_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = models_sql.insert_models(valid_rows)
        progress.update(status="done", sql="\n".join(sql_lines), count=len(valid_rows))

    @post("/execute-sql/start")
    async def execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start the background atomic insert of rows into public.models; returns a task_id to poll."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        skip_errors = str(data.get("skip_errors", "")).strip().lower() == "true"
        return start_task(len(rows), lambda progress: self._run_insert_execute(progress, rows, skip_errors=skip_errors))

    async def _run_insert_execute(self, progress: dict, rows: list[dict], skip_errors: bool = False) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await validate_insert_rows(session, rows, progress=progress)
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors and not skip_errors:
                    progress.update(status="confirm_errors", errors=errors, valid_count=len(valid_rows))
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для вставки"}])
                    return

                progress.update(processed=0, total=len(valid_rows), phase="executing")
                try:
                    for i, (train_type_id, car_place_id, design_number_id, lcn, is_default) in enumerate(valid_rows, start=1):
                        await session.execute(
                            text(
                                "INSERT INTO public.models (id_train_type, id_car_place, id_design_number, lcn, is_default) "
                                "VALUES (:tt, :cp, :dn, :lcn, :def)"
                            ),
                            {"tt": train_type_id, "cp": car_place_id, "dn": design_number_id, "lcn": lcn, "def": is_default},
                        )
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
            f"=== Execute SQL: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows inserted: {len(valid_rows)}",
            "",
            *models_sql.insert_models(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"insert_models_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("SQL executed: %d rows inserted, log saved to %s", len(valid_rows), log_file)

        message = f"Успешно вставлено {len(valid_rows)} строк"
        if errors:
            message += f" (пропущено с ошибками: {len(errors)})"
        progress.update(status="done", count=len(valid_rows), message=message, errors=errors)
