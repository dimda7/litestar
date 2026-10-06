"""Validation tests for validate_change_hours_rows (controllers/ptoir_parser.py) and its SQL builder."""

import pytest

from controllers.ptoir_parser import validate_change_hours_rows
from sql_builders import ptoir as ptoir_sql
from tests.conftest import make_active, make_counter_active, make_ptoir


async def make_ptoir_with_hours(db_session, number_ptoir="ТО0001", active_number="UL0080571",
                                value=3678220) -> int:
    id_active = await make_active(db_session, active_number)
    await make_ptoir(db_session, number_ptoir, id_active=id_active)
    await make_counter_active(db_session, id_active, value=value, is_train=False,
                              id_counter_type=ptoir_sql.HOURS_COUNTER_TYPE_ID)
    return id_active


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def test_value_is_multiplied_by_ten(db_session):
    id_active = await make_ptoir_with_hours(db_session)

    errors, valid_rows = await validate_change_hours_rows(db_session, [{"птоир": "ТО0001", "значение": 20111}])

    assert errors == []
    assert valid_rows == [{"id_active": id_active, "value": 201110, "number_ptoir": "ТО0001",
                           "active_number": "UL0080571", "old_value": 3678220, "old_value_source": 0}]


@pytest.mark.parametrize("raw, expected", [("18635,2", 186352), ("18635.2", 186352), (18635.2, 186352),
                                           (7664, 76640), (" 0 ", 0)])
async def test_decimal_forms_are_accepted(db_session, raw, expected):
    await make_ptoir_with_hours(db_session)

    errors, valid_rows = await validate_change_hours_rows(db_session, [{"птоир": "ТО0001", "значение": raw}])

    assert errors == []
    assert valid_rows[0]["value"] == expected


@pytest.mark.parametrize("raw", ["18635,25", 18635.25, "abc", -1, True, "nan", "", None])
async def test_bad_value_reported(db_session, raw):
    await make_ptoir_with_hours(db_session)

    errors, valid_rows = await validate_change_hours_rows(db_session, [{"птоир": "ТО0001", "значение": raw}])

    assert valid_rows == []
    assert error_fields(errors) == ["значение"]


async def test_headers_are_case_insensitive(db_session):
    await make_ptoir_with_hours(db_session)

    errors, valid_rows = await validate_change_hours_rows(
        db_session, [{"ПТОиР": "ТО0001", "Актив": "UL0080571", " Значение ": 1}])

    assert errors == []
    assert valid_rows[0]["value"] == 10


@pytest.mark.parametrize("row, field", [({"значение": 1}, "птоир"), ({"птоир": "ТО0001"}, "значение")])
async def test_missing_column_reported(db_session, row, field):
    errors, valid_rows = await validate_change_hours_rows(db_session, [row])

    assert valid_rows == []
    assert errors == [{"row": 0, "field": field, "message": f"В файле не найдена колонка '{field}'"}]


async def test_missing_ptoir_reported(db_session):
    errors, valid_rows = await validate_change_hours_rows(db_session, [{"птоир": "ТО9999", "значение": 1}])

    assert valid_rows == []
    assert "ПТОиР не найден" in errors[0]["message"]


async def test_active_without_hours_counter_reported(db_session):
    id_active = await make_active(db_session, "UL0080571")
    await make_ptoir(db_session, "ТО0001", id_active=id_active)
    await make_counter_active(db_session, id_active, value=5, is_train=False, id_counter_type=3)

    errors, valid_rows = await validate_change_hours_rows(db_session, [{"птоир": "ТО0001", "значение": 1}])

    assert valid_rows == []
    assert "нет счётчика моточасов" in errors[0]["message"]


async def test_active_column_must_match_the_ptoir_active(db_session):
    await make_ptoir_with_hours(db_session)

    errors, valid_rows = await validate_change_hours_rows(
        db_session, [{"птоир": "ТО0001", "актив": "DR0260892", "значение": 1}])

    assert valid_rows == []
    assert error_fields(errors) == ["актив"]


async def test_same_ptoir_repeated_with_same_value_collapses(db_session):
    await make_ptoir_with_hours(db_session)
    row = {"птоир": "ТО0001", "значение": "20111"}

    errors, valid_rows = await validate_change_hours_rows(db_session, [row, dict(row)])

    assert errors == []
    assert len(valid_rows) == 1


async def test_same_ptoir_with_different_values_is_a_conflict(db_session):
    await make_ptoir_with_hours(db_session)

    errors, valid_rows = await validate_change_hours_rows(
        db_session, [{"птоир": "ТО0001", "значение": 1}, {"птоир": "ТО0001", "значение": 2}])

    assert len(valid_rows) == 1
    assert [e["row"] for e in errors] == [2]
    assert "Конфликт" in errors[0]["message"]


async def test_two_ptoirs_of_one_active(db_session):
    id_active = await make_ptoir_with_hours(db_session)
    await make_ptoir(db_session, "ТО0002", id_active=id_active)

    errors, valid_rows = await validate_change_hours_rows(
        db_session, [{"птоир": "ТО0001", "значение": 1}, {"птоир": "ТО0002", "значение": 1}])
    assert errors == []
    assert len(valid_rows) == 1

    errors, valid_rows = await validate_change_hours_rows(
        db_session, [{"птоир": "ТО0001", "значение": 1}, {"птоир": "ТО0002", "значение": 2}])
    assert [e["row"] for e in errors] == [2]
    assert "уже получил другое значение" in errors[0]["message"]


def test_builder_wraps_the_updates_in_replica_role():
    lines = ptoir_sql.change_hours([{"id_active": 7, "value": 201110}, {"id_active": 8, "value": 0}])

    assert lines == [
        "SET session_replication_role = replica;",
        "UPDATE public.counter_active SET value = 201110 WHERE id_active = 7 AND id_counter_type = 1;",
        "UPDATE public.counter_active SET value = 0 WHERE id_active = 8 AND id_counter_type = 1;",
        "SET session_replication_role = DEFAULT;",
    ]
