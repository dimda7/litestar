import logging

from litestar import Controller, post
from litestar.params import MultipartBody
from litestar.response import Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from models import CarPlace, Train
from sql_builders import models as models_sql

from .common import parse_model_lcn

logger = logging.getLogger("parser")


async def validate_change_okz_active_rows(
    db_session: AsyncSession,
    rows: list[dict],
    progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'изменить okz в активе по модели'.

    The 'lsn' column is a models.lcn value like 'M9.6.5'; parse_model_lcn
    extracts id_train_type=9 and the rest of the path '6.5'. Every train of
    that id_train_type gets its own id substituted for 'M9', yielding the
    concrete asset lcns ('lcn_trains') whose public.location.id_car_place
    (reached through actives.id_location — actives itself has no id_car_place
    column) is set to the car_place resolved from 'new_position'. A train
    missing that particular asset is expected, not an error — the generated
    UPDATE simply won't match it (same as set_serial_none).
    """
    errors: list[dict] = []
    valid_rows: list[dict] = []

    lcn_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("lsn", "lcn")),
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
    if rows and lcn_column is None:
        errors.append({"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"})
        return errors, valid_rows
    if rows and new_column is None:
        errors.append({"row": 0, "field": "new_position", "message": "В файле не найдена колонка 'new_position'"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    train_ids_by_type: dict[int, list[int]] = {}
    batch_lsn: dict[tuple[int, str], str] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        lcn_raw = str(row.get(lcn_column, "") or "").strip()
        new_raw = str(row.get(new_column, "") or "").strip()
        old_raw = str(row.get(old_column, "") or "").strip() if old_column else ""

        if not lcn_raw:
            errors.append({"row": row_num, "field": "lcn", "message": "Пустой lcn"})
            continue

        if not new_raw:
            errors.append({"row": row_num, "field": "new_position", "message": "Пустой new_position"})
            continue

        if new_raw == old_raw:
            continue

        parsed = parse_model_lcn(lcn_raw)
        if parsed is None:
            errors.append(
                {"row": row_num, "field": "lcn", "message": f"Не удалось распознать id_train_type в lcn '{lcn_raw}'"}
            )
            continue
        id_train_type, rest = parsed
        # Keyed by the parsed (id_train_type, rest) pair, not the raw text — two
        # differently-written lcns that parse to the same target ('M9.6.5' vs a
        # leading-zero 'M09.6.5') must conflict/dedup as the same lsn.
        lsn_key = (id_train_type, rest)

        if lsn_key in batch_lsn and batch_lsn[lsn_key] != new_raw:
            errors.append(
                {
                    "row": row_num,
                    "field": "lcn",
                    "message": (
                        f"Конфликт: lsn='{lcn_raw}' уже сопоставлен другому okz "
                        f"('{batch_lsn[lsn_key]}', а не '{new_raw}')"
                    ),
                }
            )
            continue

        if id_train_type not in train_ids_by_type:
            result = await db_session.execute(select(Train.id).where(Train.id_train_type == id_train_type))
            train_ids_by_type[id_train_type] = [r[0] for r in result.all()]
        train_ids = train_ids_by_type[id_train_type]

        if not train_ids:
            errors.append(
                {"row": row_num, "field": "lcn", "message": f"Поезда с id_train_type={id_train_type} не найдены"}
            )
            continue

        cp_result = await db_session.execute(select(CarPlace.id).where(CarPlace.name == new_raw))
        matches = cp_result.scalars().all()
        if len(matches) == 0:
            errors.append({"row": row_num, "field": "new_position", "message": f"car_place не найден: '{new_raw}'"})
            continue
        if len(matches) > 1:
            errors.append(
                {
                    "row": row_num,
                    "field": "new_position",
                    "message": (
                        f"car_place неоднозначен: найдено {len(matches)} записей с именем '{new_raw}' (id: {matches})"
                    ),
                }
            )
            continue

        # Cast on an ltree column (Postgres-only, SQLite in the unit suite can't
        # parse it) — kept last so a row already doomed by a missing train or an
        # unresolved car_place fails there first, without ever reaching this query.
        result = await db_session.execute(text("SELECT 1 FROM public.models WHERE lcn::text = :lcn"), {"lcn": lcn_raw})
        if result.first() is None:
            errors.append({"row": row_num, "field": "lcn", "message": f"Модель с lcn='{lcn_raw}' не найдена"})
            continue

        if lsn_key not in batch_lsn:
            batch_lsn[lsn_key] = new_raw
            lcn_trains = [f"{tid}.{rest}" if rest else str(tid) for tid in train_ids]
            valid_rows.append({"lcn_trains": lcn_trains, "new_car_place_id": matches[0]})

    return errors, valid_rows


def _change_okz_active_summary(
    total: int, valid_rows: list[dict], errors: list[dict], rowcounts: list[int]
) -> tuple[str, list[str]]:
    updated = sum(rowcounts)
    return (
        f"Изменено okz у активов: {updated} (строк файла: {len(valid_rows)})",
        [f"Rows processed: {len(valid_rows)}, locations updated: {updated}"],
    )


CHANGE_OKZ_ACTIVE = sql_tasks.SqlOperation(
    validate=validate_change_okz_active_rows,
    build=models_sql.change_okz_active,
    log_name="change_okz_active",
    log_title="change-okz-active update",
    summary=_change_okz_active_summary,
    empty_message="Нет валидных строк для изменения",
    logger=logger,
    per_statement=True,
)


class ChangeOkzActiveController(Controller):
    path = "/parser"

    @post("/change-okz-active/generate-sql/start")
    async def change_okz_active_generate_sql_start(self, data: MultipartBody[dict]) -> Response:
        """Start background generation of the 'изменить okz в активе по модели' SQL file; returns a task_id to poll."""
        return sql_tasks.start_generate(data, CHANGE_OKZ_ACTIVE)

    @post("/change-okz-active/execute-sql/start")
    async def change_okz_active_execute_sql_start(self, data: MultipartBody[dict]) -> Response:
        """Start the background 'изменить okz в активе по модели' in the database; returns a task_id to poll."""
        return sql_tasks.start_execute(data, CHANGE_OKZ_ACTIVE)
