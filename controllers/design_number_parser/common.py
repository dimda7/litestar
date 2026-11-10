import logging
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

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
from sql_builders import design_number as design_number_sql

logger = logging.getLogger("design_number_parser")


PREFIX = "dn_parser"
PATH = "/design-number-parser"


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
