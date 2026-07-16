"""Тесты валидации PtoirParserController._validate_and_build_rows (controllers/ptoir_parser.py)."""

from datetime import datetime

from controllers.ptoir_parser import PtoirParserController
from tests.conftest import make_active, make_counter_type, make_ptoir, make_ptoir_level_warning


def make_row(number_ptoir="ТО0001", active="", counter_type="Пробег", interval=2,
             date_activation=None, service_value=1000) -> dict:
    return {
        "ПТОиР": number_ptoir,
        "Актив": active,
        "Тип счетчика": counter_type,
        "Интервал": interval,
        "Дата активации": date_activation or datetime(2026, 7, 13),
        "Данные последнего обслуживания": service_value,
    }


async def validate(db_session, rows):
    controller = PtoirParserController(owner=None)
    return await controller._validate_and_build_rows(db_session, rows)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def test_valid_mileage_row_passes(db_session):
    ct_id = await make_counter_type(db_session, "Пробег")
    ptoir_id = await make_ptoir(db_session, "ТО0001")
    lw_id = await make_ptoir_level_warning(db_session, ptoir_id, ct_id)

    errors, valid_rows = await validate(db_session, [make_row(service_value=7362771)])

    assert errors == []
    assert valid_rows == [(ptoir_id, datetime(2026, 7, 12, 21, 0), 2, lw_id, 7362771)]


async def test_missing_ptoir_reported(db_session):
    await make_counter_type(db_session, "Пробег")

    errors, valid_rows = await validate(db_session, [make_row(number_ptoir="ТО9999")])

    assert valid_rows == []
    assert error_fields(errors) == ["ПТОиР"]
    assert "ПТОиР не найден" in errors[0]["message"]


async def test_missing_counter_type_reported(db_session):
    ptoir_id = await make_ptoir(db_session, "ТО0001")

    errors, valid_rows = await validate(db_session, [make_row(counter_type="Неизвестный")])

    assert valid_rows == []
    assert error_fields(errors) == ["Тип счетчика"]
    assert "Тип счетчика не найден" in errors[0]["message"]


async def test_missing_level_warning_reported(db_session):
    ct_id = await make_counter_type(db_session, "Пробег")
    await make_ptoir(db_session, "ТО0001")
    # ptoir_level_warning для этого ПТОиР и типа счетчика не создан

    errors, valid_rows = await validate(db_session, [make_row()])

    assert valid_rows == []
    assert error_fields(errors) == ["*"]
    assert "Уровень предупреждения не найден" in errors[0]["message"]


async def test_active_mismatch_reported(db_session):
    ct_id = await make_counter_type(db_session, "Пробег")
    active_id = await make_active(db_session, "SPV000001")
    other_active_id = await make_active(db_session, "SPV000002")
    ptoir_id = await make_ptoir(db_session, "ТО0001", id_active=other_active_id)
    await make_ptoir_level_warning(db_session, ptoir_id, ct_id)

    errors, valid_rows = await validate(db_session, [make_row(active="SPV000001")])

    assert valid_rows == []
    assert error_fields(errors) == ["Актив"]
    assert "не соответствует" in errors[0]["message"]


async def test_unknown_active_reported(db_session):
    ct_id = await make_counter_type(db_session, "Пробег")
    ptoir_id = await make_ptoir(db_session, "ТО0001")
    await make_ptoir_level_warning(db_session, ptoir_id, ct_id)

    errors, valid_rows = await validate(db_session, [make_row(active="НЕТ_ТАКОГО")])

    assert valid_rows == []
    assert error_fields(errors) == ["Актив"]
    assert "Актив не найден" in errors[0]["message"]


async def test_invalid_interval_reported(db_session):
    ct_id = await make_counter_type(db_session, "Пробег")
    ptoir_id = await make_ptoir(db_session, "ТО0001")
    await make_ptoir_level_warning(db_session, ptoir_id, ct_id)

    errors, valid_rows = await validate(db_session, [make_row(interval="не число")])

    assert valid_rows == []
    assert error_fields(errors) == ["Интервал"]
    assert "Некорректный интервал" in errors[0]["message"]


async def test_time_based_service_value_parsed_as_epoch(db_session):
    ct_id = await make_counter_type(db_session, "Время")
    ptoir_id = await make_ptoir(db_session, "ТО0001")
    lw_id = await make_ptoir_level_warning(db_session, ptoir_id, ct_id)

    errors, valid_rows = await validate(
        db_session, [make_row(counter_type="Время", service_value="01.01.2026 00:00:00")]
    )

    assert errors == []
    assert len(valid_rows) == 1
    assert valid_rows[0][0] == ptoir_id
    assert valid_rows[0][3] == lw_id
    assert isinstance(valid_rows[0][4], int)
