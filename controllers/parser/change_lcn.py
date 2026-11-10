import logging

from litestar import Controller, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from sql_builders import models as models_sql

logger = logging.getLogger("parser")


async def validate_change_model_lcn_rows(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'изменить lcn в модели' (the public.models table).

    The 'id' column is models.id (the same scheme as the "Удалить из
    моделей"/"Добавить в модели" sheets in this very file); 'lsn'/'lcn' is
    the new models.lcn value. The optional 'Старый lsn'/'Старый lcn' is
    checked against the current models.lcn — a guard against a stale or wrong
    file; it takes no part in the update (matching always goes through the
    stable id, never the lcn text).
    """
    errors: list[dict] = []
    valid_rows: list[dict] = []

    id_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() == "id"),
        None,
    )
    new_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("lsn", "lcn")),
        None,
    )
    old_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("старый lsn", "старый lcn")),
        None,
    )
    if rows and id_column is None:
        errors.append({"row": 0, "field": "id", "message": "В файле не найдена колонка 'id'"})
        return errors, valid_rows
    if rows and new_column is None:
        errors.append({"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"})
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
            errors.append({"row": row_num, "field": "lcn", "message": "Пустой lcn"})
            continue

        if model_id in batch_ids and batch_ids[model_id] != new_raw:
            errors.append(
                {
                    "row": row_num,
                    "field": "id",
                    "message": (
                        f"Конфликт: id={model_id} уже сопоставлен другому lcn "
                        f"('{batch_ids[model_id]}', а не '{new_raw}')"
                    ),
                }
            )
            continue

        result = await db_session.execute(text("SELECT lcn::text FROM public.models WHERE id = :id"), {"id": model_id})
        current_lcn = result.scalar_one_or_none()
        if current_lcn is None:
            errors.append({"row": row_num, "field": "id", "message": f"Модель с id={model_id} не найдена"})
            continue

        if old_raw and current_lcn != old_raw:
            errors.append(
                {
                    "row": row_num,
                    "field": "lcn",
                    "message": (
                        f"Текущий lcn модели id={model_id} ('{current_lcn}') не совпадает со 'Старый lsn' ('{old_raw}')"
                    ),
                }
            )
            continue

        if model_id not in batch_ids:
            batch_ids[model_id] = new_raw
            valid_rows.append({"id": model_id, "new_lcn": new_raw})

    return errors, valid_rows


def _change_lcn_summary(
    total: int, valid_rows: list[dict], errors: list[dict], rowcounts: list[int]
) -> tuple[str, list[str]]:
    # Two-phase UPDATE (see models_sql.change_model_lcn): the second step is the one that lands.
    updated = rowcounts[1]
    return (
        f"Изменено lcn у моделей: {updated} (строк файла: {len(valid_rows)})",
        [f"Rows processed: {len(valid_rows)}, models updated: {updated}"],
    )


CHANGE_LCN = sql_tasks.SqlOperation(
    validate=validate_change_model_lcn_rows,
    build=models_sql.change_model_lcn,
    log_name="change_lcn",
    log_title="change-lcn update",
    summary=_change_lcn_summary,
    empty_message="Нет валидных строк для изменения",
    logger=logger,
    per_statement=True,
)


class ChangeModelLcnController(Controller):
    path = "/parser"

    @post("/change-lcn/generate-sql/start")
    async def change_lcn_generate_sql_start(
        self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)
    ) -> Response:
        """Start background generation of the 'изменить lcn в модели' SQL file; returns a task_id to poll."""
        return sql_tasks.start_generate(data, CHANGE_LCN)

    @post("/change-lcn/execute-sql/start")
    async def change_lcn_execute_sql_start(
        self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)
    ) -> Response:
        """Start the background 'изменить lcn в модели' in the database; returns a task_id to poll."""
        return sql_tasks.start_execute(data, CHANGE_LCN)
