"""Тесты валидации ParserController._validate_and_build_change_lcn_rows / _build_change_lcn_sql_lines (controllers/parser.py)."""

from controllers.parser import ParserController
from tests.conftest import make_train


async def validate(db_session, rows):
    controller = ParserController(owner=None)
    return await controller._validate_and_build_change_lcn_rows(db_session, rows)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


def make_row(new="", old="") -> dict:
    return {"lsn": new, "Старый lsn": old}


async def test_valid_row_builds_pairs_for_all_trains(db_session):
    train_a = await make_train(db_session, id_train_type=9)
    train_b = await make_train(db_session, id_train_type=9)

    errors, valid_rows = await validate(db_session, [make_row("M9.1.6.4", "M9.1.6")])

    assert errors == []
    assert sorted(valid_rows, key=lambda r: r["old_lcn"]) == sorted(
        [
            {"old_lcn": f"{train_a}.1.6", "new_lcn": f"{train_a}.1.6.4"},
            {"old_lcn": f"{train_b}.1.6", "new_lcn": f"{train_b}.1.6.4"},
        ],
        key=lambda r: r["old_lcn"],
    )


async def test_old_lcn_column_alias(db_session):
    train_id = await make_train(db_session, id_train_type=9)

    errors, valid_rows = await validate(db_session, [{"lcn": "M9.1.6.4", "Старый lcn": "M9.1.6"}])

    assert errors == []
    assert valid_rows == [{"old_lcn": f"{train_id}.1.6", "new_lcn": f"{train_id}.1.6.4"}]


async def test_duplicate_rows_deduplicate_to_single_pair(db_session):
    train_id = await make_train(db_session, id_train_type=9)

    errors, valid_rows = await validate(
        db_session, [make_row("M9.1.6.4", "M9.1.6"), make_row("M9.1.6.4", "M9.1.6")],
    )

    assert errors == []
    assert valid_rows == [{"old_lcn": f"{train_id}.1.6", "new_lcn": f"{train_id}.1.6.4"}]


async def test_conflicting_new_value_for_same_old_lcn_reported(db_session):
    await make_train(db_session, id_train_type=9)

    errors, valid_rows = await validate(
        db_session, [make_row("M9.1.6.4", "M9.1.6"), make_row("M9.1.6.5", "M9.1.6")],
    )

    assert error_fields(errors) == ["lcn"]
    assert "Конфликт" in errors[0]["message"]


async def test_train_type_mismatch_reported(db_session):
    await make_train(db_session, id_train_type=9)
    await make_train(db_session, id_train_type=10)

    errors, valid_rows = await validate(db_session, [make_row("M10.1.6.4", "M9.1.6")])

    assert error_fields(errors) == ["lcn"]
    assert "не совпадает" in errors[0]["message"]


async def test_no_trains_for_train_type_reported(db_session):
    errors, valid_rows = await validate(db_session, [make_row("M9.1.6.4", "M9.1.6")])

    assert error_fields(errors) == ["lcn"]
    assert "не найдены" in errors[0]["message"]


async def test_unparseable_new_lcn_reported(db_session):
    await make_train(db_session, id_train_type=9)

    errors, valid_rows = await validate(db_session, [make_row("abc", "M9.1.6")])

    assert error_fields(errors) == ["lcn"]


async def test_unparseable_old_lcn_reported(db_session):
    await make_train(db_session, id_train_type=9)

    errors, valid_rows = await validate(db_session, [make_row("M9.1.6.4", "abc")])

    assert error_fields(errors) == ["lcn"]


async def test_empty_new_lcn_reported(db_session):
    errors, valid_rows = await validate(db_session, [make_row("", "M9.1.6")])

    assert error_fields(errors) == ["lcn"]
    assert "Пустой lcn" in errors[0]["message"]


async def test_empty_old_lcn_reported(db_session):
    errors, valid_rows = await validate(db_session, [make_row("M9.1.6.4", "")])

    assert error_fields(errors) == ["lcn"]
    assert "Пустой Старый lcn" in errors[0]["message"]


async def test_missing_new_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"Старый lsn": "M9.1.6"}])

    assert errors == [{"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"}]


