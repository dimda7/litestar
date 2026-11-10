import json
import logging
from datetime import datetime
from typing import cast

from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Redirect, Response, Template
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

import excel_upload
from models import (
    CommodityCode,
    CommodityGroup,
    CounterGroup,
    DesignNumber,
    DesignNumberStatus,
    DesignNumberToMaintUse,
    MaintUse,
    MeasureType,
    ProductGroup,
    UnitType,
)
from parser_storage import LOG_DIR
from schemas import DesignNumberSelectSheetRequest
from sql_builders import design_number as design_number_sql
from sql_builders.actives import MILEAGE_COUNTER_TYPE_ID

logger = logging.getLogger("design_number_parser")

PREFIX = "dn_parser"

# public.counter_type ids: 1 'Моточасы', 2 'Время', 3 'Пробег'
# (MILEAGE_COUNTER_TYPE_ID, sql_builders/actives.py).
MOTOR_HOURS_COUNTER_TYPE_ID = 1
TIME_COUNTER_TYPE_ID = 2

# public.frequency_type ids of the units a counter is measured in.
KM_FREQUENCY_TYPE_ID = 5
ENGINE_HOURS_FREQUENCY_TYPE_ID = 7
MM_FREQUENCY_TYPE_ID = 8

# The counter_active rows created by the counter_group sync carry the unit their
# counter is measured in: 'Моточасы' counts engine hours, 'Пробег' kilometres,
# and every remaining counter type (4..30 — wheel and brake disc wear) is
# measured in millimetres. 'Время' gets no counter_active row at all.
FREQUENCY_TYPE_BY_COUNTER_TYPE: dict[int, int] = {
    MOTOR_HOURS_COUNTER_TYPE_ID: ENGINE_HOURS_FREQUENCY_TYPE_ID,
    MILEAGE_COUNTER_TYPE_ID: KM_FREQUENCY_TYPE_ID,
}


def _frequency_type_for(counter_type_id: int) -> int:
    return FREQUENCY_TYPE_BY_COUNTER_TYPE.get(counter_type_id, MM_FREQUENCY_TYPE_ID)


RENAME_OLD_FIELD = "Старый ТМЦ"
RENAME_NEW_FIELD = "Новый ТМЦ"


async def validate_rename_rows(
    db_session: AsyncSession, rows: list[dict]
) -> tuple[list[dict], list[tuple[int, str, str]]]:
    errors: list[dict] = []
    valid_rows: list[tuple[int, str, str]] = []
    old_rows: dict[str, int] = {}
    new_rows: dict[str, int] = {}

    for idx, row in enumerate(rows):
        old = str(row.get(RENAME_OLD_FIELD) or "")
        new = str(row.get(RENAME_NEW_FIELD) or "")
        row_num = idx + 1
        if not old:
            errors.append({"row": row_num, "field": RENAME_OLD_FIELD, "message": f"Поле '{RENAME_OLD_FIELD}' пустое"})
            continue
        if not new:
            errors.append({"row": row_num, "field": RENAME_NEW_FIELD, "message": f"Поле '{RENAME_NEW_FIELD}' пустое"})
            continue
        if old in old_rows:
            errors.append(
                {
                    "row": row_num,
                    "field": RENAME_OLD_FIELD,
                    "message": f"Старый номер {old!r} уже указан в строке {old_rows[old]}",
                }
            )
            continue
        old_rows[old] = row_num
        if new in new_rows:
            errors.append(
                {
                    "row": row_num,
                    "field": RENAME_NEW_FIELD,
                    "message": f"Новый номер {new!r} уже указан в строке {new_rows[new]}",
                }
            )
            continue
        new_rows[new] = row_num
        if new != new.strip() or "\xa0" in new:
            errors.append(
                {
                    "row": row_num,
                    "field": RENAME_NEW_FIELD,
                    "message": f"Новый номер содержит пробельные символы: {new!r}",
                }
            )
            continue
        dn_id = (
            await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == old))
        ).scalar_one_or_none()
        if dn_id is None:
            errors.append({"row": row_num, "field": RENAME_OLD_FIELD, "message": f"design_number не найден: {old!r}"})
            continue
        if new == old:
            errors.append({"row": row_num, "field": RENAME_NEW_FIELD, "message": f"Номер не меняется: {new!r}"})
            continue
        taken_id = (
            await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == new))
        ).scalar_one_or_none()
        if taken_id is not None:
            errors.append(
                {"row": row_num, "field": RENAME_NEW_FIELD, "message": f"ТМЦ {new!r} уже существует (id={taken_id})"}
            )
            continue
        valid_rows.append((dn_id, old, new))

    return errors, valid_rows


