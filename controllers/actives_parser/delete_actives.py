import logging
from datetime import datetime

from sqlalchemy import bindparam, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.response import Response

from db_manager import get_session_maker
from models import Actives, ActiveAdditionalField, ActivesToMainPtoir, MaterialsToActives, MileageHistoryActives, Orders, OrderToActives, Ptoir, Relocate
import excel_upload
from parser_storage import LOG_DIR
from progress_tasks import error_response, start_task
from sql_builders import actives as actives_sql

from .common import PREFIX, ActivesRepository


logger = logging.getLogger("actives_parser")


# Tables referencing actives by FK: a single related record blocks deleting the
# asset (a strict DELETE — only assets without history are removed).
# counter_active and mileage_start are deliberately absent: the counter_active row
# is created by the actives_trgger trigger on every asset INSERT (every asset has a
# counter, so blocking on it would forbid deletion outright), which is why both
# tables are deleted along with the asset. ptoir goes with the asset too (together
# with its ptoir_level_warning), as do the asset's "empty" orders — but an order
# with related records in any of ORDERS_DEPENDENCY_CHECKS blocks the deletion
# (checked separately).
DELETE_ACTIVES_BLOCKERS: list[tuple[str, object]] = [
    ("relocate", Relocate.id_active),
    ("relocate (root)", Relocate.id_root_active),
    ("order_to_actives", OrderToActives.id_active),
    ("active_additional_field", ActiveAdditionalField.id_active),
    ("actives_to_main_ptoir", ActivesToMainPtoir.id_actives),
    ("materials_to_actives", MaterialsToActives.id_actives),
    ("mileage_history_actives", MileageHistoryActives.id_actives),
]


# Tables referencing orders by FK (taken from information_schema of the real DB):
# an asset's order is deleted along with it only when all of these are empty —
# otherwise the order carries real working data and the asset is blocked.
ORDERS_DEPENDENCY_CHECKS: list[tuple[str, str]] = [
    ("consumption_rate_order", "id_order"),
    ("counter_order", "id_order"),
    ("labor_costs", "id_order"),
    ("material_1c", "id_order"),
    ("order_diagnostic", "id"),
    ("order_status_bin", "id"),
    ("order_to_actives", "id_order"),
    ("order_to_attachment", "id_order"),
    ("order_to_order", "id_parent"),
    ("order_to_order", "id_child"),
    ("order_to_order_executor", "id_order"),
    ("order_used_tools", "id_order"),
    ("orders_ref_data", "id_order"),
    ("orders_required_tools", "id_order"),
    ("orders_to_classifier", "id_order"),
    ("orders_to_labor_costs", "id_order"),
    ("orders_to_orders_work_operation", "id_orders"),
    ("orders_to_specification", "id_order"),
    ("orders_work_operation", "id_order"),
    ("relocate", "id_order"),
]


