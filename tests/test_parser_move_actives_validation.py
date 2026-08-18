"""Validation tests for ParserController._validate_and_build_move_rows / _build_move_actives_sql_body (controllers/parser.py).

Resolving assets by lcn ('SELECT ... WHERE lcn::text IN :lcns') uses the
Postgres-specific `::text` cast on an ltree column — SQLite (the test DB) does
not parse that syntax at all (a syntax error, not merely "0 rows"). So what is
covered here is everything happening BEFORE that query (resolving
storage/consignment/user, parsing lsn via _validate_and_build_serial_none_rows,
building the SQL body) — same as for the equivalent happy path in
train_parser.py (see checkpoint.md, phase 9). The query itself was verified by
hand against the real grom-tk database.
"""

from datetime import datetime

from controllers.parser import ParserController
from tests.conftest import make_consignment, make_design_number, make_storage, make_user


DEFAULT_REASON = "Убран десятый (лишний) межвагонный переход с 6 вагона"
DEFAULT_USER_FULLNAME = "Велебская Александра Владимировна"
DEFAULT_STORAGE = "Виртуальный склад"
DEFAULT_CONSIGNMENT = "ЧСП ЛОМ"


async def validate(
    db_session, rows, storage_name=DEFAULT_STORAGE, consignment_name=DEFAULT_CONSIGNMENT,
    user_fullname=DEFAULT_USER_FULLNAME, set_nocm=False,
):
    controller = ParserController(owner=None)
    return await controller._validate_and_build_move_rows(db_session, rows, storage_name, consignment_name, user_fullname, set_nocm)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def setup_defaults(db_session):
    storage_id = await make_storage(db_session, DEFAULT_STORAGE, last_lcn=100)
    consignment_id = await make_consignment(db_session, DEFAULT_CONSIGNMENT)
    user_id = await make_user(db_session)
    return storage_id, consignment_id, user_id


async def test_storage_not_found_reported(db_session):
    await make_consignment(db_session, DEFAULT_CONSIGNMENT)
    await make_user(db_session)

    errors, valid_rows, id_storage, id_consignment, id_user, id_design_number = await validate(db_session, [{"lsn": "M9.6.5"}], storage_name="Нет такого склада")

    assert error_fields(errors) == ["Склад"]
    assert id_storage is None
    assert valid_rows == []


async def test_consignment_not_found_reported(db_session):
    await make_storage(db_session, DEFAULT_STORAGE)
    await make_user(db_session)

    errors, valid_rows, *_ = await validate(db_session, [{"lsn": "M9.6.5"}], consignment_name="Нет такой партии")

    assert error_fields(errors) == ["Партия"]


async def test_user_not_found_reported(db_session):
    await make_storage(db_session, DEFAULT_STORAGE)
    await make_consignment(db_session, DEFAULT_CONSIGNMENT)

    errors, valid_rows, *_ = await validate(db_session, [{"lsn": "M9.6.5"}], user_fullname="Несуществующий Пользователь Иванович")

    assert error_fields(errors) == ["Пользователь"]


async def test_resolve_user_by_fullname_with_middlename(db_session):
    user_id = await make_user(db_session, lastname="Велебская", firstname="Александра", middlename="Владимировна")

    controller = ParserController(owner=None)
    resolved = await controller._resolve_user_by_fullname(db_session, DEFAULT_USER_FULLNAME)

    assert resolved == user_id


async def test_resolve_user_by_fullname_without_middlename(db_session):
    user_id = await make_user(db_session, lastname="Иванов", firstname="Иван", middlename=None)

    controller = ParserController(owner=None)
    resolved = await controller._resolve_user_by_fullname(db_session, "Иванов Иван")

    assert resolved == user_id


async def test_resolve_user_by_fullname_not_found(db_session):
    await make_user(db_session)

    controller = ParserController(owner=None)
    resolved = await controller._resolve_user_by_fullname(db_session, "Несуществующий Пользователь")

    assert resolved is None


async def test_missing_lcn_column_reported(db_session):
    await setup_defaults(db_session)

    errors, valid_rows, *_ = await validate(db_session, [{"other": "x"}])

    assert errors == [{"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"}]


async def test_unparseable_lcn_reported(db_session):
    await setup_defaults(db_session)

    errors, valid_rows, *_ = await validate(db_session, [{"lsn": "abc"}])

    assert error_fields(errors) == ["lcn"]


