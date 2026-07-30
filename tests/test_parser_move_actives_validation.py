"""Тесты валидации ParserController._validate_and_build_move_rows / _build_move_actives_sql_body (controllers/parser.py)."""

from datetime import datetime

from controllers.parser import ParserController
from tests.conftest import make_active, make_consignment, make_storage, make_user


DEFAULT_REASON = "Убран десятый (лишний) межвагонный переход с 6 вагона"
DEFAULT_USER_FULLNAME = "Велебская Александра Владимировна"
DEFAULT_STORAGE = "Виртуальный склад"
DEFAULT_CONSIGNMENT = "ЧСП ЛОМ"


async def validate(db_session, rows, storage_name=DEFAULT_STORAGE, consignment_name=DEFAULT_CONSIGNMENT, user_fullname=DEFAULT_USER_FULLNAME):
    controller = ParserController(owner=None)
    return await controller._validate_and_build_move_rows(db_session, rows, storage_name, consignment_name, user_fullname)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def setup_defaults(db_session):
    storage_id = await make_storage(db_session, DEFAULT_STORAGE, last_lcn=100)
    consignment_id = await make_consignment(db_session, DEFAULT_CONSIGNMENT)
    user_id = await make_user(db_session)
    return storage_id, consignment_id, user_id


async def test_valid_row_resolves_everything(db_session):
    storage_id, consignment_id, user_id = await setup_defaults(db_session)
    active_id = await make_active(db_session, "SPV000001", id_location=42)

    errors, valid_rows, id_storage, id_consignment, id_user = await validate(db_session, [{"Актив": "SPV000001"}])

    assert errors == []
    assert id_storage == storage_id
    assert id_consignment == consignment_id
    assert id_user == user_id
    assert valid_rows == [{
        "row": 1, "active_number": "SPV000001", "id_active": active_id, "id_location_old": 42,
    }]


async def test_active_number_column_alias(db_session):
    await setup_defaults(db_session)
    await make_active(db_session, "SPV000002")

    errors, valid_rows, *_ = await validate(db_session, [{"active_number": "SPV000002"}])

    assert errors == []
    assert valid_rows[0]["active_number"] == "SPV000002"


async def test_storage_not_found_reported(db_session):
    await make_consignment(db_session, DEFAULT_CONSIGNMENT)
    await make_user(db_session)

    errors, valid_rows, id_storage, id_consignment, id_user = await validate(db_session, [{"Актив": "X"}], storage_name="Нет такого склада")

    assert error_fields(errors) == ["Склад"]
    assert id_storage is None
    assert valid_rows == []


async def test_consignment_not_found_reported(db_session):
    await make_storage(db_session, DEFAULT_STORAGE)
    await make_user(db_session)

    errors, valid_rows, *_ = await validate(db_session, [{"Актив": "X"}], consignment_name="Нет такой партии")

    assert error_fields(errors) == ["Партия"]


async def test_user_not_found_reported(db_session):
    await make_storage(db_session, DEFAULT_STORAGE)
    await make_consignment(db_session, DEFAULT_CONSIGNMENT)

    errors, valid_rows, *_ = await validate(db_session, [{"Актив": "X"}], user_fullname="Несуществующий Пользователь Иванович")

    assert error_fields(errors) == ["Пользователь"]


async def test_user_resolved_without_middlename(db_session):
    await make_storage(db_session, DEFAULT_STORAGE)
    await make_consignment(db_session, DEFAULT_CONSIGNMENT)
    user_id = await make_user(db_session, lastname="Иванов", firstname="Иван", middlename=None)
    await make_active(db_session, "SPV000003")

    errors, valid_rows, _, _, id_user = await validate(
        db_session, [{"Актив": "SPV000003"}], user_fullname="Иванов Иван",
    )

    assert errors == []
    assert id_user == user_id


async def test_missing_active_column_reported(db_session):
    await setup_defaults(db_session)

    errors, valid_rows, *_ = await validate(db_session, [{"other": "x"}])

    assert errors == [{"row": 0, "field": "Актив", "message": "В файле не найдена колонка 'Актив' (или 'active_number')"}]


async def test_active_not_found_reported(db_session):
    await setup_defaults(db_session)

    errors, valid_rows, *_ = await validate(db_session, [{"Актив": "MISSING"}])

    assert error_fields(errors) == ["Актив"]
    assert "не найден" in errors[0]["message"]


async def test_duplicate_active_in_file_reported(db_session):
    await setup_defaults(db_session)
    await make_active(db_session, "SPV000004")

    errors, valid_rows, *_ = await validate(db_session, [{"Актив": "SPV000004"}, {"Актив": "SPV000004"}])

    assert error_fields(errors) == ["Актив"]
    assert "Дубликат" in errors[0]["message"]
    assert len(valid_rows) == 1


async def test_empty_active_reported(db_session):
    await setup_defaults(db_session)

    errors, valid_rows, *_ = await validate(db_session, [{"Актив": ""}])

    assert error_fields(errors) == ["Актив"]


def test_build_sql_body_single_row():
    valid_rows = [{"id_active": 10, "id_location_old": 5}]
    sql_lines = ParserController._build_move_actives_sql_body(
        valid_rows, id_storage=7, id_consignment=3, id_user=2,
        reason=DEFAULT_REASON, move_date=datetime(2026, 1, 1, 12, 0, 0),
    )
    sql = "\n".join(sql_lines)

    assert "DO $$" in sql
    assert "FROM generate_series(1, 1)" in sql
    assert "SELECT last_lcn INTO lcn_new FROM public.storage WHERE id = 7 FOR UPDATE;" in sql
    assert "INSERT INTO public.location (id, id_type_location, id_storage, id_consignment) VALUES (loc_ids[1], 1, 7, 3);" in sql
    assert "INSERT INTO public.relocate" in sql
    assert "VALUES (5, loc_ids[1], '2026-01-01 12:00:00', 2, 10, 'Убран десятый (лишний) межвагонный переход с 6 вагона', '2026-01-01 12:00:00', NULL);" in sql
    assert "UPDATE public.actives SET id_location = loc_ids[1], id_actves_parent = NULL, id_actives_root = NULL, lcn = ('S7.' || lcn_new)::ltree WHERE id = 10;" in sql
    assert "UPDATE public.storage SET last_lcn = lcn_new WHERE id = 7;" in sql


def test_build_sql_body_null_old_location():
    valid_rows = [{"id_active": 10, "id_location_old": None}]
    sql_lines = ParserController._build_move_actives_sql_body(
        valid_rows, id_storage=7, id_consignment=3, id_user=2,
        reason="", move_date=datetime(2026, 1, 1, 12, 0, 0),
    )
    sql = "\n".join(sql_lines)

    assert "VALUES (NULL, loc_ids[1]" in sql
    assert ", NULL, '2026-01-01 12:00:00', NULL);" in sql  # reason=NULL when empty
