import json
import logging
from datetime import datetime

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response

from db_manager import get_session_maker
from models import Train
from parser_storage import LOG_DIR
from progress_tasks import start_task
from sql_builders import models as models_sql

from .common import parse_model_lcn


logger = logging.getLogger("parser")


async def validate_move_no_relocate_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the Excel rows for 'Переместить активы без relocate'.

    Each row gives the old and the new model lcn ('Старый lsn' -> 'lsn',
    both like 'M9.1.6') for one and the same position. The id_train_type
    (which must match on both) finds every train of that type, and for each
    a pair of real asset lcns is built (old -> new). Duplicate or
    overlapping file rows collapse into one pair; the same old lcn with
    different new ones is a conflict (an error).
    """
    errors: list[dict] = []
    pairs: dict[str, str] = {}
    pair_list: list[dict] = []

    new_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("lsn", "lcn")),
        None,
    )
    old_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("старый lsn", "старый lcn")),
        None,
    )
    if rows and new_column is None:
        errors.append({"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"})
        return errors, pair_list
    if rows and old_column is None:
        errors.append({"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'Старый lsn' (или 'Старый lcn')"})
        return errors, pair_list

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    train_ids_by_type: dict[int, list[int]] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        new_raw = str(row.get(new_column, "") or "").strip()
        old_raw = str(row.get(old_column, "") or "").strip()
        if not new_raw:
            errors.append({"row": row_num, "field": "lcn", "message": "Пустой lcn"})
            continue
        if not old_raw:
            errors.append({"row": row_num, "field": "lcn", "message": "Пустой Старый lcn"})
            continue

        new_parsed = parse_model_lcn(new_raw)
        if new_parsed is None:
            errors.append({"row": row_num, "field": "lcn",
                           "message": f"Не удалось распознать id_train_type в lcn '{new_raw}'"})
            continue
        old_parsed = parse_model_lcn(old_raw)
        if old_parsed is None:
            errors.append({"row": row_num, "field": "lcn",
                           "message": f"Не удалось распознать id_train_type в Старый lcn '{old_raw}'"})
            continue

        id_train_type_new, new_path = new_parsed
        id_train_type_old, old_path = old_parsed
        if id_train_type_new != id_train_type_old:
            errors.append({"row": row_num, "field": "lcn",
                           "message": (f"id_train_type не совпадает у 'Старый lsn' и 'lsn': "
                                       f"'{old_raw}' ({id_train_type_old}) vs '{new_raw}' ({id_train_type_new})")})
            continue

        if id_train_type_new not in train_ids_by_type:
            result = await db_session.execute(select(Train.id).where(Train.id_train_type == id_train_type_new))
            train_ids_by_type[id_train_type_new] = [r[0] for r in result.all()]
        train_ids = train_ids_by_type[id_train_type_new]

        if not train_ids:
            errors.append({"row": row_num, "field": "lcn",
                           "message": f"Поезда с id_train_type={id_train_type_new} не найдены"})
            continue

        row_pairs: list[tuple[str, str]] = []
        conflict = False
        for tid in train_ids:
            old_lcn = f"{tid}.{old_path}" if old_path else str(tid)
            new_lcn = f"{tid}.{new_path}" if new_path else str(tid)
            if old_lcn in pairs and pairs[old_lcn] != new_lcn:
                errors.append({"row": row_num, "field": "lcn",
                               "message": (f"Конфликт: '{old_lcn}' уже сопоставлен другому lcn "
                                           f"('{pairs[old_lcn]}', а не '{new_lcn}')")})
                conflict = True
                break
            row_pairs.append((old_lcn, new_lcn))
        if conflict:
            continue

        for old_lcn, new_lcn in row_pairs:
            if old_lcn not in pairs:
                pairs[old_lcn] = new_lcn
                pair_list.append({"old_lcn": old_lcn, "new_lcn": new_lcn})

    return errors, pair_list


async def check_lcn_collisions(db_session: AsyncSession, pair_list: list[dict]) -> list[dict]:
    """Find assets already holding a pair's target new_lcn that are not in the file.

    The two-phase UPDATE (see models_sql.two_phase_lcn_update) is safe for
    chains WITHIN one batch (A->B, B->C), but does not help when the target
    new_lcn is already taken by an asset the file never mentions — then a
    UniqueViolationError surfaces mid-execution with no clear cause. It is a
    separate method (not part of validate_move_no_relocate_rows)
    because it uses lcn::text, a Postgres-specific cast the SQLite test
    database does not support (see test_parser_change_lcn_validation.py).
    """
    if not pair_list:
        return []
    old_lcns = {vr["old_lcn"] for vr in pair_list}
    new_lcns = [vr["new_lcn"] for vr in pair_list]
    stmt = text(
        "SELECT active_number, lcn::text FROM public.actives WHERE lcn::text IN :lcns"
    ).bindparams(bindparam("lcns", expanding=True))
    result = await db_session.execute(stmt, {"lcns": new_lcns})
    errors: list[dict] = []
    for active_number, current_lcn in result.all():
        if current_lcn not in old_lcns:
            errors.append({
                "row": 0, "field": "lcn",
                "message": (f"Конфликт: целевой lcn '{current_lcn}' уже занят активом "
                            f"'{active_number}', которого нет в файле"),
            })
    return errors


class MoveNoRelocateController(Controller):
    path = "/parser"

    @post("/move-no-relocate/generate-sql/start")
    async def move_no_relocate_generate_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start background generation of the 'Переместить активы без relocate' SQL file; returns a task_id."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_move_no_relocate_generate(progress, rows))

    async def _run_move_no_relocate_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                # The same lcn pair validation and parsing as 'Изменить lcn в модели'
                # (Старый lsn -> lsn, both columns resolved as in 'set serial=none lcn').
                errors, valid_rows = await validate_move_no_relocate_rows(session, rows, progress=progress)
                if not errors:
                    errors.extend(await check_lcn_collisions(session, valid_rows))
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = models_sql.move_no_relocate(valid_rows)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/move-no-relocate/execute-sql/start")
    async def move_no_relocate_execute_sql_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start the background atomic asset move without relocate; returns a task_id."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_move_no_relocate_execute(progress, rows))

    async def _run_move_no_relocate_execute(self, progress: dict, rows: list[dict]) -> None:
        total_updated = 0
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    errors, valid_rows = await validate_move_no_relocate_rows(session, rows, progress=progress)
                    if not errors:
                        errors.extend(await check_lcn_collisions(session, valid_rows))
                except Exception as e:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка валидации: {e}"}])
                    return

                if errors:
                    progress.update(status="error", errors=errors)
                    return

                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для перемещения"}])
                    return

                progress.update(processed=0, total=1, phase="executing")
                try:
                    # Two-phase UPDATE as in change-lcn, but additionally resetting
                    # id_actves_parent/id_actives_root; id_location and relocate are untouched.
                    sql_lines = models_sql.move_no_relocate(valid_rows)
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
            f"=== Execute move-no-relocate update: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Pairs processed: {len(valid_rows)}, actives updated: {total_updated}",
            "",
            *models_sql.move_no_relocate(valid_rows),
            "",
        ]
        log_file = LOG_DIR / f"move_no_relocate_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Moved (no relocate) %d pairs (%d actives), log: %s", len(valid_rows), total_updated, log_file)

        progress.update(status="done", count=len(valid_rows),
                         message=f"Перемещено активов: {total_updated} (пар: {len(valid_rows)})")
