"""FK validation tests for DesignNumberParserController (controllers/design_number_parser.py)."""

import pytest

from controllers import design_number_parser as dn_parser
from controllers.design_number_parser import DesignNumberParserController
from models import (
    CommodityCode, CommodityGroup, CounterGroup, DesignNumberStatus, MaintUse, MeasureType, ProductGroup, UnitType,
)
from sql_builders.design_number import DesignNumberUpdate
from tests.conftest import make_counter_group, make_design_number, make_unit_type


def controller() -> DesignNumberParserController:
    return DesignNumberParserController(owner=None)


# --- _validate_counter_group -------------------------------------------------

async def test_counter_group_valid_row_passes(db_session):
    dn_id = await make_design_number(db_session, "DN-001")
    cg_id = await make_counter_group(db_session, "Группа A")

    errors, valid_rows = await controller()._validate_counter_group(
        db_session, [{"number": "DN-001", "counter_group": "Группа A"}]
    )

    assert errors == []
    assert valid_rows == [(dn_id, "DN-001", cg_id)]


async def test_counter_group_match_is_case_insensitive(db_session):
    dn_id = await make_design_number(db_session, "DN-001")
    cg_id = await make_counter_group(db_session, "Группа A")

    errors, valid_rows = await controller()._validate_counter_group(
        db_session, [{"number": "DN-001", "counter_group": "группа a"}]
    )

    assert errors == []
    assert valid_rows == [(dn_id, "DN-001", cg_id)]


