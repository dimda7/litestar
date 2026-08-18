import logging
from datetime import datetime

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.response import Response

from db_manager import get_session_maker
from models import DesignNumber, IteratorNumberLast, Train
import excel_upload
from parser_storage import LOG_DIR
from progress_tasks import error_response, start_task
from sql_builders import actives as actives_sql
from sql_builders.actives import ACTIVE_NUMBER_COUNTER_DESCRIPTION, ACTIVE_NUMBER_LENGTH

from .common import PREFIX, IteratorNumberLastRepository, parse_model_lcn


logger = logging.getLogger("actives_parser")


async def validate_create_active_from_model_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict], int]:
    """Validate the rows for 'Создать актив из модели lcn'.

    In an lsn like 'M9.1.6.4': 9 is id_train_type, the first path segment ('1') is
    car_number, and the whole path after id_train_type ('1.6.4') is the remainder used
    to build the real asset lcn on a specific train. id_car_place is taken from
    public.models by that same lcn (WHERE id_train_type=... AND lcn::text=lsn) — the
    same lookup principle train_parser.py uses when creating a new train.

    One lcn often appears in several models rows with different id_design_number
    (alternative material positions for the same place in the model) — in practice
    their id_car_place usually matches, and is_default may be false on ALL of the rows
    at once (verified against the real database: 'M9.2.5.4' has 2 rows, both
    is_default=false but with the same id_car_place). So all distinct id_car_place
    values are collected first, without filtering on is_default; if there is only one,
    it is used. Only when car_place genuinely differs between rows does is_default=true
    come in as a tie-break; if that still leaves it ambiguous, the row is an error.

    The number of assets created per position does NOT come from a column in the file
    but from the number of trains of that id_train_type (public.train): each train gets
    its own asset with its own location (id_type_location=2, id_train, car_number,
    id_car_place). Rows with identical or overlapping lsn share one cache of trains by
    type (as in validate_serial_none_rows, controllers/parser/serial_none.py).

    Assets whose computed lcn is already taken by an existing one (the position is
    already filled on some of the fleet) are skipped silently — not a validation error
    but the ordinary case of partial fleet coverage. Returns
    (errors, valid_rows, skipped_count).
    """
    errors: list[dict] = []
    candidates: list[dict] = []

    tmc_column: str | None = next(
        (k for k in (rows[0] if rows else {})
         if str(k).strip().lower() in ("артикул", "номер тмц (du,kp,a2v)")),
        None,
    )
    if rows and tmc_column is None:
        errors.append({"row": 0, "field": "АРТИКУЛ",
                        "message": "В файле не найдена колонка 'АРТИКУЛ' (или 'Номер ТМЦ (DU,KP,A2V)')"})
        return errors, [], 0

    lsn_column: str | None = next(
        (k for k in (rows[0] if rows else {}) if str(k).strip().lower() in ("lsn", "lcn")),
        None,
    )
    if rows and lsn_column is None:
        errors.append({"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"})
        return errors, [], 0

    counter_repo = IteratorNumberLastRepository(session=db_session)
    counter_row = await counter_repo.get_one_or_none(
        IteratorNumberLast.description == ACTIVE_NUMBER_COUNTER_DESCRIPTION
    )
    if counter_row is None or counter_row.number is None:
        errors.append({
            "row": 0, "field": "*",
            "message": f"Не найден счётчик '{ACTIVE_NUMBER_COUNTER_DESCRIPTION}' в iterator_number_last",
        })
        return errors, [], 0

    design_number_cache: dict[str, int | None] = {}
    train_ids_by_type: dict[int, list[int]] = {}
    model_cache: dict[tuple[int, str], list[int | None]] = {}

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        design_number_raw = str(row.get(tmc_column) or "").strip()
        if not design_number_raw or design_number_raw == "None":
            continue

        type_active = str(row.get("Тип актива") or "").strip()
        if not type_active:
            errors.append({"row": row_num, "field": "Тип актива", "message": "Поле 'Тип актива' пустое"})
            continue
        if len(type_active) >= ACTIVE_NUMBER_LENGTH:
            errors.append({
                "row": row_num, "field": "Тип актива",
                "message": (f"'{type_active}' слишком длинный для {ACTIVE_NUMBER_LENGTH}-значного "
                            f"номера актива (префикс + цифры)"),
            })
            continue

        lsn_raw = str(row.get(lsn_column) or "").strip()
        if not lsn_raw:
            errors.append({"row": row_num, "field": "lcn", "message": "Пустой lsn"})
            continue

        parsed = parse_model_lcn(lsn_raw)
        if parsed is None:
            errors.append({"row": row_num, "field": "lcn",
                            "message": f"Не удалось распознать id_train_type в lcn '{lsn_raw}'"})
            continue
        id_train_type, rest = parsed

        if design_number_raw not in design_number_cache:
            design_number_cache[design_number_raw] = await db_session.scalar(
                select(DesignNumber.id).where(DesignNumber.number == design_number_raw)
            )
        id_design_number = design_number_cache[design_number_raw]
        if id_design_number is None:
            errors.append({"row": row_num, "field": tmc_column, "message": f"ТМЦ не найдена: '{design_number_raw}'"})
            continue

        model_key = (id_train_type, lsn_raw)
        if model_key not in model_cache:
            model_result = await db_session.execute(
                text(
                    "SELECT id_car_place, is_default FROM public.models "
                    "WHERE id_train_type = :tt AND lcn::text = :lcn"
                ),
                {"tt": id_train_type, "lcn": lsn_raw},
            )
            model_cache[model_key] = model_result.all()
        model_rows = model_cache[model_key]
        if not model_rows:
            errors.append({"row": row_num, "field": "lcn", "message": f"Модель с lcn '{lsn_raw}' не найдена"})
            continue
        distinct_places = {r[0] for r in model_rows}
        if len(distinct_places) == 1:
            id_car_place = next(iter(distinct_places))
        else:
            # One lcn may map to several models rows with different id_design_number
            # (alternative positions) — when those also differ on id_car_place, the row
            # with is_default=true wins. is_default=true is not guaranteed for every lcn
            # (see the case above where both rows are non-default but agree on
            # car_place — that path never reaches here).
            default_places = {r[0] for r in model_rows if r[1]}
            if len(default_places) == 1:
                id_car_place = next(iter(default_places))
            else:
                errors.append({"row": row_num, "field": "lcn",
                                "message": (f"Модель с lcn '{lsn_raw}' неоднозначна: несколько разных "
                                            f"id_car_place {sorted(p for p in distinct_places if p is not None)}")})
                continue

        car_number: int | None = None
        if rest:
            first_segment = rest.split(".")[0]
            try:
                car_number = int(first_segment)
            except ValueError:
                errors.append({"row": row_num, "field": "lcn",
                                "message": f"Не удалось распознать номер вагона в lcn '{lsn_raw}'"})
                continue

        if id_train_type not in train_ids_by_type:
            result = await db_session.execute(select(Train.id).where(Train.id_train_type == id_train_type))
            train_ids_by_type[id_train_type] = [r[0] for r in result.all()]
        train_ids = train_ids_by_type[id_train_type]
        if not train_ids:
            errors.append({"row": row_num, "field": "lcn",
                            "message": f"Поезда с id_train_type={id_train_type} не найдены"})
            continue

        # The serial number from the file is applied only when exactly one asset is
        # created for the position (a single train of that type) — otherwise the same
        # serial would be duplicated across assets (as in validate_create_actives_rows).
        serial_raw = str(row.get("Серийный номер") or "").strip()
        serial_number = serial_raw if len(train_ids) == 1 else "none"

        for train_id in train_ids:
            new_lcn = f"{train_id}.{rest}" if rest else str(train_id)
            candidates.append({
                "row_num": row_num,
                "id_design_number": id_design_number,
                "type_active": type_active,
                "id_train": train_id,
                "car_number": car_number,
                "id_car_place": id_car_place,
                "lcn": new_lcn,
                "serial_number": serial_number or None,
            })

    if not candidates:
        return errors, [], 0

    # A batched check of target lcn occupancy (one query for the whole file rather than
    # per row) plus deduplication within the batch — both are skipped silently, not errors.
    all_lcns = [c["lcn"] for c in candidates]
    stmt = text(
        "SELECT lcn::text FROM public.actives WHERE lcn::text IN :lcns"
    ).bindparams(bindparam("lcns", expanding=True))
    existing_lcns = set((await db_session.execute(stmt, {"lcns": all_lcns})).scalars().all())

    valid_rows: list[dict] = []
    seen_lcns: set[str] = set()
    skipped_count = 0
    for c in candidates:
        if c["lcn"] in existing_lcns or c["lcn"] in seen_lcns:
            skipped_count += 1
            continue
        seen_lcns.add(c["lcn"])
        valid_rows.append(c)

    return errors, valid_rows, skipped_count


