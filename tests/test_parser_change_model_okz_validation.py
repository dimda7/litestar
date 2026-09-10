"""Validation tests for validate_change_model_okz_rows /
models_sql.change_model_okz (controllers/parser/change_okz.py, sql_builders/models.py) — the 'Изменить okz в модели' button.

Changes id_car_place in public.models, matching on models.id (the 'id' column
in the file) and resolving the target car_place through its name in
'new_position'. Unlike 'Изменить lcn в модели', the models.id existence check
here ('SELECT id_car_place FROM public.models WHERE id = :id') needs no
Postgres-specific cast, so it runs fine against SQLite too — every branch is
covered here, none deferred to the pg suite.
"""

from controllers.parser.change_okz import validate_change_model_okz_rows

from sql_builders import models as models_sql
from tests.conftest import make_car_place


async def validate(db_session, rows):
    return await validate_change_model_okz_rows(db_session, rows)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def make_model(db_session, id_car_place=None):
    from models import Models

    model = Models(id_train_type=1, lcn="M1.1", id_car_place=id_car_place, is_default=False)
    db_session.add(model)
    await db_session.flush()
    return model.id


async def test_empty_id_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "", "new_position": "+342_(06)"}])

    assert error_fields(errors) == ["id"]
    assert "пустое" in errors[0]["message"]
    assert valid_rows == []


async def test_invalid_id_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "abc", "new_position": "+342_(06)"}])

    assert error_fields(errors) == ["id"]
    assert "Некорректный id" in errors[0]["message"]


async def test_empty_new_position_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "1", "new_position": ""}])

    assert error_fields(errors) == ["new_position"]
    assert "Пустой new_position" in errors[0]["message"]


async def test_missing_id_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"new_position": "+342_(06)"}])

    assert errors == [{"row": 0, "field": "id", "message": "В файле не найдена колонка 'id'"}]


async def test_missing_new_position_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "1"}])

    assert errors == [
        {"row": 0, "field": "new_position", "message": "В файле не найдена колонка 'new_position'"}
    ]


async def test_model_not_found_reported(db_session):
    await make_car_place(db_session, "+342_(06)")

    errors, valid_rows = await validate(db_session, [{"id": "999", "new_position": "+342_(06)"}])

    assert valid_rows == []
    assert error_fields(errors) == ["id"]
    assert "не найдена" in errors[0]["message"]


async def test_missing_car_place_becomes_one_to_create(db_session):
    model_id = await make_model(db_session)

    errors, valid_rows = await validate(db_session, [{"id": str(model_id), "new_position": "+106.20-01_(01)"}])

    assert errors == []
    assert valid_rows == [{"id": model_id, "new_car_place": "+106.20-01_(01)"}]


async def test_car_place_to_create_named_by_several_models_is_created_once(db_session):
    first_id = await make_model(db_session)
    second_id = await make_model(db_session)

    errors, valid_rows = await validate(db_session, [
        {"id": str(first_id), "new_position": "+106.20-01_(01)"},
        {"id": str(second_id), "new_position": "+106.20-01_(01)"},
    ])

    assert errors == []
    assert valid_rows == [
        {"id": first_id, "new_car_place": "+106.20-01_(01)"},
        {"id": second_id, "new_car_place": "+106.20-01_(01)"},
    ]
    assert "\n".join(models_sql.change_model_okz(valid_rows)).count("INSERT INTO public.car_place") == 1


async def test_ambiguous_car_place_reported(db_session):
    """Regression: car_place.name is not unique in the DB -> used to raise MultipleResultsFound."""
    model_id = await make_model(db_session)
    await make_car_place(db_session, "+342_(06)")
    await make_car_place(db_session, "+342_(06)")  # duplicate name, different id

    errors, valid_rows = await validate(db_session, [{"id": str(model_id), "new_position": "+342_(06)"}])

    assert valid_rows == []
    assert error_fields(errors) == ["new_position"]
    assert "неоднозначен" in errors[0]["message"]


