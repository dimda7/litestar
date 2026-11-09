"""Validation tests for validate_delete_main_rows (controllers/ptoir_parser/delete_main.py)."""

from controllers.ptoir_parser.delete_main import validate_delete_main_rows
from tests.conftest import make_main_ptoir, make_order, make_ptoir


async def test_found_main_ptoir_comes_with_its_ptoir_and_their_orders(db_session):
    id_main = await make_main_ptoir(db_session, "VR_SPEC_0093_odnoraz")
    first = await make_ptoir(db_session, "ТО0001", id_main_ptoir=id_main)
    second = await make_ptoir(db_session, "ТО0002", id_main_ptoir=id_main)
    await make_ptoir(db_session, "ТО0003", id_main_ptoir=await make_main_ptoir(db_session, "OTHER"))
    await make_order(db_session, "ЗН1", id_ptoir=first)

    errors, valid_rows = await validate_delete_main_rows(db_session, [{"БПТОиР": "VR_SPEC_0093_odnoraz"}])

    assert errors == []
    assert valid_rows == [{"id": id_main, "number_ptoir": "VR_SPEC_0093_odnoraz", "ptoirs": [
        {"id": first, "number_ptoir": "ТО0001", "order_numbers": ["ЗН1"]},
        {"id": second, "number_ptoir": "ТО0002", "order_numbers": []},
    ]}]


async def test_missing_main_ptoir_is_a_row_error_and_other_rows_stay_valid(db_session):
    id_main = await make_main_ptoir(db_session, "VR_SPEC_0002")

    errors, valid_rows = await validate_delete_main_rows(db_session, [{"БПТОиР": "NOPE"}, {"БПТОиР": " VR_SPEC_0002 "}])

    assert errors == [{"row": 1, "field": "БПТОиР", "message": "БПТОиР не найден: 'NOPE'"}]
    assert valid_rows == [{"id": id_main, "number_ptoir": "VR_SPEC_0002", "ptoirs": []}]


async def test_empty_cell_is_a_row_error(db_session):
    await make_main_ptoir(db_session, "VR_SPEC_0001")

    errors, valid_rows = await validate_delete_main_rows(db_session, [{"БПТОиР": "VR_SPEC_0001"}, {"БПТОиР": None}])

    assert errors == [{"row": 2, "field": "БПТОиР", "message": "Поле 'БПТОиР' пустое"}]
    assert len(valid_rows) == 1


async def test_repeated_number_is_taken_once_without_error(db_session):
    await make_main_ptoir(db_session, "VR_SPEC_0001")

    errors, valid_rows = await validate_delete_main_rows(
        db_session, [{"БПТОиР": "VR_SPEC_0001"}, {"БПТОиР": "VR_SPEC_0001"}])

    assert errors == []
    assert len(valid_rows) == 1


async def test_header_is_case_insensitive(db_session):
    await make_main_ptoir(db_session, "VR_SPEC_0001")

    errors, valid_rows = await validate_delete_main_rows(db_session, [{" бптоир ": "VR_SPEC_0001"}])

    assert errors == []
    assert len(valid_rows) == 1


async def test_missing_column_blocks_the_file(db_session):
    errors, valid_rows = await validate_delete_main_rows(db_session, [{"ПТОиР": "ТО0001"}])

    assert valid_rows == []
    assert errors == [{"row": 0, "field": "БПТОиР", "message": "В файле не найдена колонка 'БПТОиР'"}]
