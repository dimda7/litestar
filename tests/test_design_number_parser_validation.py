"""FK validation tests for DesignNumberParserController (controllers/design_number_parser.py)."""

import pytest

from controllers import design_number_parser as dn_parser
from controllers.design_number_parser import DesignNumberParserController
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
