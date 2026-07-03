"""Тесты чистых функций разбора LSN/position в controllers/train_parser.py."""

from controllers.train_parser import (
    _lcn_to_model,
    _lcn_to_lcn,
    _lcn_to_prelcn,
    _parse_car_number,
)


def test_lcn_to_model_root_segment():
    assert _lcn_to_model("5", id_train_type=1) == "M1"


def test_lcn_to_model_nested_segment():
    assert _lcn_to_model("5.2.3", id_train_type=1) == "M1.2.3"


def test_lcn_to_lcn_root_segment():
    assert _lcn_to_lcn("5", id_train=42) == "42"


def test_lcn_to_lcn_nested_segment():
    assert _lcn_to_lcn("5.2.3", id_train=42) == "42.2.3"


def test_lcn_to_prelcn_root_has_no_parent():
    assert _lcn_to_prelcn("5") == ""


def test_lcn_to_prelcn_one_level():
    assert _lcn_to_prelcn("5.2") == "5"


def test_lcn_to_prelcn_multi_level_drops_last_segment_only():
    assert _lcn_to_prelcn("5.2.3") == "5.2"


def test_parse_car_number_extracts_digits():
    assert _parse_car_number("+100_(01)") == 1


def test_parse_car_number_multi_digit():
    assert _parse_car_number("+100_(12)") == 12


def test_parse_car_number_empty_position_returns_none():
    assert _parse_car_number("") is None


def test_parse_car_number_no_match_returns_none():
    assert _parse_car_number("+100") is None
