"""Validation tests for validate_change_model_lcn_rows /
models_sql.change_model_lcn (controllers/parser/change_lcn.py, sql_builders/models.py) — the 'Изменить lcn в модели' button.

Changes lcn in public.models, matching on models.id (the 'id' column in the
file) rather than on assets.

Resolving a model by id ('SELECT lcn::text FROM public.models WHERE id = :id')
uses the Postgres-specific `::text` cast on an ltree column — SQLite (the test
DB) does not parse that syntax at all (a syntax error, not "0 rows"). So what is
covered here is everything happening BEFORE that query (column parsing, id
format, duplicates within the file, empty values) — the same situation as the
happy path in train_parser.py (see checkpoint.md, phase 9) and as asset
resolution in the other buttons of this page. The query itself and the full
happy path were verified by hand against the real grom-tk database.
"""

from controllers.parser.change_lcn import validate_change_model_lcn_rows

from sql_builders import models as models_sql


async def validate(db_session, rows):
    return await validate_change_model_lcn_rows(db_session, rows)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def test_empty_id_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "", "lsn": "M9.1.6.4"}])

    assert error_fields(errors) == ["id"]
    assert "пустое" in errors[0]["message"]
    assert valid_rows == []


async def test_invalid_id_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "abc", "lsn": "M9.1.6.4"}])

    assert error_fields(errors) == ["id"]
    assert "Некорректный id" in errors[0]["message"]


async def test_empty_new_lcn_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "1", "lsn": ""}])

    assert error_fields(errors) == ["lcn"]
    assert "Пустой lcn" in errors[0]["message"]


async def test_missing_id_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"lsn": "M9.1.6.4"}])

    assert errors == [{"row": 0, "field": "id", "message": "В файле не найдена колонка 'id'"}]


async def test_missing_lcn_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "1"}])

    assert errors == [{"row": 0, "field": "lcn", "message": "В файле не найдена колонка 'lsn' (или 'lcn')"}]


def test_build_sql_lines_single_row():
    valid_rows = [{"id": 269122, "new_lcn": "M9.1.6.4"}]
    sql_lines = models_sql.change_model_lcn(valid_rows)

    assert len(sql_lines) == 2
    assert sql_lines[0] == "UPDATE public.models SET lcn = ('Z' || lcn::text)::ltree WHERE id IN (269122);"
    assert sql_lines[1] == (
        "UPDATE public.models AS m SET lcn = v.new_lcn::ltree "
        "FROM (VALUES (269122, 'M9.1.6.4')) AS v(mid, new_lcn) WHERE m.id = v.mid;"
    )


def test_build_sql_lines_multiple_rows():
    valid_rows = [
        {"id": 1, "new_lcn": "M9.1.6.4"},
        {"id": 2, "new_lcn": "M9.1.6.4.1"},
    ]
    sql_lines = models_sql.change_model_lcn(valid_rows)

    assert len(sql_lines) == 2
    assert "WHERE id IN (1, 2);" in sql_lines[0]
    assert "(1, 'M9.1.6.4')" in sql_lines[1]
    assert "(2, 'M9.1.6.4.1')" in sql_lines[1]


def test_build_sql_lines_targets_models_not_actives():
    valid_rows = [{"id": 1, "new_lcn": "M9.1.6.4"}]
    sql = "\n".join(models_sql.change_model_lcn(valid_rows))

    assert "public.models" in sql
    assert "public.actives" not in sql


def test_build_sql_lines_empty():
    assert models_sql.change_model_lcn([]) == []
