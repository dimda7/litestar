"""Validation tests for ParserController._validate_and_build_serial_none_rows and _parse_model_lcn (controllers/parser.py)."""

from controllers.parser import ParserController, _parse_model_lcn
from tests.conftest import make_train


async def validate(db_session, rows):
    controller = ParserController(owner=None)
    return await controller._validate_and_build_serial_none_rows(db_session, rows)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


def test_parse_model_lcn_with_letter_prefix_and_path():
    assert _parse_model_lcn("M9.6.5") == (9, "6.5")


def test_parse_model_lcn_with_letter_prefix_no_path():
    assert _parse_model_lcn("M9") == (9, "")


def test_parse_model_lcn_without_letter_prefix():
    assert _parse_model_lcn("9.6.5") == (9, "6.5")


def test_parse_model_lcn_unparseable():
    assert _parse_model_lcn("abc") is None


async def test_valid_row_lists_all_matching_trains(db_session):
    train_a = await make_train(db_session, id_train_type=9, name="Поезд A")
    train_b = await make_train(db_session, id_train_type=9, name="Поезд B")

    errors, valid_rows = await validate(db_session, [{"lsn": "M9.6.5"}])

    assert errors == []
    assert len(valid_rows) == 1
    assert valid_rows[0]["id_train_type"] == 9
    assert sorted(valid_rows[0]["lcn_trains"]) == sorted([f"{train_a}.6.5", f"{train_b}.6.5"])


async def test_valid_row_accepts_lcn_column_alias(db_session):
    train_id = await make_train(db_session, id_train_type=9)

    errors, valid_rows = await validate(db_session, [{"lcn": "M9.6.5"}])

    assert errors == []
    assert valid_rows[0]["lcn_trains"] == [f"{train_id}.6.5"]


async def test_no_path_segment_lcn_maps_to_bare_train_id(db_session):
    train_id = await make_train(db_session, id_train_type=9)

    errors, valid_rows = await validate(db_session, [{"lsn": "M9"}])

    assert errors == []
    assert valid_rows[0]["lcn_trains"] == [str(train_id)]


async def test_empty_lcn_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"lsn": ""}])
    assert error_fields(errors) == ["lcn"]
    assert valid_rows == []


async def test_unparseable_lcn_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"lsn": "abc"}])
    assert error_fields(errors) == ["lcn"]
    assert "распознать" in errors[0]["message"]


async def test_no_trains_for_train_type_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"lsn": "M9.6.5"}])
    assert error_fields(errors) == ["lcn"]
    assert "не найдены" in errors[0]["message"]
    assert valid_rows == []


async def test_missing_lcn_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"other": "x"}])
    assert errors == [{"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"}]
    assert valid_rows == []


async def test_multiple_rows_share_train_type_lookup_cache(db_session):
    train_id = await make_train(db_session, id_train_type=9)

    errors, valid_rows = await validate(db_session, [{"lsn": "M9.6.5"}, {"lsn": "M9.7.1"}])

    assert errors == []
    assert len(valid_rows) == 2
    assert valid_rows[0]["lcn_trains"] == [f"{train_id}.6.5"]
    assert valid_rows[1]["lcn_trains"] == [f"{train_id}.7.1"]


def test_duplicate_lsn_rows_merge_into_single_update():
    valid_rows = [
        {"lcn_trains": ["281.6.5", "275.6.5", "278.6.5"]},
        {"lcn_trains": ["281.6.5", "275.6.5", "278.6.5"]},
    ]

    sql_lines = ParserController._build_serial_none_sql_lines(valid_rows)

    assert len(sql_lines) == 1
    assert sql_lines[0].count("UPDATE public.actives") == 1
    assert sql_lines[0] == (
        "UPDATE public.actives SET serial_number = 'none' "
        "WHERE lcn::text IN ('281.6.5', '275.6.5', '278.6.5');"
    )


def test_overlapping_lsn_rows_merge_without_duplicate_lcns():
    valid_rows = [
        {"lcn_trains": ["1.6.5", "2.6.5"]},
        {"lcn_trains": ["2.6.5", "3.6.5"]},
    ]

    merged = ParserController._merge_serial_none_lcns(valid_rows)

    assert merged == ["1.6.5", "2.6.5", "3.6.5"]
