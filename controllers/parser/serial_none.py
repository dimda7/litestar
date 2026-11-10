import logging

from litestar import Controller, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from models import Train
from sql_builders import models as models_sql

from .common import parse_model_lcn

logger = logging.getLogger("parser")


async def validate_serial_none_rows(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'set serial=none lcn'.

    In an lcn like 'M9.6.5', 9 is id_train_type and '6.5' is the rest of the
    path. For every train of that id_train_type its own id replaces 'M9',
    yielding the list of asset lcns ('lcn_trains') that need
    serial_number='none'.
    """
    errors: list[dict] = []
    valid_rows: list[dict] = []

    lcn_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("lsn", "lcn")),
        None,
    )
    if rows and lcn_column is None:
        errors.append({"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    train_ids_by_type: dict[int, list[int]] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        lcn_raw = str(row.get(lcn_column, "") or "").strip()
        if not lcn_raw:
            errors.append({"row": row_num, "field": "lcn", "message": "Пустой lcn"})
            continue

        parsed = parse_model_lcn(lcn_raw)
        if parsed is None:
            errors.append(
                {"row": row_num, "field": "lcn", "message": f"Не удалось распознать id_train_type в lcn '{lcn_raw}'"}
            )
            continue
        id_train_type, rest = parsed

        if id_train_type not in train_ids_by_type:
            result = await db_session.execute(select(Train.id).where(Train.id_train_type == id_train_type))
            train_ids_by_type[id_train_type] = [r[0] for r in result.all()]
        train_ids = train_ids_by_type[id_train_type]

        if not train_ids:
            errors.append(
                {"row": row_num, "field": "lcn", "message": f"Поезда с id_train_type={id_train_type} не найдены"}
            )
            continue

        lcn_trains = [f"{tid}.{rest}" if rest else str(tid) for tid in train_ids]

        valid_rows.append(
            {
                "row": row_num,
                "lcn": lcn_raw,
                "id_train_type": id_train_type,
                "lcn_trains": lcn_trains,
            }
        )

    return errors, valid_rows


def _serial_none_summary(
    total: int, valid_rows: list[dict], errors: list[dict], rowcounts: list[int]
) -> tuple[str, list[str]]:
    updated = sum(rowcounts)
    return (
        f"Обновлено активов: {updated} (строк файла: {len(valid_rows)})",
        [f"Rows processed: {len(valid_rows)}, actives updated: {updated}"],
    )


SERIAL_NONE = sql_tasks.SqlOperation(
    validate=validate_serial_none_rows,
    build=models_sql.set_serial_none,
    log_name="serial_none",
    log_title="serial-none update",
    summary=_serial_none_summary,
    empty_message="Нет валидных строк для обновления",
    logger=logger,
    per_statement=True,
)


class SerialNoneController(Controller):
    path = "/parser"

    @post("/serial-none/generate-sql/start")
    async def serial_none_generate_sql_start(
        self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)
    ) -> Response:
        """Start background generation of the serial_number = 'none' SQL file; returns a task_id to poll."""
        return sql_tasks.start_generate(data, SERIAL_NONE)

    @post("/serial-none/execute-sql/start")
    async def serial_none_execute_sql_start(
        self, data: dict = Body(media_type=RequestEncodingType.MULTI_PART)
    ) -> Response:
        """Start the background serial_number = 'none' in the database; returns a task_id to poll."""
        return sql_tasks.start_execute(data, SERIAL_NONE)
