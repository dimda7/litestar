"""Validation tests for OrderParserController._validate_and_build_rows (controllers/order_parser.py)."""

from controllers.order_parser import OrderParserController
from tests.conftest import make_order


def make_row(child="ЗН000783610", parent="ЗН000464234") -> dict:
    return {"Заказ-наряд": child, "Родительский ЗН": parent}


async def validate(db_session, rows):
    controller = OrderParserController(owner=None)
    return await controller._validate_and_build_rows(db_session, rows)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def test_valid_row_passes(db_session):
    child_id = await make_order(db_session, "ЗН000783610")
    parent_id = await make_order(db_session, "ЗН000464234")

    errors, valid_rows = await validate(db_session, [make_row()])

    assert errors == []
    assert valid_rows == [(child_id, parent_id, "ЗН000783610", "ЗН000464234")]


async def test_column_missing_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"Актив": "x"}])

    assert valid_rows == []
    assert len(errors) == 1
    assert "не найдена колонка" in errors[0]["message"]


async def test_empty_child_reported(db_session):
    await make_order(db_session, "ЗН000464234")

    errors, valid_rows = await validate(db_session, [make_row(child="")])

    assert valid_rows == []
    assert error_fields(errors) == ["Заказ-наряд"]
    assert "пустое" in errors[0]["message"]


async def test_empty_parent_reported(db_session):
    await make_order(db_session, "ЗН000783610")

    errors, valid_rows = await validate(db_session, [make_row(parent="")])

    assert valid_rows == []
    assert error_fields(errors) == ["Родительский ЗН"]
    assert "пустое" in errors[0]["message"]


async def test_self_parent_reported(db_session):
    await make_order(db_session, "ЗН000783610")

    errors, valid_rows = await validate(db_session, [make_row(child="ЗН000783610", parent="ЗН000783610")])

    assert valid_rows == []
    assert error_fields(errors) == ["Родительский ЗН"]
    assert "самому себе" in errors[0]["message"]


async def test_duplicate_child_in_file_reported(db_session):
    await make_order(db_session, "ЗН000783610")
    await make_order(db_session, "ЗН000464234")
    await make_order(db_session, "ЗН000999999")

    errors, valid_rows = await validate(db_session, [make_row(), make_row(parent="ЗН000999999")])

    assert len(valid_rows) == 1
    assert error_fields(errors) == ["Заказ-наряд"]
    assert "Дубликат" in errors[0]["message"]


async def test_unknown_child_reported(db_session):
    await make_order(db_session, "ЗН000464234")

    errors, valid_rows = await validate(db_session, [make_row(child="НЕТ_ТАКОГО")])

    assert valid_rows == []
    assert error_fields(errors) == ["Заказ-наряд"]
    assert "не найден" in errors[0]["message"]


async def test_unknown_parent_reported(db_session):
    await make_order(db_session, "ЗН000783610")

    errors, valid_rows = await validate(db_session, [make_row(parent="НЕТ_ТАКОГО")])

    assert valid_rows == []
    assert error_fields(errors) == ["Родительский ЗН"]
    assert "не найден" in errors[0]["message"]


def test_sql_body_upsert():
    sql_lines = OrderParserController._build_sql_lines([(1, 2, "ЗН000783610", "ЗН000464234")])

    assert len(sql_lines) == 1
    assert "INSERT INTO public.order_to_order (id_parent, id_child) VALUES (2, 1)" in sql_lines[0]
    assert "ON CONFLICT (id_child) DO UPDATE SET id_parent = EXCLUDED.id_parent" in sql_lines[0]
