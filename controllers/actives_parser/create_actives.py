import base64
import logging
from io import BytesIO
from datetime import datetime

import openpyxl
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.response import Response

from db_manager import get_session_maker
from models import Consignment, DesignNumber, IteratorNumberLast, Storage, StoragePlace
import excel_upload
from parser_storage import LOG_DIR
from progress_tasks import error_response, start_task
from sql_builders import actives as actives_sql
from sql_builders.actives import ACTIVE_NUMBER_COUNTER_DESCRIPTION, ACTIVE_NUMBER_LENGTH

from .common import PREFIX, StorageRepository, StoragePlaceRepository, ConsignmentRepository, DesignNumberRepository, IteratorNumberLastRepository


logger = logging.getLogger("actives_parser")


async def validate_create_actives_rows(
    db_session: AsyncSession, rows: list[dict], progress: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate the material rows and build the asset creation plan (replaces add_active_spcial).

    valid_rows holds one element per asset to create ('Количество' expanded row by
    row), and each asset gets its own location record.

    The current storage.last_lcn and iterator_number_last.number are deliberately not
    read here for use in the SQL — only to check that the counter exists. The values
    themselves are read and locked (FOR UPDATE) inside the DO block when it runs (see
    actives_sql.create_actives) rather than snapshotted on the Python side during
    validation — otherwise by the time of the real run (especially for a file
    downloaded and executed later) the value in the database could have moved on.
    """
    storage_repo = StorageRepository(session=db_session)
    storage_place_repo = StoragePlaceRepository(session=db_session)
    consignment_repo = ConsignmentRepository(session=db_session)
    design_number_repo = DesignNumberRepository(session=db_session)
    iterator_repo = IteratorNumberLastRepository(session=db_session)

    errors: list[dict] = []
    valid_rows: list[dict] = []

    storage_cache: dict[str, Storage | None] = {}
    storage_place_cache: dict[str, int | None] = {}
    consignment_cache: dict[str, int | None] = {}
    design_number_cache: dict[str, int | None] = {}

    counter_row = await iterator_repo.get_one_or_none(
        IteratorNumberLast.description == ACTIVE_NUMBER_COUNTER_DESCRIPTION
    )
    if counter_row is None or counter_row.number is None:
        errors.append({
            "row": 0, "field": "*",
            "message": f"Не найден счётчик '{ACTIVE_NUMBER_COUNTER_DESCRIPTION}' в iterator_number_last",
        })
        return errors, valid_rows

    # The material column is called 'АРТИКУЛ' in newer files and
    # 'Номер ТМЦ (DU,KP,A2V)' in older ones; matched ignoring case and edge spaces.
    tmc_column: str | None = next(
        (k for k in (rows[0] if rows else {})
         if str(k).strip().lower() in ("артикул", "номер тмц (du,kp,a2v)")),
        None,
    )
    if rows and tmc_column is None:
        errors.append({"row": 0, "field": "АРТИКУЛ",
                        "message": "В файле не найдена колонка 'АРТИКУЛ' (или 'Номер ТМЦ (DU,KP,A2V)')"})
        return errors, valid_rows

    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None and (idx % 20 == 0 or row_num == len(rows)):
            progress["processed"] = row_num

        design_number_raw = str(row.get(tmc_column) or "").strip()
        if not design_number_raw or design_number_raw == "None":
            continue

        count_raw = row.get("Количество")
        try:
            count = int(count_raw)
        except (TypeError, ValueError):
            errors.append({"row": row_num, "field": "Количество", "message": f"Некорректное количество: '{count_raw}'"})
            continue
        if count < 1:
            errors.append({"row": row_num, "field": "Количество", "message": f"Количество должно быть больше 0: '{count_raw}'"})
            continue

        storage_name = str(row.get("Склад") or "").strip()
        if not storage_name:
            errors.append({"row": row_num, "field": "Склад", "message": "Поле 'Склад' пустое"})
            continue
        if storage_name not in storage_cache:
            storage_cache[storage_name] = await storage_repo.get_one_or_none(Storage.name == storage_name)
        storage = storage_cache[storage_name]
        if storage is None:
            errors.append({"row": row_num, "field": "Склад", "message": f"Склад не найден: '{storage_name}'"})
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

        special_raw = row.get("Особый учет")
        special = str(special_raw).strip() if special_raw not in (None, "") else None

        storage_place_name = str(row.get("Ячейка") or "").strip()
        id_storage_place: int | None = None
        if storage_place_name and storage_place_name != "None":
            if storage_place_name not in storage_place_cache:
                sp = await storage_place_repo.get_one_or_none(StoragePlace.name == storage_place_name)
                storage_place_cache[storage_place_name] = sp.id if sp else None
            id_storage_place = storage_place_cache[storage_place_name]
            if id_storage_place is None:
                errors.append({"row": row_num, "field": "Ячейка", "message": f"Ячейка не найдена: '{storage_place_name}'"})
                continue

        consignment_name = str(row.get("Партия") or "").strip()
        if not consignment_name:
            errors.append({"row": row_num, "field": "Партия", "message": "Поле 'Партия' пустое"})
            continue
        if consignment_name not in consignment_cache:
            c = await consignment_repo.get_one_or_none(Consignment.name == consignment_name)
            consignment_cache[consignment_name] = c.id if c else None
        id_consignment = consignment_cache[consignment_name]
        if id_consignment is None:
            errors.append({"row": row_num, "field": "Партия", "message": f"Партия не найдена: '{consignment_name}'"})
            continue

        if design_number_raw not in design_number_cache:
            dn = await design_number_repo.get_one_or_none(DesignNumber.number == design_number_raw)
            design_number_cache[design_number_raw] = dn.id if dn else None
        id_design_number = design_number_cache[design_number_raw]
        if id_design_number is None:
            errors.append({"row": row_num, "field": tmc_column, "message": f"ТМЦ не найдена: '{design_number_raw}'"})
            continue

        serial_number = (str(row.get("Серийный номер") or "").strip() if count == 1 else "none")

        for _ in range(count):
            valid_rows.append({
                "row_num": row_num,
                "id_design_number": id_design_number,
                "id_storage": storage.id,
                "id_storage_place": id_storage_place,
                "id_consignment": id_consignment,
                "type_active": type_active,
                "special_account": special,
                "serial_number": serial_number or None,
            })

    return errors, valid_rows


def reconstruct_created_active_numbers(valid_rows: list[dict], counter_after: int) -> list[str]:
    """Reconstruct the created assets' numbers from the counter's final value.

    The DO block hands out numbers strictly in valid_rows order (active_num is
    incremented per asset), so the numbers can be reconstructed deterministically from
    the counter value after execution. counter_after must be read in the same
    transaction as the DO block — FOR UPDATE still holds the counter row, so no
    concurrent run could have slipped in between.
    """
    counter_before = counter_after - len(valid_rows)
    numbers: list[str] = []
    for i, vr in enumerate(valid_rows, start=1):
        width = ACTIVE_NUMBER_LENGTH - len(vr["type_active"])
        numbers.append(vr["type_active"] + str(counter_before + i).rjust(width, "0"))
    return numbers


def build_created_actives_xlsx(rows: list[tuple[str, str | None]]) -> str:
    """Build an xlsx of (active_number, serial_number) and return it base64-encoded."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["active_number", "serial_number"])
    for active_number, serial_number in rows:
        ws.append([active_number, serial_number])
    buf = BytesIO()
    wb.save(buf)
    return base64.b64encode(buf.getvalue()).decode()


class CreateActivesController(Controller):
    path = "/actives-parser"

    @post("/create-actives/generate-sql/start")
    async def create_actives_generate_sql_start(self, request: Request) -> Response:
        """Start background generation of the SQL file creating assets from materials; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_create_actives_generate(progress, rows))

    async def _run_create_actives_generate(self, progress: dict, rows: list[dict]) -> None:
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = \
                    await validate_create_actives_rows(session, rows, progress=progress)
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка: {e}"}])
            return

        if errors:
            progress.update(status="error", errors=errors)
            return
        if not valid_rows:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
            return

        sql_lines = actives_sql.create_actives(valid_rows)
        full_sql = "\n".join(["BEGIN;", *sql_lines, "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    @post("/create-actives/execute/start")
    async def create_actives_execute_start(self, request: Request) -> Response:
        """Start the background atomic insert of assets from materials; returns a task_id."""
        rows = excel_upload.stored_rows(request, PREFIX)
        if rows is None:
            return error_response("Данные не загружены")

        return start_task(len(rows), lambda progress: self._run_create_actives_execute(progress, rows))

    async def _run_create_actives_execute(self, progress: dict, rows: list[dict]) -> None:
        valid_rows: list[dict] = []
        created_numbers: list[str] = []
        xlsx_rows: list[tuple[str, str | None]] = []
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                errors, valid_rows = \
                    await validate_create_actives_rows(session, rows, progress=progress)

                if errors:
                    progress.update(status="error", errors=errors)
                    return
                if not valid_rows:
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для создания активов"}])
                    return

                progress.update(processed=0, total=1, phase="executing")

                sql_lines = actives_sql.create_actives(valid_rows)
                sql_body = "\n".join(sql_lines)

                try:
                    # IMPORTANT: as in train_parser, session.rollback() below also rolls this
                    # raw call back only because the session opened a real transaction earlier
                    # (the repository queries inside validate_create_actives_rows). Do not
                    # remove the session usage before this point.
                    conn = await session.connection()
                    raw_conn = await conn.get_raw_connection()
                    await raw_conn.driver_connection.execute(sql_body)

                    counter_after = (await session.execute(
                        text("SELECT number FROM public.iterator_number_last WHERE description = :d"),
                        {"d": ACTIVE_NUMBER_COUNTER_DESCRIPTION},
                    )).scalar()
                    created_numbers = reconstruct_created_active_numbers(valid_rows, counter_after)

                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
                    return

                progress["processed"] = 1

                # Serial numbers are read from the database rather than the file, so the
                # report reflects the records actually created
                db_rows = (await session.execute(
                    text("SELECT active_number, serial_number FROM public.actives "
                         "WHERE active_number = ANY(:nums)"),
                    {"nums": created_numbers},
                )).all()
                serial_by_number = {an: sn for an, sn in db_rows}
                xlsx_rows = [(n, serial_by_number.get(n)) for n in created_numbers]
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
            return

        now = datetime.now()
        log_lines = [
            f"=== Create Actives From TMC: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Actives created: {len(valid_rows)}",
            "",
            *(f"{an}\t{sn or ''}" for an, sn in xlsx_rows),
            "",
        ]
        log_file = LOG_DIR / f"create_actives_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Created %d actives from TMC, log saved to %s", len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows),
                         xlsx=build_created_actives_xlsx(xlsx_rows),
                         xlsx_filename="actives.xlsx",
                         message=f"Успешно создано активов: {len(valid_rows)}")
