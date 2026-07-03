"""Тесты FK-валидации TrainParserController._validate_train_rows (controllers/train_parser.py).

Покрыты только ветки, не доходящие до `text("... FROM public.models WHERE lcn::text = ...")`:
эта часть использует Postgres-специфичный оператор приведения типа `::text`, который
SQLite не умеет парсить (см. tests/conftest.py про ATTACH DATABASE ... AS public для
остальных запросов). Полный прогон happy-path с реальным разрешением car_place_id
и id_actives_parent требует настоящего Postgres (например, testcontainers) — не
покрыт этими тестами намеренно, а не по недосмотру.
"""

from controllers.train_parser import TrainParserController
from tests.conftest import make_design_number


def make_row(active="", ser="", itemnum="", lsn="", position="") -> dict:
    return {"Актив": active, "Сер": ser, "itemnum": itemnum, "lsn": lsn, "position": position}


async def validate(db_session, rows, id_type_train=1, id_train=1):
    controller = TrainParserController(owner=None)
    return await controller._validate_train_rows(db_session, rows, id_type_train, id_train)


async def test_empty_lsn_reported_without_touching_db(db_session):
    errors, valid_rows = await validate(db_session, [make_row(itemnum="DN-001", lsn="")])

    assert valid_rows == []
    assert errors[0]["field"] == "*"
    assert "Пустые lsn или itemnum" in errors[0]["message"]


async def test_empty_itemnum_reported_without_touching_db(db_session):
    errors, valid_rows = await validate(db_session, [make_row(itemnum="", lsn="5.1")])

    assert valid_rows == []
    assert errors[0]["field"] == "*"
    assert "Пустые lsn или itemnum" in errors[0]["message"]


async def test_unknown_design_number_reported(db_session):
    """dn_row is None -> continue до раw-SQL с id_car_place, поэтому SQLite тут достаточно."""
    errors, valid_rows = await validate(db_session, [make_row(itemnum="DN-999", lsn="5.1")])

    assert valid_rows == []
    assert errors[0]["field"] == "itemnum"
    assert "design_number 'DN-999' не найден" in errors[0]["message"]


async def test_multiple_rows_mixed_empty_and_unknown(db_session):
    await make_design_number(db_session, "DN-001", id_unit_type=7)

    rows = [
        make_row(itemnum="DN-001", lsn=""),  # пустой lsn -> row 1
        make_row(itemnum="DN-999", lsn="5.1"),  # design_number не найден -> row 2
    ]
    errors, valid_rows = await validate(db_session, rows)

    assert valid_rows == []
    assert [e["row"] for e in errors] == [1, 2]
    assert errors[0]["field"] == "*"
    assert errors[1]["field"] == "itemnum"
