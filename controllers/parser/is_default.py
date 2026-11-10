import logging

from litestar import Controller, post
from litestar.params import MultipartBody
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from models import Models
from sql_builders import models as models_sql

logger = logging.getLogger("parser")


async def validate_is_default_rows(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
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
        select(
            Models.id, Models.id_train_type, Models.lcn, Models.id_car_place, Models.id_design_number, Models.is_default
        )
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
            errors.append(
                {
                    "row": row_num,
                    "field": "isdefault",
                    "message": f"Неверное значение isdefault: '{isdefault_raw}' (ожидается true/false)",
                }
            )
            continue
        is_default = isdefault_raw in ("true", "1", "да")

        if model_id in parsed:
            if parsed[model_id]["is_default"] != is_default:
                errors.append(
                    {
                        "row": row_num,
                        "field": "id",
                        "message": (
                            f"Конфликт: id={model_id} уже сопоставлен другому значению isdefault "
                            f"({parsed[model_id]['is_default']}, а не {is_default})"
                        ),
                    }
                )
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
                errors.append(
                    {
                        "row": row_num,
                        "field": "isdefault",
                        "message": (
                            f"Конфликт unique (lcn, car_place) WHERE is_default=true: "
                            f"lcn='{lcn}', car_place={id_car_place} уже заняты"
                        ),
                    }
                )
                continue
            owner2 = existing_default_car_type_design.get(car_type_design, batch_car_type_design.get(car_type_design))
            if owner2 is not None and owner2 != model_id:
                errors.append(
                    {
                        "row": row_num,
                        "field": "isdefault",
                        "message": (
                            f"Конфликт unique (car_place, train_type, design_number) WHERE is_default=true: "
                            f"car_place={id_car_place}, train_type={id_train_type}, "
                            f"design_number={id_design_number} уже заняты"
                        ),
                    }
                )
                continue
            batch_lcn_car[lcn_car] = model_id
            batch_car_type_design[car_type_design] = model_id

        valid_ids.append(model_id)

    valid_rows = [{"id": mid, "is_default": parsed[mid]["is_default"]} for mid in valid_ids]
    return errors, valid_rows


def _is_default_summary(
    total: int, valid_rows: list[dict], errors: list[dict], rowcounts: list[int]
) -> tuple[str, list[str]]:
    updated = sum(rowcounts)
    return (
        f"Изменена серийность (is_default) у моделей: {updated} (строк файла: {len(valid_rows)})",
        [f"Rows processed: {len(valid_rows)}, models updated: {updated}"],
    )


IS_DEFAULT = sql_tasks.SqlOperation(
    validate=validate_is_default_rows,
    build=models_sql.set_is_default,
    log_name="is_default",
    log_title="is-default update",
    summary=_is_default_summary,
    empty_message="Нет валидных строк для изменения",
    logger=logger,
    per_statement=True,
)


class IsDefaultController(Controller):
    path = "/parser"

    @post("/is-default/generate-sql/start")
    async def is_default_generate_sql_start(self, data: MultipartBody[dict]) -> Response:
        """Start background generation of the is_default change SQL file; returns a task_id to poll."""
        return sql_tasks.start_generate(data, IS_DEFAULT)

    @post("/is-default/execute-sql/start")
    async def is_default_execute_sql_start(self, data: MultipartBody[dict]) -> Response:
        """Start the background is_default change in the database; returns a task_id to poll."""
        return sql_tasks.start_execute(data, IS_DEFAULT)
