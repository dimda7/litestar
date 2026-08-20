"""Validation tests for validate_change_okz_active_rows /
models_sql.change_okz_active (controllers/parser/change_okz_active.py, sql_builders/models.py) —
the 'Изменить okz в активе по модели' button.

Unlike 'Изменить okz в модели' (which matches by models.id directly), this
button starts from a model lcn ('lsn'), fans it out across every train of
that model's train type (same mechanic as 'set serial=none lcn'), and
reassigns public.location.id_car_place for whichever assets actually exist
at the computed per-train lcns — actives has no id_car_place column of its
own.

The 'lsn is a real models.lcn' check uses the Postgres-specific `::text`
cast on an ltree column, which SQLite (the test DB) cannot parse at all —
same situation as change_model_lcn's suite. It runs last in the validator
precisely so a row already doomed by a missing train or an unresolved
car_place never reaches it, but any row that would otherwise fully succeed
(the happy path, the in-file conflict check, which needs a first row to
fully succeed to seed its dict) still has to run it — those cases are
exercised only against the real database in tests/pg/test_models_sql.py.
"""

from controllers.parser.change_okz_active import validate_change_okz_active_rows

from sql_builders import models as models_sql
from tests.conftest import make_car_place, make_train


async def validate(db_session, rows):
    return await validate_change_okz_active_rows(db_session, rows)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def test_empty_lcn_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"lsn": "", "new_position": "+342_(06)"}])

    assert error_fields(errors) == ["lcn"]
    assert "Пустой lcn" in errors[0]["message"]
    assert valid_rows == []


async def test_empty_new_position_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"lsn": "M9.6.5", "new_position": ""}])

    assert error_fields(errors) == ["new_position"]
    assert "Пустой new_position" in errors[0]["message"]


async def test_missing_lcn_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"new_position": "+342_(06)"}])

    assert errors == [{"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"}]


async def test_missing_new_position_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"lsn": "M9.6.5"}])

    assert errors == [
        {"row": 0, "field": "new_position", "message": "В файле не найдена колонка 'new_position'"}
    ]


async def test_no_op_row_skipped(db_session):
    errors, valid_rows = await validate(
        db_session, [{"lsn": "M9.6.5", "position": "+342_(06)", "new_position": "+342_(06)"}]
    )

    assert errors == []
    assert valid_rows == []


async def test_unparseable_lcn_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"lsn": "abc", "new_position": "+342_(06)"}])

    assert error_fields(errors) == ["lcn"]
    assert "распознать" in errors[0]["message"]


async def test_no_trains_for_train_type_reported(db_session):
    await make_car_place(db_session, "+342_(06)")

    errors, valid_rows = await validate(db_session, [{"lsn": "M9.6.5", "new_position": "+342_(06)"}])

    assert error_fields(errors) == ["lcn"]
    assert "не найдены" in errors[0]["message"]
    assert valid_rows == []


async def test_car_place_not_found_reported(db_session):
    await make_train(db_session, id_train_type=9)

    errors, valid_rows = await validate(db_session, [{"lsn": "M9.6.5", "new_position": "Неизвестный вагон"}])

    assert error_fields(errors) == ["new_position"]
    assert "car_place не найден" in errors[0]["message"]


async def test_ambiguous_car_place_reported(db_session):
    """Regression: car_place.name is not unique in the DB -> used to raise MultipleResultsFound."""
    await make_train(db_session, id_train_type=9)
    await make_car_place(db_session, "+342_(06)")
    await make_car_place(db_session, "+342_(06)")  # duplicate name, different id

    errors, valid_rows = await validate(db_session, [{"lsn": "M9.6.5", "new_position": "+342_(06)"}])

    assert error_fields(errors) == ["new_position"]
    assert "неоднозначен" in errors[0]["message"]


def test_build_sql_single_group_single_train():
    valid_rows = [{"lcn_trains": ["4021.6.5"], "new_car_place_id": 42}]
    sql_lines = models_sql.change_okz_active(valid_rows)

    assert len(sql_lines) == 1
    assert sql_lines[0] == (
        "UPDATE public.location AS loc SET id_car_place = v.new_car_place "
        "FROM public.actives AS act, (VALUES ('4021.6.5', 42)) AS v(lcn_train, new_car_place) "
        "WHERE act.lcn::text = v.lcn_train AND loc.id = act.id_location;"
    )


def test_build_sql_single_group_multiple_trains():
    valid_rows = [{"lcn_trains": ["4021.6.5", "4022.6.5"], "new_car_place_id": 42}]
    sql_lines = models_sql.change_okz_active(valid_rows)

    assert len(sql_lines) == 1
    assert "('4021.6.5', 42)" in sql_lines[0]
    assert "('4022.6.5', 42)" in sql_lines[0]


def test_build_sql_multiple_groups():
    valid_rows = [
        {"lcn_trains": ["4021.6.5"], "new_car_place_id": 42},
        {"lcn_trains": ["4021.7.1"], "new_car_place_id": 55},
    ]
    sql_lines = models_sql.change_okz_active(valid_rows)

    assert "('4021.6.5', 42)" in sql_lines[0]
    assert "('4021.7.1', 55)" in sql_lines[0]


def test_build_sql_targets_location_and_actives_not_models():
    valid_rows = [{"lcn_trains": ["4021.6.5"], "new_car_place_id": 42}]
    sql = "\n".join(models_sql.change_okz_active(valid_rows))

    assert "public.location" in sql
    assert "public.actives" in sql
    assert "public.models" not in sql


def test_build_sql_empty():
    assert models_sql.change_okz_active([]) == []
