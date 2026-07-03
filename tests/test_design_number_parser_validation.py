"""Тесты FK-валидации DesignNumberParserController (controllers/design_number_parser.py)."""

import pytest

from controllers.design_number_parser import DesignNumberParserController
from tests.conftest import make_counter_group, make_design_number


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