async def test_missing_old_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"lsn": "M9.1.6.4"}])

    assert errors == [{"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'Старый lsn' (или 'Старый lcn')"}]


def test_build_sql_lines_single_pair():
    valid_rows = [{"old_lcn": "1.1.6", "new_lcn": "1.1.6.4"}]
    sql_lines = ParserController._build_change_lcn_sql_lines(valid_rows)

    assert len(sql_lines) == 2
    assert sql_lines[0] == "UPDATE public.actives SET lcn = ('Z' || lcn::text)::ltree WHERE lcn::text IN ('1.1.6');"
    assert sql_lines[1] == (
        "UPDATE public.actives AS act SET lcn = v.new_lcn::ltree "
        "FROM (VALUES ('Z1.1.6', '1.1.6.4')) AS v(tmp_lcn, new_lcn) WHERE act.lcn::text = v.tmp_lcn;"
    )


def test_build_sql_lines_multiple_pairs():
    valid_rows = [
        {"old_lcn": "1.1.6", "new_lcn": "1.1.6.4"},
        {"old_lcn": "2.1.6", "new_lcn": "2.1.6.4"},
    ]
    sql_lines = ParserController._build_change_lcn_sql_lines(valid_rows)

    assert len(sql_lines) == 2
    assert "'1.1.6'" in sql_lines[0] and "'2.1.6'" in sql_lines[0]
    assert "('Z1.1.6', '1.1.6.4')" in sql_lines[1]
    assert "('Z2.1.6', '2.1.6.4')" in sql_lines[1]


def test_build_sql_lines_chained_rename_is_collision_safe():
    """Цепочка: A ('1.1.6' -> '1.1.6.4') и B ('1.1.6.4' -> '1.1.6.4.1') —
    новый lcn A совпадает со старым lcn B. Временный 'Z'-префикс в первом шаге
    гарантирует, что на момент второго шага среди старых/временных значений
    нет дублей с финальными new_lcn."""
    valid_rows = [
        {"old_lcn": "1.1.6", "new_lcn": "1.1.6.4"},
        {"old_lcn": "1.1.6.4", "new_lcn": "1.1.6.4.1"},
    ]
    sql_lines = ParserController._build_change_lcn_sql_lines(valid_rows)

    tmp_values = {f"Z{vr['old_lcn']}" for vr in valid_rows}
    new_values = {vr["new_lcn"] for vr in valid_rows}
    assert tmp_values.isdisjoint(new_values)
    assert "('Z1.1.6', '1.1.6.4')" in sql_lines[1]
    assert "('Z1.1.6.4', '1.1.6.4.1')" in sql_lines[1]


def test_build_sql_lines_empty():
    assert ParserController._build_change_lcn_sql_lines([]) == []


def test_build_move_no_relocate_sql_lines_resets_parent_and_root():
    valid_rows = [{"old_lcn": "1.1.6", "new_lcn": "1.1.6.4"}]
    sql_lines = ParserController._build_move_no_relocate_sql_lines(valid_rows)

    assert len(sql_lines) == 2
    assert sql_lines[0] == "UPDATE public.actives SET lcn = ('Z' || lcn::text)::ltree WHERE lcn::text IN ('1.1.6');"
    assert sql_lines[1] == (
        "UPDATE public.actives AS act SET lcn = v.new_lcn::ltree, id_actves_parent = NULL, id_actives_root = NULL "
        "FROM (VALUES ('Z1.1.6', '1.1.6.4')) AS v(tmp_lcn, new_lcn) WHERE act.lcn::text = v.tmp_lcn;"
    )


def test_build_move_no_relocate_sql_lines_no_location_or_relocate_touch():
    valid_rows = [{"old_lcn": "1.1.6", "new_lcn": "1.1.6.4"}]
    sql_lines = ParserController._build_move_no_relocate_sql_lines(valid_rows)
    sql = "\n".join(sql_lines)

    assert "id_location" not in sql
    assert "relocate" not in sql


def test_build_move_no_relocate_sql_lines_empty():
    assert ParserController._build_move_no_relocate_sql_lines([]) == []