# File column -> catalogue column its value is looked up in.
UPDATE_REFERENCES = {
    "unit_type": UnitType.name,
    "commodity_group": CommodityGroup.name,
    "commodity_code": CommodityCode.name,
    "design_number_status": DesignNumberStatus.status,
    "measure_type": MeasureType.name,
    "product_group": ProductGroup.name,
    "counter_group": CounterGroup.name,
}
# public.unit_type 'Поезд': a train has neither is_repairable nor a measure type.
TRAIN_UNIT_TYPE_ID = 1
# design_number.number is varchar(32).
DESIGN_NUMBER_MAX_LENGTH = 32
UPDATE_HEADERS = (
    "number",
    "description",
    "unit_type",
    "is_serial",
    "is_created_1c",
    "commodity_group",
    "commodity_code",
    "design_number_status",
    "measure_type",
    "is_repairable",
    "product_group",
    "description_1c",
    "main_number",
    "counter_group",
    "maint_use",
    "is_serial_1c",
)
TRUE_VALUES = ("true", "1", "да")
FALSE_VALUES = ("false", "0", "нет")
UPDATE_FLAGS = ("is_serial", "is_created_1c", "is_repairable", "is_serial_1c")
NOT_NULL_FLAGS = ("is_serial", "is_serial_1c")


def _cell_text(value: object) -> str:
    # Excel cells arrive with their native types: FALSE and 0 must not read as empty.
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _similarity_key(number: str) -> str:
    return number.strip().casefold()


def _cell(row: dict, column: str) -> str | None:
    value = _cell_text(row.get(column)).strip()
    return None if value == "" or value.upper() == "NULL" else value


async def _catalogue(db_session: AsyncSession, name_column: InstrumentedAttribute[str | None]) -> dict[str, list[int]]:
    model = name_column.class_
    ids: dict[str, list[int]] = {}
    for id_, name in (await db_session.execute(select(model.id, name_column))).all():
        if name:
            ids.setdefault(name.strip().lower(), []).append(id_)
    return ids


def _lookup(catalogue: dict[str, list[int]], value: str, row_num: int, column: str, errors: list[dict]) -> int | None:
    ids = catalogue.get(value.lower(), [])
    if len(ids) == 1:
        return ids[0]
    message = f"{column} не найден: {value!r}" if not ids else f"{column} неоднозначен: {value!r} (записей: {len(ids)})"
    errors.append({"row": row_num, "field": column, "message": message})
    return None


async def _measure_types(db_session: AsyncSession) -> dict[str, list[int]]:
    # measure_type really holds duplicate names ('пог. м', 'рул'): of those, the
    # one with a code_1c wins, and the highest id breaks any remaining tie.
    best: dict[str, tuple[bool, int]] = {}
    for id_, name, code_1c in (
        await db_session.execute(select(MeasureType.id, MeasureType.name, MeasureType.code_1c))
    ).all():
        if name:
            key = name.strip().lower()
            best[key] = max(best.get(key, (False, 0)), (code_1c is not None, id_))
    return {key: [id_] for key, (_, id_) in best.items()}


def _missing_headers(rows: list[dict]) -> list[dict]:
    missing = [header for header in UPDATE_HEADERS if rows and header not in rows[0]]
    return [{"row": 0, "field": "*", "message": f"В файле нет колонок: {', '.join(missing)}"}] if missing else []


async def _field_catalogues(db_session: AsyncSession) -> dict[str, dict[str, list[int]]]:
    catalogues = {
        column: await _catalogue(db_session, name_column) for column, name_column in UPDATE_REFERENCES.items()
    }
    catalogues["measure_type"] = await _measure_types(db_session)
    catalogues["maint_use"] = await _catalogue(db_session, MaintUse.name)
    return catalogues