async def test_no_trains_for_train_type_reported(db_session):
    await setup_defaults(db_session)

    errors, valid_rows, *_ = await validate(db_session, [{"lsn": "M9.6.5"}])

    assert error_fields(errors) == ["lcn"]
    assert "не найдены" in errors[0]["message"]


async def test_set_nocm_resolves_design_number(db_session):
    await setup_defaults(db_session)
    dn_id = await make_design_number(db_session, "NOCM")

    errors, valid_rows, _, _, _, id_design_number = await validate(db_session, [{"lsn": "M9.6.5"}], set_nocm=True)

    assert error_fields(errors) == ["lcn"]  # no trains of this type — but NOCM did resolve
    assert id_design_number == dn_id


async def test_set_nocm_not_found_reported(db_session):
    await setup_defaults(db_session)

    errors, valid_rows, _, _, _, id_design_number = await validate(db_session, [{"lsn": "M9.6.5"}], set_nocm=True)

    assert error_fields(errors) == ["ТМЦ"]
    assert "NOCM" in errors[0]["message"]
    assert id_design_number is None


async def test_set_nocm_false_skips_lookup(db_session):
    await setup_defaults(db_session)

    errors, valid_rows, _, _, _, id_design_number = await validate(db_session, [{"lsn": "M9.6.5"}], set_nocm=False)

    assert id_design_number is None
    assert error_fields(errors) == ["lcn"]  # NOCM did not resolve, but did not interfere either — the error is only about trains


def test_build_sql_body_single_row():
    valid_rows = [{"id_active": 10, "id_location_old": 5, "active_number": "ULP0090952"}]
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
    assert "UPDATE public.actives SET id_location = loc_ids[1], id_actves_parent = NULL, id_actives_root = NULL, lcn = ('S7.' || lcn_new)::ltree WHERE id = 10; -- ULP0090952" in sql
    assert "UPDATE public.storage SET last_lcn = lcn_new WHERE id = 7;" in sql


def test_build_sql_body_with_design_number():
    valid_rows = [{"id_active": 430071, "id_location_old": 5, "active_number": "ULP0090952"}]
    sql_lines = ParserController._build_move_actives_sql_body(
        valid_rows, id_storage=78, id_consignment=3, id_user=2,
        reason=DEFAULT_REASON, move_date=datetime(2026, 1, 1, 12, 0, 0), id_design_number=74269,
    )
    sql = "\n".join(sql_lines)

    assert (
        "UPDATE public.actives SET id_location = loc_ids[1], id_actves_parent = NULL, "
        "id_actives_root = NULL, id_design_number = 74269, lcn = ('S78.' || lcn_new)::ltree "
        "WHERE id = 430071; -- ULP0090952"
    ) in sql


def test_build_sql_body_without_design_number_omits_clause():
    valid_rows = [{"id_active": 10, "id_location_old": 5, "active_number": "ULP0090952"}]
    sql_lines = ParserController._build_move_actives_sql_body(
        valid_rows, id_storage=7, id_consignment=3, id_user=2,
        reason=DEFAULT_REASON, move_date=datetime(2026, 1, 1, 12, 0, 0),
    )
    sql = "\n".join(sql_lines)

    assert "id_design_number" not in sql


def test_build_sql_body_active_number_comment_strips_newlines():
    valid_rows = [{"id_active": 10, "id_location_old": 5, "active_number": "ULP\n0090952\r"}]
    sql_lines = ParserController._build_move_actives_sql_body(
        valid_rows, id_storage=7, id_consignment=3, id_user=2,
        reason=DEFAULT_REASON, move_date=datetime(2026, 1, 1, 12, 0, 0),
    )
    sql = "\n".join(sql_lines)

    assert "-- ULP 0090952" in sql


def test_build_sql_body_null_old_location():
    valid_rows = [{"id_active": 10, "id_location_old": None, "active_number": "ULP0090952"}]
    sql_lines = ParserController._build_move_actives_sql_body(
        valid_rows, id_storage=7, id_consignment=3, id_user=2,
        reason="", move_date=datetime(2026, 1, 1, 12, 0, 0),
    )
    sql = "\n".join(sql_lines)

    assert "VALUES (NULL, loc_ids[1]" in sql
    assert ", NULL, '2026-01-01 12:00:00', NULL);" in sql  # reason=NULL when empty
