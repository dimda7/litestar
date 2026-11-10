import json
import logging
from datetime import datetime
from pathlib import Path

from litestar import Controller, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_manager import get_session_maker
from models import DesignNumber, Models, Train
from parser_storage import LOG_DIR
from progress_tasks import start_task
from sql_builders import models as models_sql

from .common import column_key, execute_sql_lines, parse_model_id, parse_model_lcn, row_text

logger = logging.getLogger("parser")


async def validate_change_design_number_active_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    valid_rows: list[dict] = []
    id_column = column_key(rows, "id")
    old_column = column_key(rows, "itemnum")
    new_column = column_key(rows, "new_itemnum")

    async def design_number_id(number: str) -> int | None:
        return (await db_session.execute(
            select(DesignNumber.id).where(DesignNumber.number == number))).scalar_one_or_none()

    for name, found in (("id", id_column), ("itemnum", old_column), ("new_itemnum", new_column)):
        if rows and found is None:
            return [{"row": 0, "field": name, "message": f"В файле не найдена колонка '{name}'"}], []

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    batch_numbers: dict[tuple[int, str], str] = {}
    batch_positions: dict[tuple[int, str, int], int] = {}
    batch_targets: dict[tuple[int, str, int], int] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num
        id_raw = row_text(row, id_column)
        if not id_raw:
            errors.append({"row": row_num, "field": "id", "message": "Поле 'id' пустое"})
            continue
        model_id = parse_model_id(id_raw)
        if model_id is None:
            errors.append({"row": row_num, "field": "id", "message": f"Некорректный id: '{id_raw}'"})
            continue
        old_number, new_number = row_text(row, old_column), row_text(row, new_column)
        lcn = (await db_session.execute(
            select(cast(Models.lcn, String)).where(Models.id == model_id))).scalar_one_or_none()
        if lcn is None:
            errors.append({"row": row_num, "field": "id", "message": f"Модель с id={model_id} не найдена"})
            continue
        # One model may legitimately carry several old numbers on its assets,
        # so a repeat is keyed by (model, old number), not by the model alone.
        if not old_number or not new_number:
            field = "itemnum" if not old_number else "new_itemnum"
            errors.append({"row": row_num, "field": field, "message": f"Поле '{field}' пустое"})
            continue
        repeat = (model_id, old_number)
        if repeat in batch_numbers:
            if batch_numbers[repeat] != new_number:
                errors.append({"row": row_num, "field": "id", "message": (
                    f"Конфликт: id={model_id} уже сопоставлен другому ТМЦ "
                    f"('{batch_numbers[repeat]}', а не '{new_number}')")})
            continue
        if new_number == old_number:
            batch_numbers[repeat] = new_number
            continue
        old_id = await design_number_id(old_number)
        if old_id is None:
            errors.append({"row": row_num, "field": "itemnum", "message": f"ТМЦ не найден: '{old_number}'"})
            continue
        new_id = await design_number_id(new_number)
        if new_id is None:
            errors.append({"row": row_num, "field": "new_itemnum", "message": f"ТМЦ не найден: '{new_number}'"})
            continue
        parsed = parse_model_lcn(lcn)
        if parsed is None:
            errors.append({"row": row_num, "field": "id",
                           "message": f"Не удалось распознать id_train_type в lcn модели id={model_id}: '{lcn}'"})
            continue
        id_train_type, rest = parsed
        position = (id_train_type, rest, old_id)
        if position in batch_positions:
            errors.append({"row": row_num, "field": "id", "message": (
                f"Конфликт со строкой {batch_positions[position]}: те же активы "
                f"(позиция {rest}, ТМЦ '{old_number}') уже меняются")})
            continue
        # X->Y and Y->Z on one position run correctly once, but re-running the
        # same file would carry the X assets on to Z.
        target = (id_train_type, rest, new_id)
        chained: tuple[int, str] | None = None
        if target in batch_positions:
            chained = (batch_positions[target], new_number)
        elif position in batch_targets:
            chained = (batch_targets[position], old_number)
        if chained is not None:
            errors.append({"row": row_num, "field": "id", "message": (
                f"Цепочка со строкой {chained[0]} на позиции {rest}: ТМЦ '{chained[1]}' там и ставится, "
                "и заменяется — разнесите по двум запускам")})
            continue
        train_ids = (await db_session.execute(
            select(Train.id).where(Train.id_train_type == id_train_type).order_by(Train.id))).scalars().all()
        if not train_ids:
            errors.append({"row": row_num, "field": "id",
                           "message": f"Поезда с id_train_type={id_train_type} не найдены"})
            continue
        batch_numbers[repeat] = new_number
        batch_positions[position] = row_num
        batch_targets[target] = row_num
        valid_rows.append({
            "lcn_trains": [f"{tid}.{rest}" if rest else str(tid) for tid in train_ids],
            "old_id_design_number": old_id, "id_design_number": new_id,
            "old_number": old_number, "new_number": new_number,
        })

    return errors, valid_rows


class ChangeDesignNumberActiveController(Controller):
    path = "/parser"

    @post("/change-design-number-active/generate-sql/start")
    async def change_design_number_active_generate_sql_start(
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
                errors, valid_rows = await validate_change_design_number_active_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = models_sql.change_design_number_active(valid_rows)
        progress.update(status="done", sql="\n".join(["BEGIN;", *sql_lines, "COMMIT;"]), count=len(valid_rows))

    @post("/change-design-number-active/execute-sql/start")
    async def change_design_number_active_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_execute(progress, rows))

    async def _run_execute(self, progress: dict, rows: list[dict]) -> None:
        total_updated = 0
        log_file: Path | None = None
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await validate_change_design_number_active_rows(
                        session, rows, progress=progress)
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет строк с изменением ТМЦ"}])
                    return

                progress.update(processed=0, total=1, phase="executing")
                sql_lines = models_sql.change_design_number_active(valid_rows)
                try:
                    conn = await session.connection()
                    targets = (await conn.exec_driver_sql(models_sql.design_number_active_targets(valid_rows))).all()
                    numbers = {vr["old_id_design_number"]: vr["old_number"] for vr in valid_rows}
                    numbers.update((vr["id_design_number"], vr["new_number"]) for vr in valid_rows)
                    # Written before the UPDATE: it is the only record of which
                    # assets held the old design number.
                    now = datetime.now()
                    log_lines = [
                        f"=== Execute change-design-number-active: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
                        f"Rows: {len(valid_rows)}, assets to update: {len(targets)}",
                        "",
                        *(f"active id={id_active} lcn={lcn}: '{numbers[old]}' (id_design_number {old}) "
                          f"-> '{numbers[new]}' (id_design_number {new})"
                          for id_active, lcn, old, new in targets),
                        "",
                        *sql_lines,
                        "",
                    ]
                    log_file = LOG_DIR / f"change_design_number_active_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write("\n".join(log_lines))
                    _, total_updated = await execute_sql_lines(session, sql_lines)
                    progress["processed"] = 1
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    if log_file is not None:
                        with open(log_file, "a", encoding="utf-8") as f:
                            f.write(f"-- ROLLED BACK, nothing was changed: {e}\n")
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
                    return
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
            return

        logger.info("Changed design number of %d actives, log: %s", total_updated, log_file)
        progress.update(status="done", count=total_updated, message=(
            f"Изменён ТМЦ у активов: {total_updated} (строк файла: {len(valid_rows)})\n"
            "counter_active не пересчитан: при смене группы счётчиков запустите "
            "«Синхронизация counter_active» на странице «Парсинг ТМЦ»"))
