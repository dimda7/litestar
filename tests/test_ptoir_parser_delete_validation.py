"""Validation tests for validate_delete_rows (controllers/ptoir_parser.py)."""

from controllers.ptoir_parser import validate_delete_rows
from tests.conftest import make_order, make_ptoir


async def test_found_ptoir_is_valid_with_its_orders(db_session):
    id_ptoir = await make_ptoir(db_session, "ТО0001")
    await make_order(db_session, "ЗН1", id_ptoir=id_ptoir)
    await make_order(db_session, "ЗН2", id_ptoir=id_ptoir)
    await make_order(db_session, "ЗН3")

    errors, valid_rows = await validate_delete_rows(db_session, [{"ПТОиР": "ТО0001"}])

    assert errors == []
    assert valid_rows == [{"id": id_ptoir, "number_ptoir": "ТО0001", "order_numbers": ["ЗН1", "ЗН2"]}]


async def test_missing_ptoir_is_a_row_error_and_other_rows_stay_valid(db_session):
    id_ptoir = await make_ptoir(db_session, "ТО0002")

    errors, valid_rows = await validate_delete_rows(db_session, [{"ПТОиР": "ТО9999"}, {"ПТОиР": " ТО0002 "}])

    assert errors == [{"row": 1, "field": "ПТОиР", "message": "ПТОиР не найден: 'ТО9999'"}]
    assert valid_rows == [{"id": id_ptoir, "number_ptoir": "ТО0002", "order_numbers": []}]


async def test_empty_cell_is_a_row_error(db_session):
    await make_ptoir(db_session, "ТО0001")

    errors, valid_rows = await validate_delete_rows(db_session, [{"ПТОиР": "ТО0001"}, {"ПТОиР": None}])

    assert errors == [{"row": 2, "field": "ПТОиР", "message": "Поле 'ПТОиР' пустое"}]
    assert len(valid_rows) == 1


async def test_repeated_number_is_taken_once_without_error(db_session):
    await make_ptoir(db_session, "ТО0001")

    errors, valid_rows = await validate_delete_rows(db_session, [{"ПТОиР": "ТО0001"}, {"ПТОиР": "ТО0001"}])

    assert errors == []
    assert len(valid_rows) == 1


async def test_header_is_case_insensitive(db_session):
    await make_ptoir(db_session, "ТО0001")

    errors, valid_rows = await validate_delete_rows(db_session, [{" птоир ": "ТО0001"}])

    assert errors == []
    assert len(valid_rows) == 1


async def test_missing_column_blocks_the_file(db_session):
    errors, valid_rows = await validate_delete_rows(db_session, [{"Номер": "ТО0001"}])

    assert valid_rows == []
    assert errors == [{"row": 0, "field": "ПТОиР", "message": "В файле не найдена колонка 'ПТОиР'"}]