async def test_counter_group_empty_number_reported(db_session):
    errors, valid_rows = await controller()._validate_counter_group(
        db_session, [{"number": "", "counter_group": "Группа A"}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "number"


async def test_counter_group_unknown_design_number_reported(db_session):
    await make_counter_group(db_session, "Группа A")

    errors, valid_rows = await controller()._validate_counter_group(
        db_session, [{"number": "DN-999", "counter_group": "Группа A"}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "number"
    assert "design_number не найден" in errors[0]["message"]


async def test_counter_group_empty_name_reported(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await controller()._validate_counter_group(
        db_session, [{"number": "DN-001", "counter_group": ""}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "counter_group"


async def test_counter_group_unknown_name_reported(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await controller()._validate_counter_group(
        db_session, [{"number": "DN-001", "counter_group": "Нет такой группы"}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "counter_group"
    assert "counter_group не найден" in errors[0]["message"]


# --- _validate_unit_type ------------------------------------------------------

async def test_unit_type_valid_row_passes(db_session):
    dn_id = await make_design_number(db_session, "DN-001")
    ut_id = await make_unit_type(db_session, "Ось колесной пары")

    errors, valid_rows = await controller()._validate_unit_type(
        db_session, [{"number": "DN-001", "unit_type": "Ось колесной пары"}]
    )

    assert errors == []
    assert valid_rows == [(dn_id, "DN-001", ut_id)]


async def test_unit_type_match_is_case_insensitive(db_session):
    dn_id = await make_design_number(db_session, "DN-001")
    ut_id = await make_unit_type(db_session, "Ось колесной пары")

    errors, valid_rows = await controller()._validate_unit_type(
        db_session, [{"number": "DN-001", "unit_type": "ось колесной пары"}]
    )

    assert errors == []
    assert valid_rows == [(dn_id, "DN-001", ut_id)]


async def test_unit_type_empty_number_reported(db_session):
    errors, valid_rows = await controller()._validate_unit_type(
        db_session, [{"number": "", "unit_type": "Ось колесной пары"}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "number"


async def test_unit_type_unknown_design_number_reported(db_session):
    await make_unit_type(db_session, "Ось колесной пары")

    errors, valid_rows = await controller()._validate_unit_type(
        db_session, [{"number": "DN-999", "unit_type": "Ось колесной пары"}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "number"
    assert "design_number не найден" in errors[0]["message"]


async def test_unit_type_empty_name_reported(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await controller()._validate_unit_type(
        db_session, [{"number": "DN-001", "unit_type": ""}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "unit_type"


async def test_unit_type_unknown_name_reported(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await controller()._validate_unit_type(
        db_session, [{"number": "DN-001", "unit_type": "Нет такого типа"}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "unit_type"
    assert "unit_type не найден" in errors[0]["message"]


# --- _validate_is_serial_1c ---------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), ("да", True),
    ("false", False), ("0", False), ("нет", False),
])
async def test_is_serial_1c_accepted_values(db_session, raw, expected):
    dn_id = await make_design_number(db_session, "DN-001")

    errors, valid_rows = await controller()._validate_is_serial_1c(
        db_session, [{"number": "DN-001", "is_serial_1c": raw}]
    )

    assert errors == []
    assert valid_rows == [(dn_id, "DN-001", expected)]


async def test_is_serial_1c_invalid_value_reported(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await controller()._validate_is_serial_1c(
        db_session, [{"number": "DN-001", "is_serial_1c": "maybe"}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "is_serial_1c"


async def test_is_serial_1c_unknown_design_number_reported(db_session):
    errors, valid_rows = await controller()._validate_is_serial_1c(
        db_session, [{"number": "DN-999", "is_serial_1c": "true"}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "number"
    assert "design_number не найден" in errors[0]["message"]


async def test_is_serial_1c_empty_number_reported(db_session):
    errors, valid_rows = await controller()._validate_is_serial_1c(
        db_session, [{"number": "", "is_serial_1c": "true"}]
    )

    assert valid_rows == []
    assert errors[0]["field"] == "number"


def test_counter_is_measured_in_its_own_unit():
    assert (dn_parser._frequency_type_for(dn_parser.MOTOR_HOURS_COUNTER_TYPE_ID)
            == dn_parser.ENGINE_HOURS_FREQUENCY_TYPE_ID)
    assert dn_parser._frequency_type_for(dn_parser.MILEAGE_COUNTER_TYPE_ID) == dn_parser.KM_FREQUENCY_TYPE_ID
    # wheel and brake disc wear (counter types 4..30) is measured in millimetres
    assert dn_parser._frequency_type_for(17) == dn_parser.MM_FREQUENCY_TYPE_ID


def test_counter_type_and_frequency_type_ids_match_the_database_dictionaries():
    assert (dn_parser.MOTOR_HOURS_COUNTER_TYPE_ID, dn_parser.TIME_COUNTER_TYPE_ID,
            dn_parser.MILEAGE_COUNTER_TYPE_ID) == (1, 2, 3)
    assert (dn_parser.KM_FREQUENCY_TYPE_ID, dn_parser.ENGINE_HOURS_FREQUENCY_TYPE_ID,
            dn_parser.MM_FREQUENCY_TYPE_ID) == (5, 7, 8)


# --- validate_rename_rows ----------------------------------------------------

def rename_row(old: str, new: str) -> dict:
    return {"Старый ТМЦ": old, "Новый ТМЦ": new}


async def test_rename_valid_row_passes(db_session):
    dn_id = await make_design_number(db_session, "DN-001")

    errors, valid_rows = await dn_parser.validate_rename_rows(db_session, [rename_row("DN-001", "DN-002")])

    assert errors == []
    assert valid_rows == [(dn_id, "DN-001", "DN-002")]


async def test_rename_old_number_is_matched_exactly(db_session):
    dn_id = await make_design_number(db_session, "ИН-00000237 ")

    errors, valid_rows = await dn_parser.validate_rename_rows(
        db_session, [rename_row("ИН-00000237 ", "ИН-00000237"), rename_row("ИН-00000237", "ИН-1")]
    )

    assert valid_rows == [(dn_id, "ИН-00000237 ", "ИН-00000237")]
    assert errors == [{"row": 2, "field": "Старый ТМЦ", "message": "design_number не найден: 'ИН-00000237'"}]


async def test_rename_message_shows_invisible_characters(db_session):
    errors, _ = await dn_parser.validate_rename_rows(db_session, [rename_row("A2V_\xa0R1", "A2V_R1")])

    assert errors[0]["message"] == "design_number не найден: 'A2V_\\xa0R1'"


async def test_rename_empty_cells_reported(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await dn_parser.validate_rename_rows(
        db_session, [rename_row(None, "DN-002"), rename_row("DN-001", None)]
    )

    assert valid_rows == []
    assert errors == [
        {"row": 1, "field": "Старый ТМЦ", "message": "Поле 'Старый ТМЦ' пустое"},
        {"row": 2, "field": "Новый ТМЦ", "message": "Поле 'Новый ТМЦ' пустое"},
    ]


@pytest.mark.parametrize("new", ["DN-002 ", " DN-002", "DN-\xa0002"])
async def test_rename_new_number_with_whitespace_reported(db_session, new):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await dn_parser.validate_rename_rows(db_session, [rename_row("DN-001", new)])

    assert valid_rows == []
    assert errors == [{"row": 1, "field": "Новый ТМЦ",
                       "message": f"Новый номер содержит пробельные символы: {new!r}"}]


async def test_rename_unchanged_number_reported(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await dn_parser.validate_rename_rows(db_session, [rename_row("DN-001", "DN-001")])

    assert valid_rows == []
    assert errors == [{"row": 1, "field": "Новый ТМЦ", "message": "Номер не меняется: 'DN-001'"}]


async def test_rename_to_existing_number_reported(db_session):
    await make_design_number(db_session, "DN-001")
    taken_id = await make_design_number(db_session, "DN-002")

    errors, valid_rows = await dn_parser.validate_rename_rows(db_session, [rename_row("DN-001", "DN-002")])

    assert valid_rows == []
    assert errors == [{"row": 1, "field": "Новый ТМЦ",
                       "message": f"ТМЦ 'DN-002' уже существует (id={taken_id})"}]


async def test_rename_duplicate_old_number_reported(db_session):
    await make_design_number(db_session, "DN-001")

    errors, _ = await dn_parser.validate_rename_rows(
        db_session, [rename_row("DN-001", "DN-002"), rename_row("DN-001", "DN-003")]
    )

    assert errors == [{"row": 2, "field": "Старый ТМЦ", "message": "Старый номер 'DN-001' уже указан в строке 1"}]


async def test_rename_duplicate_new_number_reported(db_session):
    await make_design_number(db_session, "DN-001")
    await make_design_number(db_session, "DN-002")

    errors, _ = await dn_parser.validate_rename_rows(
        db_session, [rename_row("DN-001", "DN-003"), rename_row("DN-002", "DN-003")]
    )

    assert errors == [{"row": 2, "field": "Новый ТМЦ", "message": "Новый номер 'DN-003' уже указан в строке 1"}]


async def test_rename_chains_and_swaps_rejected(db_session):
    a_id = await make_design_number(db_session, "A")
    b_id = await make_design_number(db_session, "B")

    chain_errors, chain_valid = await dn_parser.validate_rename_rows(
        db_session, [rename_row("A", "B"), rename_row("B", "C")]
    )
    swap_errors, swap_valid = await dn_parser.validate_rename_rows(
        db_session, [rename_row("A", "B"), rename_row("B", "A")]
    )

    assert [old for _, old, _ in chain_valid] == ["B"]
    assert chain_errors == [{"row": 1, "field": "Новый ТМЦ", "message": f"ТМЦ 'B' уже существует (id={b_id})"}]
    assert swap_valid == []
    assert swap_errors == [
        {"row": 1, "field": "Новый ТМЦ", "message": f"ТМЦ 'B' уже существует (id={b_id})"},
        {"row": 2, "field": "Новый ТМЦ", "message": f"ТМЦ 'A' уже существует (id={a_id})"},
    ]


# --- validate_update_rows ----------------------------------------------------

async def make_ref(db_session, model, **columns) -> int:
    obj = model(**columns)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


def update_row(**overrides) -> dict:
    row = {
        "number": "DN-001", "description": "NULL", "unit_type": "NULL", "is_serial": "False",
        "is_created_1c": "NULL", "commodity_group": "NULL", "commodity_code": "NULL",
        "design_number_status": "NULL", "measure_type": "NULL", "is_repairable": "NULL",
        "product_group": "NULL", "description_1c": "NULL", "main_number": "NULL", "counter_group": "NULL",
        "maint_use": "NULL", "is_serial_1c": "False",
    }
    row.update(overrides)
    return row


async def test_update_full_row_resolves_every_column(db_session):
    dn_id = await make_design_number(db_session, "DN-001")
    refs = {
        "unit_type": await make_ref(db_session, UnitType, id=5, name="Вагон"),
        "commodity_group": await make_ref(db_session, CommodityGroup, name="SPARES"),
        "commodity_code": await make_ref(db_session, CommodityCode, name="CARBODY"),
        "status": await make_ref(db_session, DesignNumberStatus, status="ACTIVE"),
        "measure": await make_ref(db_session, MeasureType, name="шт"),
        "product_group": await make_ref(db_session, ProductGroup, name="HE"),
        "counter_group": await make_ref(db_session, CounterGroup, name="KM"),
        "cm": await make_ref(db_session, MaintUse, name="CM"),
        "tools": await make_ref(db_session, MaintUse, name="TOOLS"),
    }
    row = update_row(
        description="Ось", unit_type="Вагон", is_serial="True", is_created_1c="False", commodity_group="SPARES",
        commodity_code="CARBODY", design_number_status="ACTIVE", measure_type="шт", is_repairable="True",
        product_group="HE", description_1c="ОСЬ 1С", main_number="A2V0001", counter_group="KM",
        maint_use="CM,TOOLS", is_serial_1c="True",
    )

    errors, valid_rows = await dn_parser.validate_update_rows(db_session, [row])

    assert errors == []
    assert valid_rows == [DesignNumberUpdate(
        id=dn_id, number="DN-001", description="Ось", id_unit_type=refs["unit_type"], is_serial=True,
        is_created_1c=False, id_commodity_group=refs["commodity_group"], id_commodity_code=refs["commodity_code"],
        id_design_number_status=refs["status"], id_measure_type=refs["measure"], is_repairable=True,
        id_product_group=refs["product_group"], description_1c="ОСЬ 1С", main_number="A2V0001",
        id_counter_group=refs["counter_group"], is_serial_1c=True,
        maint_use_ids=(refs["cm"], refs["tools"]),
    )]


async def test_update_missing_headers_reported(db_session):
    await make_design_number(db_session, "DN-001")
    row = update_row()
    del row["maint_use"], row["is_serial_1c"]

    errors, valid_rows = await dn_parser.validate_update_rows(db_session, [row])

    assert valid_rows == []
    assert errors == [{"row": 0, "field": "*", "message": "В файле нет колонок: maint_use, is_serial_1c"}]


async def test_update_number_must_exist_exactly_and_once(db_session):
    dn_id = await make_design_number(db_session, "DN-001")

    errors, valid_rows = await dn_parser.validate_update_rows(db_session, [
        update_row(number="DN-001"), update_row(number="DN-001 "), update_row(number="DN-001"),
        update_row(number=None),
    ])

    assert [r.id for r in valid_rows] == [dn_id]
    assert errors == [
        {"row": 2, "field": "number", "message": "design_number не найден: 'DN-001 '"},
        {"row": 3, "field": "number", "message": "Номер 'DN-001' уже указан в строке 1"},
        {"row": 4, "field": "number", "message": "Поле 'number' пустое"},
    ]


async def test_update_references_matched_case_and_whitespace_insensitively(db_session):
    await make_design_number(db_session, "DN-001")
    wheelset = await make_ref(db_session, UnitType, id=7, name="Колесная\xa0пара")
    measure = await make_ref(db_session, MeasureType, name="шт")

    errors, valid_rows = await dn_parser.validate_update_rows(
        db_session, [update_row(unit_type=" колесная\xa0ПАРА ", measure_type="ШТ")]
    )

    assert errors == []
    assert (valid_rows[0].id_unit_type, valid_rows[0].id_measure_type) == (wheelset, measure)


async def test_update_unknown_and_ambiguous_references_reported(db_session):
    await make_design_number(db_session, "DN-001")
    await make_ref(db_session, ProductGroup, name="HE")
    await make_ref(db_session, ProductGroup, name="he")

    errors, valid_rows = await dn_parser.validate_update_rows(
        db_session, [update_row(measure_type="боб", product_group="HE", design_number_status="NEW")]
    )

    assert valid_rows == []
    assert errors == [
        {"row": 1, "field": "design_number_status", "message": "design_number_status не найден: 'NEW'"},
        {"row": 1, "field": "measure_type", "message": "measure_type не найден: 'боб'"},
        {"row": 1, "field": "product_group", "message": "product_group неоднозначен: 'HE' (записей: 2)"},
    ]


async def test_update_maint_use_list_parsed_and_unknown_item_reported(db_session):
    await make_design_number(db_session, "DN-001")
    await make_design_number(db_session, "DN-002")
    cm = await make_ref(db_session, MaintUse, name="CM")
    tools = await make_ref(db_session, MaintUse, name="TOOLS")

    errors, valid_rows = await dn_parser.validate_update_rows(db_session, [
        update_row(number="DN-001", maint_use=" tools , CM,TOOLS"),
        update_row(number="DN-002", maint_use="CM,XX"),
    ])

    assert [r.maint_use_ids for r in valid_rows] == [(tools, cm)]
    assert errors == [{"row": 2, "field": "maint_use", "message": "maint_use не найден: 'XX'"}]


async def test_update_booleans_parsed(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await dn_parser.validate_update_rows(
        db_session, [update_row(is_serial="ДА", is_created_1c="0", is_repairable=" null ", is_serial_1c="TRUE")]
    )

    assert errors == []
    row = valid_rows[0]
    assert (row.is_serial, row.is_created_1c, row.is_repairable, row.is_serial_1c) == (True, False, None, True)


async def test_update_invalid_booleans_and_null_in_not_null_reported(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await dn_parser.validate_update_rows(
        db_session, [update_row(is_serial="NULL", is_created_1c="yes", is_serial_1c="")]
    )

    assert valid_rows == []
    assert errors == [
        {"row": 1, "field": "is_serial", "message": "Поле 'is_serial' не может быть NULL"},
        {"row": 1, "field": "is_created_1c",
         "message": "Неверное значение is_created_1c: 'yes' (ожидается true/false)"},
        {"row": 1, "field": "is_serial_1c", "message": "Поле 'is_serial_1c' не может быть NULL"},
    ]


async def test_update_train_unit_type_requires_null_repairable_and_measure(db_session):
    await make_design_number(db_session, "DN-001")
    await make_design_number(db_session, "DN-002")
    await make_ref(db_session, UnitType, id=1, name="Поезд")
    await make_ref(db_session, MeasureType, name="шт")

    errors, valid_rows = await dn_parser.validate_update_rows(db_session, [
        update_row(number="DN-001", unit_type="Поезд", is_repairable="False", measure_type="шт"),
        update_row(number="DN-002", unit_type="Поезд"),
    ])

    assert [r.number for r in valid_rows] == ["DN-002"]
    assert errors == [
        {"row": 1, "field": "is_repairable", "message": "Для unit_type 'Поезд' поле 'is_repairable' должно быть NULL"},
        {"row": 1, "field": "measure_type", "message": "Для unit_type 'Поезд' поле 'measure_type' должно быть NULL"},
    ]


async def test_update_texts_trimmed_and_null_cleared(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await dn_parser.validate_update_rows(db_session, [
        update_row(description="\xa0Ось 'A' \"Б\" ", description_1c="  ", main_number=" A2V0001\xa0"),
    ])

    assert errors == []
    row = valid_rows[0]
    assert (row.description, row.description_1c, row.main_number) == ("Ось 'A' \"Б\"", None, "A2V0001")


async def test_update_native_excel_booleans_and_numbers_are_not_lost(db_session):
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await dn_parser.validate_update_rows(db_session, [
        update_row(is_serial=False, is_created_1c=True, is_repairable=0, is_serial_1c=1, main_number=12345.0,
                   description=0),
    ])

    assert errors == []
    row = valid_rows[0]
    assert (row.is_serial, row.is_created_1c, row.is_repairable, row.is_serial_1c) == (False, True, False, True)
    assert (row.main_number, row.description) == ("12345", "0")


async def test_update_duplicate_measure_type_prefers_code_1c_then_max_id(db_session):
    await make_design_number(db_session, "DN-001")
    await make_design_number(db_session, "DN-002")
    await make_design_number(db_session, "DN-003")
    with_code = await make_ref(db_session, MeasureType, id=13, name="пог. м", code_1c="18")
    await make_ref(db_session, MeasureType, id=22, name="пог. м", code_1c=None)
    await make_ref(db_session, MeasureType, id=15, name="рул", code_1c="736")
    both_coded_max = await make_ref(db_session, MeasureType, id=21, name="рул", code_1c="737")
    await make_ref(db_session, MeasureType, id=30, name="бухта", code_1c=None)
    none_coded_max = await make_ref(db_session, MeasureType, id=31, name="бухта", code_1c=None)

    errors, valid_rows = await dn_parser.validate_update_rows(db_session, [
        update_row(number="DN-001", measure_type="пог. м"),
        update_row(number="DN-002", measure_type="рул"),
        update_row(number="DN-003", measure_type="бухта"),
    ])

    assert errors == []
    assert [r.id_measure_type for r in valid_rows] == [with_code, both_coded_max, none_coded_max]