class CreateActiveFromModelController(Controller):
    path = "/actives-parser"

    @post("/create-active-from-model/generate-sql/start")
    async def create_active_from_model_generate_sql_start(self, request: Request) -> Response:
        """Start background generation of the SQL file creating assets from a model lcn; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_create_active_from_model_generate(progress, rows))

    async def _run_create_active_from_model_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows, skipped_count = \
                    await validate_create_active_from_model_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
            return

        sql_lines = actives_sql.create_active_from_model(valid_rows)
        header = [f"-- Пропущено (lcn уже занят на части поездов): {skipped_count}"] if skipped_count else []
        full_sql = "\n".join([*header, "BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/create-active-from-model/execute/start")
    async def create_active_from_model_execute_start(self, request: Request) -> Response:
        """Start the background atomic insert of assets from a model lcn; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_create_active_from_model_execute(progress, rows))

    async def _run_create_active_from_model_execute(self, progress: dict, rows: list[dict]) -> None:
        valid_rows: list[dict] = []
        skipped_count = 0
        sql_lines: list[str] = []
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows, skipped_count = \
                    await validate_create_active_from_model_rows(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
                    return

                progress.update(processed=0, total=1, phase="executing")

                sql_lines = actives_sql.create_active_from_model(valid_rows)
                sql_body = "\n".join(sql_lines)

                try:
                    # IMPORTANT: as in create-actives, session.rollback() below also rolls this
                    # raw call back only because the session opened a real transaction earlier
                    # (the repository queries inside validate_create_active_from_model_rows).
                    # Do not remove the session usage before this point.
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
            f"=== Create Active From Model LCN: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Actives created: {len(valid_rows)}",
            f"Skipped (lcn already occupied): {skipped_count}",
            "",
            *sql_lines,
            "",
        ]
        log_file = LOG_DIR / f"create_active_from_model_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Created %d actives from model lcn (%d skipped), log: %s",
                     len(valid_rows), skipped_count, log_file)

        message = f"Успешно создано активов: {len(valid_rows)}"
        if skipped_count:
            message += f" (пропущено {skipped_count} — позиция уже занята на части поездов)"
        progress.update(status="done", count=len(valid_rows), message=message)