def _parse_fields(
    row: dict, row_num: int, catalogues: dict[str, dict[str, list[int]]], errors: list[dict]
) -> dict | None:
    row_errors: list[dict] = []
    refs: dict[str, int | None] = {}
    for column in UPDATE_REFERENCES:
        value = _cell(row, column)
        refs[column] = _lookup(catalogues[column], value, row_num, column, row_errors) if value else None
    flags: dict[str, bool | None] = {}
    for column in UPDATE_FLAGS:
        value = _cell(row, column)
        if value is None:
            flags[column] = None
            if column in NOT_NULL_FLAGS:
                row_errors.append({"row": row_num, "field": column, "message": f"Поле '{column}' не может быть NULL"})
        elif value.lower() in TRUE_VALUES + FALSE_VALUES:
            flags[column] = value.lower() in TRUE_VALUES
        else:
            row_errors.append(
                {
                    "row": row_num,
                    "field": column,
                    "message": f"Неверное значение {column}: {value!r} (ожидается true/false)",
                }
            )
    if refs["unit_type"] == TRAIN_UNIT_TYPE_ID:
        for column in ("is_repairable", "measure_type"):
            if _cell(row, column) is not None:
                row_errors.append(
                    {
                        "row": row_num,
                        "field": column,
                        "message": f"Для unit_type 'Поезд' поле '{column}' должно быть NULL",
                    }
                )
    maint_use_ids: list[int] = []
    for item in (_cell(row, "maint_use") or "").split(","):
        if item.strip():
            id_maint_use = _lookup(catalogues["maint_use"], item.strip(), row_num, "maint_use", row_errors)
            if id_maint_use is not None and id_maint_use not in maint_use_ids:
                maint_use_ids.append(id_maint_use)
    if row_errors:
        errors.extend(row_errors)
        return None
    return dict(
        description=_cell(row, "description"),
        id_unit_type=refs["unit_type"],
        is_serial=flags["is_serial"],
        is_created_1c=flags["is_created_1c"],
        id_commodity_group=refs["commodity_group"],
        id_commodity_code=refs["commodity_code"],
        id_design_number_status=refs["design_number_status"],
        id_measure_type=refs["measure_type"],
        is_repairable=flags["is_repairable"],
        id_product_group=refs["product_group"],
        description_1c=_cell(row, "description_1c"),
        main_number=_cell(row, "main_number"),
        id_counter_group=refs["counter_group"],
        is_serial_1c=flags["is_serial_1c"],
        maint_use_ids=tuple(maint_use_ids),
    )


async def validate_update_rows(
    db_session: AsyncSession, rows: list[dict]
) -> tuple[list[dict], list[design_number_sql.DesignNumberRow]]:
    errors: list[dict] = _missing_headers(rows)
    if errors:
        return errors, []
    valid_rows: list[design_number_sql.DesignNumberRow] = []

    catalogues = await _field_catalogues(db_session)
    numbers = [_cell_text(row.get("number")) for row in rows]
    dn_ids: dict[str | None, int] = dict(
        (await db_session.execute(select(DesignNumber.number, DesignNumber.id).where(DesignNumber.number.in_(numbers))))
        .tuples()
        .all()
    )

    number_rows: dict[str, int] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        number = numbers[idx]
        if not number:
            errors.append({"row": row_num, "field": "number", "message": "Поле 'number' пустое"})
            continue
        if number in number_rows:
            errors.append(
                {
                    "row": row_num,
                    "field": "number",
                    "message": f"Номер {number!r} уже указан в строке {number_rows[number]}",
                }
            )
            continue
        number_rows[number] = row_num
        if number not in dn_ids:
            errors.append({"row": row_num, "field": "number", "message": f"design_number не найден: {number!r}"})
            continue
        fields = _parse_fields(row, row_num, catalogues, errors)
        if fields is not None:
            valid_rows.append(design_number_sql.DesignNumberRow(id=dn_ids[number], number=number, **fields))

    return errors, valid_rows


async def validate_create_rows(
    db_session: AsyncSession, rows: list[dict]
) -> tuple[list[dict], list[design_number_sql.DesignNumberRow]]:
    errors: list[dict] = _missing_headers(rows)
    if errors:
        return errors, []
    valid_rows: list[design_number_sql.DesignNumberRow] = []

    catalogues = await _field_catalogues(db_session)
    existing: dict[str, int] = {}
    similar: dict[str, tuple[str, int]] = {}
    for number, id_ in (await db_session.execute(select(DesignNumber.number, DesignNumber.id))).all():
        if number:
            existing[number] = id_
            similar.setdefault(_similarity_key(number), (number, id_))
    number_rows: dict[str, int] = {}
    similar_rows: dict[str, int] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        number = _cell_text(row.get("number"))
        if _cell(row, "number") is None:
            errors.append({"row": row_num, "field": "number", "message": "Поле 'number' пустое"})
            continue
        if number != number.strip() or "\xa0" in number:
            errors.append(
                {"row": row_num, "field": "number", "message": f"Номер содержит пробельные символы: {number!r}"}
            )
            continue
        if len(number) > DESIGN_NUMBER_MAX_LENGTH:
            errors.append(
                {
                    "row": row_num,
                    "field": "number",
                    "message": f"Номер длиннее {DESIGN_NUMBER_MAX_LENGTH} символов ({len(number)}): {number!r}",
                }
            )
            continue
        key = _similarity_key(number)
        if number in number_rows:
            errors.append(
                {
                    "row": row_num,
                    "field": "number",
                    "message": f"Номер {number!r} уже указан в строке {number_rows[number]}",
                }
            )
            continue
        if key in similar_rows:
            errors.append(
                {
                    "row": row_num,
                    "field": "number",
                    "message": f"Номер {number!r} похож на номер из строки {similar_rows[key]}",
                }
            )
            continue
        number_rows[number] = row_num
        similar_rows[key] = row_num
        if key in similar and number not in existing:
            similar_number, similar_id = similar[key]
            errors.append(
                {
                    "row": row_num,
                    "field": "number",
                    "message": f"В БД есть похожий номер {similar_number!r} (id={similar_id})",
                }
            )
            continue
        fields = _parse_fields(row, row_num, catalogues, errors)
        if fields is not None:
            valid_rows.append(design_number_sql.DesignNumberRow(id=existing.get(number), number=number, **fields))

    return errors, valid_rows


