"""Validation tests for validate_change_okz / actives_sql.change_okz
(controllers/actives_parser/change_okz.py) — the 'Изменить окз' button on /actives-parser."""

from controllers.actives_parser.change_okz import validate_change_okz
from models import Location
from sql_builders import actives as actives_sql
from tests.conftest import make_active, make_car_place


async def make_located_active(db_session, active_number: str) -> int:
    location = Location()
    db_session.add(location)
    await db_session.flush()
    return await make_active(db_session, active_number, id_location=location.id)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def test_valid_row_resolves_car_place(db_session):
    await make_located_active(db_session, "ES1050060001")
    cp = await make_car_place(db_session, "+100_(01)")

    errors, valid_rows = await validate_change_okz(db_session, [{"Актив": "ES1050060001", "position": "+100_(01)"}])

    assert errors == []
    assert valid_rows == [("ES1050060001", cp)]


async def test_okz_column_accepted(db_session):
    await make_located_active(db_session, "ES1050060001")
    cp = await make_car_place(db_session, "+100_(01)")

    errors, valid_rows = await validate_change_okz(db_session, [{"Актив": "ES1050060001", "ОКЗ": "+100_(01)"}])

    assert errors == []
    assert valid_rows == [("ES1050060001", cp)]


async def test_missing_position_column_reported(db_session):
    errors, valid_rows = await validate_change_okz(db_session, [{"Актив": "ES1050060001"}])

    assert errors == [{"row": 0, "field": "position", "message": "В файле не найдена колонка 'position' (или 'окз')"}]


async def test_empty_position_skipped(db_session):
    errors, valid_rows = await validate_change_okz(db_session, [{"Актив": "ES1050060000", "position": None}])

    assert errors == []
    assert valid_rows == []


async def test_empty_active_reported(db_session):
    errors, valid_rows = await validate_change_okz(db_session, [{"Актив": "", "position": "+100_(01)"}])

    assert error_fields(errors) == ["Актив"]


async def test_unknown_active_reported(db_session):
    await make_car_place(db_session, "+100_(01)")

    errors, valid_rows = await validate_change_okz(db_session, [{"Актив": "NOPE", "position": "+100_(01)"}])

    assert error_fields(errors) == ["Актив"]
    assert "не найден" in errors[0]["message"]


async def test_unknown_car_place_reported(db_session):
    await make_located_active(db_session, "ES1050060001")

    errors, valid_rows = await validate_change_okz(db_session, [{"Актив": "ES1050060001", "position": "+999_(01)"}])

    assert error_fields(errors) == ["position"]
    assert "car_place не найден" in errors[0]["message"]


async def test_duplicate_car_place_name_reported(db_session):
    await make_located_active(db_session, "ES1050060001")
    await make_car_place(db_session, "+100_(01)")
    await make_car_place(db_session, "+100_(01)")

    errors, valid_rows = await validate_change_okz(db_session, [{"Актив": "ES1050060001", "position": "+100_(01)"}])

    assert error_fields(errors) == ["position"]
    assert "неоднозначен" in errors[0]["message"]


async def test_repeated_active_with_same_position_deduplicated(db_session):
    await make_located_active(db_session, "ES1050060001")
    cp = await make_car_place(db_session, "+100_(01)")
    row = {"Актив": "ES1050060001", "position": "+100_(01)"}

    errors, valid_rows = await validate_change_okz(db_session, [row, row])

    assert errors == []
    assert valid_rows == [("ES1050060001", cp)]


async def test_repeated_active_with_other_position_reported(db_session):
    await make_located_active(db_session, "ES1050060001")
    await make_car_place(db_session, "+100_(01)")
    await make_car_place(db_session, "+101_(01)")

    errors, valid_rows = await validate_change_okz(db_session, [
        {"Актив": "ES1050060001", "position": "+100_(01)"},
        {"Актив": "ES1050060001", "position": "+101_(01)"},
    ])

    assert [e["row"] for e in errors] == [2]
    assert "Конфликт" in errors[0]["message"]


def test_sql_escapes_active_number():
    sql = actives_sql.change_okz([("A'1", 5)])

    assert sql == [
        "UPDATE public.location AS loc SET id_car_place = v.id_car_place "
        "FROM public.actives AS act, (VALUES ('A''1', 5)) AS v(active_number, id_car_place) "
        "WHERE act.id_location = loc.id AND act.active_number = v.active_number;"
    ]


def test_sql_empty_for_no_rows():
    assert actives_sql.change_okz([]) == []
