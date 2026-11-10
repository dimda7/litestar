import json
import logging
from datetime import datetime

from litestar import Controller, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy import String, and_, cast, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from db_manager import get_session_maker
from models import DesignNumber, Models
from parser_storage import LOG_DIR
from progress_tasks import start_task
from sql_builders import models as models_sql

from .common import column_key, execute_sql_lines, parse_model_id, row_text

logger = logging.getLogger("parser")


def _shown(number: str | None) -> str:
    return "NULL" if number is None else f"'{number}'"


def _index_keys(model: Row, id_design_number: int) -> list[tuple]:
    # The two unique indexes of public.models that contain id_design_number.
    # A NULL never collides in a Postgres unique index, so a key with one is no key.
    keys: list[tuple] = []
    default_key = ("default", model.id_car_place, model.id_train_type, id_design_number)
    if model.is_default and None not in default_key:
        keys.append(default_key)
    full_key = ("full", model.id_train_type, model.lcn, model.id_car_place, id_design_number, model.is_default)
    if None not in full_key:
        keys.append(full_key)
    return keys


async def _index_conflict(db_session: AsyncSession, model_id: int, id_design_number: int) -> int | None:
    model, other = aliased(Models), aliased(Models)
    return (
        await db_session.execute(
            select(other.id)
            .join(model, model.id == model_id)
            .where(
                other.id != model_id,
                other.id_design_number == id_design_number,
                other.id_train_type == model.id_train_type,
                other.id_car_place == model.id_car_place,
                or_(
                    and_(model.is_default.is_(True), other.is_default.is_(True)),
                    and_(other.lcn == model.lcn, other.is_default == model.is_default),
                ),
            )
            .order_by(other.id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def validate_change_model_design_number_rows(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    valid_rows: list[dict] = []
    id_column = column_key(rows, "id")
    new_column = column_key(rows, "new_itemnum")
    old_column = column_key(rows, "itemnum")

    if rows and id_column is None:
        return [{"row": 0, "field": "id", "message": "В файле не найдена колонка 'id'"}], []
    if rows and new_column is None:
        return [{"row": 0, "field": "new_itemnum", "message": "В файле не найдена колонка 'new_itemnum'"}], []

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    batch_numbers: dict[int, str] = {}
    batch_keys: dict[tuple, int] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num
        id_raw = row_text(row, id_column)
        new_number = row_text(row, new_column)
        if not id_raw:
            errors.append({"row": row_num, "field": "id", "message": "Поле 'id' пустое"})
            continue
        model_id = parse_model_id(id_raw)
        if model_id is None:
            errors.append({"row": row_num, "field": "id", "message": f"Некорректный id: '{id_raw}'"})
            continue
        model = (
            await db_session.execute(
                select(
                    Models.id_train_type,
                    cast(Models.lcn, String).label("lcn"),
                    Models.id_car_place,
                    Models.is_default,
                    Models.id_design_number,
                    DesignNumber.number,
                )
                .outerjoin(DesignNumber, DesignNumber.id == Models.id_design_number)
                .where(Models.id == model_id)
            )
        ).first()
        if model is None:
            errors.append({"row": row_num, "field": "id", "message": f"Модель с id={model_id} не найдена"})
            continue
        current = model.number
        old_number = row_text(row, old_column)
        if old_number and old_number != current:
            errors.append(
                {
                    "row": row_num,
                    "field": "itemnum",
                    "message": f"У модели id={model_id} в БД ТМЦ {_shown(current)}, а в файле '{old_number}'",
                }
            )
            continue
        if model_id in batch_numbers:
            if batch_numbers[model_id] != new_number:
                errors.append(
                    {
                        "row": row_num,
                        "field": "id",
                        "message": (
                            f"Конфликт: id={model_id} уже сопоставлен другому ТМЦ "
                            f"('{batch_numbers[model_id]}', а не '{new_number}')"
                        ),
                    }
                )
            continue
        if not new_number:
            errors.append({"row": row_num, "field": "new_itemnum", "message": "Поле 'new_itemnum' пустое"})
            continue
        batch_numbers[model_id] = new_number
        if new_number == current:
            continue
        id_design_number = (
            await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == new_number))
        ).scalar_one_or_none()
        if id_design_number is None:
            errors.append({"row": row_num, "field": "new_itemnum", "message": f"ТМЦ не найден: '{new_number}'"})
            continue
        conflict = await _index_conflict(db_session, model_id, id_design_number)
        if conflict is not None:
            errors.append(
                {
                    "row": row_num,
                    "field": "new_itemnum",
                    "message": f"Конфликт с моделью id={conflict}: такой ТМЦ уже стоит на этом месте",
                }
            )
            continue
        keys = _index_keys(model, id_design_number)
        clash = next((batch_keys[key] for key in keys if key in batch_keys), None)
        if clash is not None:
            errors.append(
                {
                    "row": row_num,
                    "field": "new_itemnum",
                    "message": f"Конфликт со строкой {clash}: такой ТМЦ уже ставится на это место",
                }
            )
            continue
        batch_keys.update((key, row_num) for key in keys)
        valid_rows.append(
            {
                "id": model_id,
                "id_design_number": id_design_number,
                "old_id_design_number": model.id_design_number,
                "old_number": current,
                "new_number": new_number,
            }
        )

    return errors, valid_rows


class ChangeModelDesignNumberController(Controller):
    path = "/parser"

    @post("/change-design-number/generate-sql/start")
    async def change_design_number_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_generate(progress, rows))

    async def _run_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_change_model_design_number_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = models_sql.change_model_design_number(valid_rows)
        progress.update(status="done", sql="\n".join(["BEGIN;", *sql_lines, "COMMIT;"]), count=len(valid_rows))

    @post("/change-design-number/execute-sql/start")
    async def change_design_number_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_execute(progress, rows))

    async def _run_execute(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await validate_change_model_design_number_rows(
                        session, rows, progress=progress
                    )
                except Exception as e:
                    progress.update(
                        status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}]
                    )
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(
                        status="error", errors=[{"row": 0, "field": "*", "message": "Нет строк с изменением ТМЦ"}]
                    )
                    return

                progress.update(processed=0, total=1, phase="executing")
                try:
                    await execute_sql_lines(session, models_sql.change_model_design_number(valid_rows))
                    progress["processed"] = 1
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    progress.update(
                        status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]
                    )
                    return
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
            return

        now = datetime.now()
        log_lines = [
            f"=== Execute change-model-design-number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Models updated: {len(valid_rows)}",
            "",
            *(
                f"model id={vr['id']}: {_shown(vr['old_number'])} (id_design_number {vr['old_id_design_number']}) "
                f"-> '{vr['new_number']}' (id_design_number {vr['id_design_number']})"
                for vr in valid_rows
            ),
            "",
            *models_sql.change_model_design_number(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"change_model_design_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Changed design number of %d models, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows), message=f"Изменён ТМЦ у моделей: {len(valid_rows)}")