async def test_valid_row_passes(db_session):
    model_id = await make_model(db_session)
    cp_id = await make_car_place(db_session, "+342_(06)")

    errors, valid_rows = await validate(db_session, [{"id": str(model_id), "new_position": "+342_(06)"}])

    assert errors == []
    assert valid_rows == [{"id": model_id, "new_car_place": cp_id}]


async def test_no_op_row_skipped(db_session):
    model_id = await make_model(db_session)
    await make_car_place(db_session, "+342_(06)")

    errors, valid_rows = await validate(
        db_session, [{"id": str(model_id), "position": "+342_(06)", "new_position": "+342_(06)"}]
    )

    assert errors == []
    assert valid_rows == []


async def test_conflicting_new_position_in_same_file_reported(db_session):
    model_id = await make_model(db_session)
    await make_car_place(db_session, "+342_(06)")
    await make_car_place(db_session, "+354_(06)")

    errors, valid_rows = await validate(db_session, [
        {"id": str(model_id), "new_position": "+342_(06)"},
        {"id": str(model_id), "new_position": "+354_(06)"},
    ])

    assert error_fields(errors) == ["id"]
    assert "Конфликт" in errors[0]["message"]
    assert len(valid_rows) == 1


def test_build_sql_lines_single_row():
    valid_rows = [{"id": 168948, "new_car_place": 42}]
    sql_lines = models_sql.change_model_okz(valid_rows)

    assert len(sql_lines) == 2
    assert sql_lines[0] == "UPDATE public.models SET id_car_place = NULL WHERE id IN (168948);"
    assert sql_lines[1] == (
        "UPDATE public.models AS m SET id_car_place = v.new_car_place "
        "FROM (VALUES (168948, 42)) AS v(mid, new_car_place) WHERE m.id = v.mid;"
    )


def test_build_sql_lines_multiple_rows():
    valid_rows = [
        {"id": 1, "new_car_place": 10},
        {"id": 2, "new_car_place": 20},
    ]
    sql_lines = models_sql.change_model_okz(valid_rows)

    assert len(sql_lines) == 2
    assert "WHERE id IN (1, 2);" in sql_lines[0]
    assert "(1, 10)" in sql_lines[1]
    assert "(2, 20)" in sql_lines[1]


def test_build_sql_lines_creates_missing_car_place():
    valid_rows = [{"id": 1, "new_car_place": "+106.20-01_(01)"}]
    sql_lines = models_sql.change_model_okz(valid_rows)

    assert sql_lines == [
        "INSERT INTO public.car_place (name, car_number) VALUES ('+106.20-01_(01)', 1) "
        "ON CONFLICT (name) DO NOTHING RETURNING name;",
        "UPDATE public.models SET id_car_place = NULL WHERE id IN (1);",
        "UPDATE public.models AS m SET id_car_place = v.new_car_place "
        "FROM (VALUES (1, (SELECT id FROM public.car_place WHERE name = '+106.20-01_(01)'))) "
        "AS v(mid, new_car_place) WHERE m.id = v.mid;",
    ]


def test_build_sql_lines_missing_car_place_without_car_number():
    sql_lines = models_sql.change_model_okz([{"id": 1, "new_car_place": "+106.20"}])

    assert sql_lines[0] == (
        "INSERT INTO public.car_place (name, car_number) VALUES ('+106.20', NULL) "
        "ON CONFLICT (name) DO NOTHING RETURNING name;"
    )


def test_build_sql_lines_creates_a_repeated_car_place_once():
    valid_rows = [
        {"id": 1, "new_car_place": "+106.20-01_(01)"},
        {"id": 2, "new_car_place": "+106.20-01_(01)"},
        {"id": 3, "new_car_place": 42},
    ]
    sql = "\n".join(models_sql.change_model_okz(valid_rows))

    assert sql.count("INSERT INTO public.car_place") == 1
    assert "(3, 42)" in sql


def test_build_sql_lines_targets_models_not_actives():
    valid_rows = [{"id": 1, "new_car_place": 10}]
    sql = "\n".join(models_sql.change_model_okz(valid_rows))

    assert "public.models" in sql
    assert "public.actives" not in sql


def test_build_sql_lines_empty():
    assert models_sql.change_model_okz([]) == []