async def validate_delete_actives(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the rows of an "удалить активы" file (a strict DELETE).

    Only assets without history are removed: if a single record from
    DELETE_ACTIVES_BLOCKERS (maintenance, moves, orders and so on) references the
    asset, the row is an error and the asset stays. Blockers are checked in batches:
    one query per table for all the file's assets, not one query per row.
    """
    actives_repo = ActivesRepository(session=db_session)

    errors: list[dict] = []
    batch_numbers: set[str] = set()
    # id_active -> the row's data; the valid ones remain after the batched blocker check
    candidates: dict[int, dict] = {}

    active_column: str | None = next(
        (k for k in (rows[0] if rows else {})
         if str(k).strip().lower() in ("актив", "active_number")),
        None,
    )
    if rows and active_column is None:
        errors.append({"row": 0, "field": "Актив",
                        "message": "В файле не найдена колонка 'Актив' (или 'active_number')"})
        return errors, []

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        active_number = str(row.get(active_column, "") or "").strip()
        if not active_number:
            errors.append({"row": row_num, "field": "Актив", "message": "Поле 'Актив' пустое"})
            continue

        if active_number in batch_numbers:
            errors.append({"row": row_num, "field": "Актив",
                            "message": f"Дубликат внутри файла: '{active_number}'"})
            continue

        active = await actives_repo.get_one_or_none(Actives.active_number == active_number)
        if active is None:
            errors.append({"row": row_num, "field": "Актив",
                            "message": f"Актив не найден: '{active_number}'"})
            continue

        batch_numbers.add(active_number)
        candidates[active.id] = {
            "row_num": row_num,
            "active_number": active_number,
            "id_active": active.id,
            "id_location": active.id_location,
        }

    if candidates:
        ids = list(candidates)
        for table_name, column in DELETE_ACTIVES_BLOCKERS:
            blocked_ids = (await db_session.execute(
                select(column).where(column.in_(ids)).distinct()
            )).scalars().all()
            for blocked_id in blocked_ids:
                vr = candidates.pop(blocked_id, None)
                if vr is not None:
                    errors.append({
                        "row": vr["row_num"], "field": "Актив",
                        "message": (f"У актива '{vr['active_number']}' есть связанные записи "
                                    f"в {table_name} — удаление запрещено"),
                    })

    if candidates:
        # An asset's orders (directly by id_active or through its maintenance record)
        # are deleted with it, but only the "empty" ones: an order with related records
        # in any of ORDERS_DEPENDENCY_CHECKS is real working history, and blocks the asset.
        ids = list(candidates)
        order_rows = (await db_session.execute(
            select(Orders.id, Orders.id_active, Ptoir.id_active)
            .outerjoin(Ptoir, Orders.id_ptoir == Ptoir.id)
            .where(or_(Orders.id_active.in_(ids), Ptoir.id_active.in_(ids)))
        )).all()
        order_owners: dict[int, set[int]] = {}
        for order_id, direct_active, ptoir_active in order_rows:
            owners = order_owners.setdefault(order_id, set())
            for owner in (direct_active, ptoir_active):
                if owner in candidates:
                    owners.add(owner)

        if order_owners:
            order_ids = list(order_owners)
            for table_name, column in ORDERS_DEPENDENCY_CHECKS:
                stmt = text(
                    f"SELECT DISTINCT {column} FROM public.{table_name} WHERE {column} IN :ids"
                ).bindparams(bindparam("ids", expanding=True))
                blocked_orders = (await db_session.execute(stmt, {"ids": order_ids})).scalars().all()
                for order_id in blocked_orders:
                    for owner in order_owners.get(order_id, ()):
                        vr = candidates.pop(owner, None)
                        if vr is not None:
                            errors.append({
                                "row": vr["row_num"], "field": "Актив",
                                "message": (f"У заказов актива '{vr['active_number']}' есть "
                                            f"связанные записи в {table_name} — удаление запрещено"),
                            })

    valid_rows = sorted(candidates.values(), key=lambda vr: vr["row_num"])
    return errors, valid_rows


class DeleteActivesController(Controller):
    path = "/actives-parser"

    @post("/delete-actives/generate-sql/start")
    async def delete_actives_generate_sql_start(self, request: Request) -> Response:
        """Start background generation of the asset deletion SQL file; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_delete_actives_generate(progress, rows))

    async def _run_delete_actives_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_delete_actives(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для удаления"}])
            return

        sql_lines = actives_sql.delete_actives(valid_rows)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/delete-actives/execute/start")
    async def delete_actives_execute_start(self, request: Request) -> Response:
        """Start the background atomic asset deletion; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_delete_actives_execute(progress, rows))

    async def _run_delete_actives_execute(self, progress: dict, rows: list[dict]) -> None:
        valid_rows: list[dict] = []
        sql_lines: list[str] = []
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = await validate_delete_actives(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для удаления"}])
                    return

                progress.update(processed=0, total=1, phase="executing")

                sql_lines = actives_sql.delete_actives(valid_rows)
                sql_body = "\n".join(sql_lines)

                try:
                    # IMPORTANT: as in create-actives, session.rollback() below also rolls this
                    # raw call back only because the session opened a real transaction earlier
                    # (the repository queries inside validate_delete_actives). Do not remove
                    # the session usage before this point.
                    conn = await session.connection()
                    raw_conn = await conn.get_raw_connection()
                    await raw_conn.driver_connection.execute(sql_body)

                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
                    return

                progress["processed"] = 1
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
            return

        now = datetime.now()
        log_lines = [
            f"=== Delete Actives: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Actives deleted: {len(valid_rows)}",
            "",
            *sql_lines,
            "",
        ]
        log_file = LOG_DIR / f"delete_actives_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Deleted %d actives, log: %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Успешно удалено активов: {len(valid_rows)}")