async def snapshot_design_numbers(db_session: AsyncSession, ids: list[int]) -> list[design_number_sql.DesignNumberRow]:
    links: dict[int, list[int]] = {}
    for id_design_number, id_maint_use in (
        await db_session.execute(
            select(DesignNumberToMaintUse.id_design_number, DesignNumberToMaintUse.id_maint_use)
            .where(DesignNumberToMaintUse.id_design_number.in_(ids))
            .order_by(DesignNumberToMaintUse.id_maint_use)
        )
    ).all():
        links.setdefault(id_design_number, []).append(id_maint_use)
    design_numbers = (
        (await db_session.execute(select(DesignNumber).where(DesignNumber.id.in_(ids)).order_by(DesignNumber.id)))
        .scalars()
        .all()
    )
    return [
        design_number_sql.DesignNumberRow(
            id=dn.id,
            # The ids were matched by number, so it is never NULL here.
            number=cast(str, dn.number),
            description=dn.description,
            id_unit_type=dn.id_unit_type,
            is_serial=dn.is_serial,
            is_created_1c=dn.is_created_1c,
            id_commodity_group=dn.id_commodity_group,
            id_commodity_code=dn.id_commodity_code,
            id_design_number_status=dn.id_design_number_status,
            id_measure_type=dn.id_measure_type,
            is_repairable=dn.is_repairable,
            id_product_group=dn.id_product_group,
            description_1c=dn.description_1c,
            main_number=dn.main_number,
            id_counter_group=dn.id_counter_group,
            is_serial_1c=dn.is_serial_1c,
            maint_use_ids=tuple(links.get(dn.id, [])),
        )
        for dn in design_numbers
    ]


