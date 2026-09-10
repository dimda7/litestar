"""Tests for sql_builders/car_place.py — the car places 'Изменить okz в модели' and
'Добавить строку в модель' create when a file names one that does not exist yet."""

from sql_builders import car_place as car_place_sql


def test_parse_car_number_extracts_digits():
    assert car_place_sql.parse_car_number("+100_(01)") == 1


def test_parse_car_number_multi_digit():
    assert car_place_sql.parse_car_number("+100_(12)") == 12


def test_parse_car_number_with_dashed_position():
    assert car_place_sql.parse_car_number("+106.20-01_(01)") == 1


def test_parse_car_number_empty_position_returns_none():
    assert car_place_sql.parse_car_number("") is None


def test_parse_car_number_no_match_returns_none():
    assert car_place_sql.parse_car_number("+100") is None