class DesignNumberParserController(Controller):
    path = "/design-number-parser"

    @get("/")
    async def index(
        self,
        request: Request,
        page: int = 1,
        per_page: int = 10,
        select_sheet: bool = False,
    ) -> Template:
        page = max(page, 1)
        per_page = min(per_page, 200)
        error: str = request.session.pop(f"{PREFIX}_error", "")

        pending_sheets: list[str] = []
        pending_filename: str = ""
        if select_sheet:
            pending_sheets = request.session.get(f"{PREFIX}_pending_sheets", [])
            pending_filename = request.session.get(f"{PREFIX}_pending_filename", "")

        stored = excel_upload.stored_data(request, PREFIX)

        all_rows: list[dict] = stored["rows"] if stored else []
        headers: list[str] = stored["headers"] if stored else []
        filename: str = stored["filename"] if stored else ""

        total = len(all_rows)
        total_pages = max((total + per_page - 1) // per_page, 1)
        if page > total_pages:
            page = total_pages

        offset = (page - 1) * per_page
        rows = all_rows[offset : offset + per_page]

        return Template(
            template_name="design_number_parser.html",
            context={
                "headers": headers,
                "rows": rows,
                "all_rows": all_rows,
                "filename": filename,
                "error": error,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "design_number_parser",
                "pending_sheets": pending_sheets,
                "pending_filename": pending_filename,
            },
        )

    @post("/upload")
    async def upload(self, request: Request) -> Redirect:
        """Upload of an Excel file (.xlsx/.xls) — see excel_upload.handle_upload."""
        return await excel_upload.handle_upload(request, PREFIX, "/design-number-parser")

    @post("/select-sheet")
    async def select_sheet(
        self,
        request: Request,
        data: DesignNumberSelectSheetRequest = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        """Sheet choice for a multi-sheet Excel file — see excel_upload.handle_sheet_choice."""
        return excel_upload.handle_sheet_choice(request, PREFIX, "/design-number-parser", data.sheet_name)

    async def _validate_counter_group(
        self, db_session: AsyncSession, rows: list[dict]
    ) -> tuple[list[dict], list[tuple[int, str, int]]]:
        """Validate rows for counter_group update.
        Returns (errors, valid_rows) where valid_rows is [(design_number_id, number, counter_group_id), ...]
        """
        errors: list[dict] = []
        valid_rows: list[tuple[int, str, int]] = []

        cg_result = await db_session.execute(select(CounterGroup.id, CounterGroup.name))
        cg_map: dict[str, int] = {}
        for cg in cg_result.all():
            if cg[1]:
                cg_map[cg[1].strip().lower()] = cg[0]

        for idx, row in enumerate(rows):
            row_num = idx + 1
            number = str(row.get("number", "") or "").strip()
            cg_name = str(row.get("counter_group", "") or "").strip()

            if not number:
                errors.append({"row": row_num, "field": "number", "message": "Поле 'number' пустое"})
                continue

            dn_result = await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == number))
            dn_id = dn_result.scalar_one_or_none()
            if dn_id is None:
                errors.append({"row": row_num, "field": "number", "message": f"design_number не найден: '{number}'"})
                continue

            if not cg_name:
                errors.append({"row": row_num, "field": "counter_group", "message": "Поле 'counter_group' пустое"})
                continue

            cg_id = cg_map.get(cg_name.lower())
            if cg_id is None:
                errors.append(
                    {"row": row_num, "field": "counter_group", "message": f"counter_group не найден: '{cg_name}'"}
                )
                continue

            valid_rows.append((dn_id, number, cg_id))

        return errors, valid_rows

    async def _validate_unit_type(
        self, db_session: AsyncSession, rows: list[dict]
    ) -> tuple[list[dict], list[tuple[int, str, int]]]:
        """Validate rows for unit_type update.
        Returns (errors, valid_rows) where valid_rows is [(design_number_id, number, unit_type_id), ...]
        """
        errors: list[dict] = []
        valid_rows: list[tuple[int, str, int]] = []

        ut_result = await db_session.execute(select(UnitType.id, UnitType.name))
        ut_map: dict[str, int] = {}
        for ut in ut_result.all():
            if ut[1]:
                ut_map[ut[1].strip().lower()] = ut[0]

        for idx, row in enumerate(rows):
            row_num = idx + 1
            number = str(row.get("number", "") or "").strip()
            ut_name = str(row.get("unit_type", "") or "").strip()

            if not number:
                errors.append({"row": row_num, "field": "number", "message": "Поле 'number' пустое"})
                continue

            dn_result = await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == number))
            dn_id = dn_result.scalar_one_or_none()
            if dn_id is None:
                errors.append({"row": row_num, "field": "number", "message": f"design_number не найден: '{number}'"})
                continue

            if not ut_name:
                errors.append({"row": row_num, "field": "unit_type", "message": "Поле 'unit_type' пустое"})
                continue

            ut_id = ut_map.get(ut_name.lower())
            if ut_id is None:
                errors.append({"row": row_num, "field": "unit_type", "message": f"unit_type не найден: '{ut_name}'"})
                continue

            valid_rows.append((dn_id, number, ut_id))

        return errors, valid_rows

    async def _validate_is_serial_1c(
        self, db_session: AsyncSession, rows: list[dict]
    ) -> tuple[list[dict], list[tuple[int, str, bool]]]:
        """Validate rows for is_serial_1c update.
        Returns (errors, valid_rows) where valid_rows is [(design_number_id, number, is_serial_1c), ...]
        """
        errors: list[dict] = []
        valid_rows: list[tuple[int, str, bool]] = []

        for idx, row in enumerate(rows):
            row_num = idx + 1
            number = str(row.get("number", "") or "").strip()
            is_serial_1c_raw = str(row.get("is_serial_1c", "") or "").strip().lower()

            if not number:
                errors.append({"row": row_num, "field": "number", "message": "Поле 'number' пустое"})
                continue

            dn_result = await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == number))
            dn_id = dn_result.scalar_one_or_none()
            if dn_id is None:
                errors.append({"row": row_num, "field": "number", "message": f"design_number не найден: '{number}'"})
                continue

            if is_serial_1c_raw not in ("true", "false", "1", "0", "да", "нет"):
                errors.append(
                    {
                        "row": row_num,
                        "field": "is_serial_1c",
                        "message": f"Неверное значение is_serial_1c: '{is_serial_1c_raw}' (ожидается true/false)",
                    }
                )
                continue

            is_serial_1c = is_serial_1c_raw in ("true", "1", "да")
            valid_rows.append((dn_id, number, is_serial_1c))

        return errors, valid_rows

    @post("/update-counter-group")
    async def update_counter_group(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_counter_group(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps(
                    {
                        "status": "error",
                        "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}],
                    }
                ),
                status_code=200,
                media_type="application/json",
            )

        try:
            for _dn_id, number, cg_id in valid_rows:
                await db_session.execute(
                    text("UPDATE public.design_number SET id_counter_group = :cg_id WHERE number = :number"),
                    {"cg_id": cg_id, "number": number},
                )
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            return Response(
                content=json.dumps(
                    {"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]}
                ),
                status_code=200,
                media_type="application/json",
            )

        now = datetime.now()
        log_lines = [
            f"=== Update id_counter_group: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        log_lines.extend(design_number_sql.update_counter_group(valid_rows))
        log_lines.append("")

        log_file = LOG_DIR / f"update_counter_group_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated counter_group for %d rows, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps(
                {
                    "status": "ok",
                    "count": len(valid_rows),
                    "message": f"Успешно обновлено id_counter_group для {len(valid_rows)} записей",
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/update-unit-type")
    async def update_unit_type(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_unit_type(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps(
                    {
                        "status": "error",
                        "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}],
                    }
                ),
                status_code=200,
                media_type="application/json",
            )

        try:
            for _dn_id, number, ut_id in valid_rows:
                await db_session.execute(
                    text("UPDATE public.design_number SET id_unit_type = :ut_id WHERE number = :number"),
                    {"ut_id": ut_id, "number": number},
                )
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            return Response(
                content=json.dumps(
                    {"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]}
                ),
                status_code=200,
                media_type="application/json",
            )

        now = datetime.now()
        log_lines = [
            f"=== Update id_unit_type: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        log_lines.extend(design_number_sql.update_unit_type(valid_rows))
        log_lines.append("")

        log_file = LOG_DIR / f"update_unit_type_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated unit_type for %d rows, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps(
                {
                    "status": "ok",
                    "count": len(valid_rows),
                    "message": f"Успешно обновлено id_unit_type для {len(valid_rows)} записей",
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-unit-type")
    async def generate_sql_unit_type(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_unit_type(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.update_unit_type(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )

    @post("/sync-counter-active")
    async def sync_counter_active(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_counter_group(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps(
                    {
                        "status": "error",
                        "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для синхронизации"}],
                    }
                ),
                status_code=200,
                media_type="application/json",
            )

        deleted_total = 0
        inserted_total = 0
        sync_details: list[str] = []

        try:
            for dn_id, number, cg_id in valid_rows:
                ct_result = await db_session.execute(
                    text(
                        "SELECT ctg.id_counter_type "
                        "FROM public.counter_type_to_group ctg "
                        "WHERE ctg.id_counter_group = :cg_id"
                    ),
                    {"cg_id": cg_id},
                )
                new_types = {row[0] for row in ct_result.all()}

                active_result = await db_session.execute(
                    text("SELECT id FROM public.actives WHERE id_design_number = :dn_id"),
                    {"dn_id": dn_id},
                )
                active_ids = [row[0] for row in active_result.all()]

                if not active_ids:
                    continue

                deleted_dn = 0
                inserted_dn = 0

                for aid in active_ids:
                    existing_result = await db_session.execute(
                        text("SELECT id, id_counter_type FROM public.counter_active WHERE id_active = :aid"),
                        {"aid": aid},
                    )
                    existing_rows = existing_result.all()

                    for ca_id, ca_type in existing_rows:
                        if ca_type not in new_types:
                            await db_session.execute(
                                text("DELETE FROM public.counter_active WHERE id = :id"),
                                {"id": ca_id},
                            )
                            deleted_dn += 1

                    existing_types = {row[1] for row in existing_rows}
                    missing_types = new_types - existing_types

                    for ct_id in missing_types:
                        if ct_id == TIME_COUNTER_TYPE_ID:
                            continue
                        freq = _frequency_type_for(ct_id)
                        now_ts = datetime.now()
                        await db_session.execute(
                            text(
                                "INSERT INTO public.counter_active "
                                "(id_active, date, value, id_counter_type, id_frequency_type, is_train, date_create) "
                                "VALUES (:aid, :dt, 0, :ct_id, :freq, false, :dt)"
                            ),
                            {"aid": aid, "dt": now_ts, "ct_id": ct_id, "freq": freq},
                        )
                        inserted_dn += 1

                deleted_total += deleted_dn
                inserted_total += inserted_dn
                sync_details.append(
                    f"design_number id={dn_id} number='{number}' counter_group={cg_id}: "
                    f"типы={sorted(new_types)}, actives={len(active_ids)}, "
                    f"удалено={deleted_dn}, добавлено={inserted_dn}"
                )

            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            return Response(
                content=json.dumps(
                    {"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]}
                ),
                status_code=200,
                media_type="application/json",
            )

        now = datetime.now()
        log_lines = [
            f"=== Sync counter_active: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows processed: {len(valid_rows)}",
            f"Counter_active удалено: {deleted_total}, добавлено: {inserted_total}",
            "",
        ]
        for dn_id, number, cg_id in valid_rows:
            log_lines.append(f"design_number id={dn_id} number='{number}' counter_group={cg_id}")
        if sync_details:
            log_lines.append("")
            log_lines.append("Детали синхронизации:")
            for detail in sync_details:
                log_lines.append(f"  {detail}")
        log_lines.append("")

        log_file = LOG_DIR / f"sync_counter_active_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info(
            "Synced counter_active for %d rows, deleted %d, inserted %d, log: %s",
            len(valid_rows),
            deleted_total,
            inserted_total,
            log_file,
        )

        return Response(
            content=json.dumps(
                {
                    "status": "ok",
                    "count": len(valid_rows),
                    "deleted_counter_active": deleted_total,
                    "inserted_counter_active": inserted_total,
                    "message": f"Синхронизация counter_active: {len(valid_rows)} design_number, удалено {deleted_total}, добавлено {inserted_total}",
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-counter-active")
    async def generate_sql_counter_active(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_counter_group(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines: list[str] = []

        for dn_id, _number, cg_id in valid_rows:
            ct_result = await db_session.execute(
                text(
                    "SELECT ctg.id_counter_type "
                    "FROM public.counter_type_to_group ctg "
                    "WHERE ctg.id_counter_group = :cg_id"
                ),
                {"cg_id": cg_id},
            )
            new_types = {row[0] for row in ct_result.all()}

            active_result = await db_session.execute(
                text("SELECT id FROM public.actives WHERE id_design_number = :dn_id"),
                {"dn_id": dn_id},
            )
            active_ids = [row[0] for row in active_result.all()]

            if not active_ids:
                continue

            for aid in active_ids:
                existing_result = await db_session.execute(
                    text("SELECT id, id_counter_type FROM public.counter_active WHERE id_active = :aid"),
                    {"aid": aid},
                )
                existing_rows = existing_result.all()

                for ca_id, ca_type in existing_rows:
                    if ca_type not in new_types:
                        sql_lines.append(f"DELETE FROM public.counter_active WHERE id = {ca_id};")

                existing_types = {row[1] for row in existing_rows}
                missing_types = new_types - existing_types

                for ct_id in missing_types:
                    if ct_id == TIME_COUNTER_TYPE_ID:
                        continue
                    freq = _frequency_type_for(ct_id)
                    sql_lines.append(
                        f"INSERT INTO public.counter_active "
                        f"(id_active, date, value, id_counter_type, id_frequency_type, is_train, date_create) "
                        f"VALUES ({aid}, NOW(), 0, {ct_id}, {freq}, false, NOW());"
                    )

        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-counter-group")
    async def generate_sql_counter_group(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_counter_group(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.update_counter_group(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )

    @post("/update-is-serial-1c")
    async def update_is_serial_1c(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_is_serial_1c(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps(
                    {
                        "status": "error",
                        "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}],
                    }
                ),
                status_code=200,
                media_type="application/json",
            )

        try:
            for _dn_id, number, is_serial_1c in valid_rows:
                await db_session.execute(
                    text("UPDATE public.design_number SET is_serial_1c = :val WHERE number = :number"),
                    {"val": is_serial_1c, "number": number},
                )
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            return Response(
                content=json.dumps(
                    {"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]}
                ),
                status_code=200,
                media_type="application/json",
            )

        now = datetime.now()
        log_lines = [
            f"=== Update is_serial_1c: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        log_lines.extend(design_number_sql.update_is_serial_1c(valid_rows))
        log_lines.append("")

        log_file = LOG_DIR / f"update_is_serial_1c_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated is_serial_1c for %d rows, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps(
                {
                    "status": "ok",
                    "count": len(valid_rows),
                    "message": f"Успешно обновлено is_serial_1c для {len(valid_rows)} записей",
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-is-serial-1c")
    async def generate_sql_is_serial_1c(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await self._validate_is_serial_1c(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.update_is_serial_1c(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )

    @post("/rename-design-number")
    async def rename_design_number(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_rename_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps(
                    {
                        "status": "error",
                        "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}],
                    }
                ),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.rename(valid_rows)
        try:
            conn = await db_session.connection()
            for line in sql_lines:
                await conn.exec_driver_sql(line)
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            return Response(
                content=json.dumps(
                    {"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]}
                ),
                status_code=200,
                media_type="application/json",
            )

        now = datetime.now()
        log_lines = [
            f"=== Rename design_number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        log_lines.extend(f"id={dn_id}: {old!r} -> {new!r}" for dn_id, old, new in valid_rows)
        log_lines.append("")
        log_lines.extend(sql_lines)
        log_lines.append("")

        log_file = LOG_DIR / f"rename_design_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Renamed %d design numbers, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps(
                {"status": "ok", "count": len(valid_rows), "message": f"Успешно переименовано {len(valid_rows)} ТМЦ"}
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-rename-design-number")
    async def generate_sql_rename_design_number(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_rename_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.rename(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )

    @post("/update-design-number")
    async def update_design_number(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_update_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps(
                    {
                        "status": "error",
                        "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}],
                    }
                ),
                status_code=200,
                media_type="application/json",
            )

        snapshot = await snapshot_design_numbers(db_session, [row.id for row in valid_rows if row.id is not None])
        sql_lines = design_number_sql.update_full(valid_rows)

        # Written before execution: the overwrite is irreversible, so the
        # rollback SQL has to exist even if the run itself fails half-way.
        now = datetime.now()
        log_lines = [
            f"=== Update design_number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows: {len(valid_rows)}",
            "",
            "-- Rollback (state before the update):",
            *design_number_sql.update_full(snapshot),
            "",
            "-- Executed:",
            *sql_lines,
            "",
        ]
        log_file = LOG_DIR / f"update_design_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))

        try:
            conn = await db_session.connection()
            raw_conn = await conn.get_raw_connection()
            assert raw_conn.driver_connection is not None
            await raw_conn.driver_connection.execute("\n".join(sql_lines))
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            return Response(
                content=json.dumps(
                    {"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]}
                ),
                status_code=200,
                media_type="application/json",
            )
        logger.info("Updated %d design numbers, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps(
                {"status": "ok", "count": len(valid_rows), "message": f"Успешно обновлено {len(valid_rows)} ТМЦ"}
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-update-design-number")
    async def generate_sql_update_design_number(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_update_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.update_full(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(valid_rows)}),
            status_code=200,
            media_type="application/json",
        )

    @post("/create-design-number")
    async def create_design_number(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_create_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps(
                    {
                        "status": "error",
                        "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для создания"}],
                    }
                ),
                status_code=200,
                media_type="application/json",
            )

        snapshot = await snapshot_design_numbers(db_session, [row.id for row in valid_rows if row.id is not None])
        sql_lines = design_number_sql.create_or_update(valid_rows)
        created = sum(1 for row in valid_rows if row.id is None)
        updated = len(valid_rows) - created

        now = datetime.now()
        log_lines = [
            f"=== Create design_number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows: {len(valid_rows)} (created {created}, updated {updated})",
            "",
            "-- Rollback (deletes the created design numbers, restores the updated ones):",
            *design_number_sql.undo_create_or_update(valid_rows, snapshot),
            "",
            "-- Executed:",
            *sql_lines,
            "",
        ]
        log_file = LOG_DIR / f"create_design_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))

        try:
            conn = await db_session.connection()
            raw_conn = await conn.get_raw_connection()
            assert raw_conn.driver_connection is not None
            await raw_conn.driver_connection.execute("\n".join(sql_lines))
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            return Response(
                content=json.dumps(
                    {"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]}
                ),
                status_code=200,
                media_type="application/json",
            )
        logger.info("Created %d and updated %d design numbers, log: %s", created, updated, log_file)

        return Response(
            content=json.dumps(
                {
                    "status": "ok",
                    "count": len(valid_rows),
                    "message": f"Успешно создано {created} ТМЦ, обновлено {updated} ТМЦ",
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-create-design-number")
    async def generate_sql_create_design_number(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_create_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.create_or_update(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(valid_rows)}),
            status_code=200,
            media_type="application/json",
        )
