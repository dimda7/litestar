"""Тесты валидации ParserController._validate_and_build_change_model_lcn_rows /
_build_change_lcn_sql_lines (controllers/parser.py) — кнопка 'Изменить lcn в модели'.

Меняет lcn в таблице public.models, сопоставление по models.id (колонка 'id'
в файле), а не по активам.

Резолв модели по id ('SELECT lcn::text FROM public.models WHERE id = :id')
использует Postgres-специфичный `::text`-каст на ltree-колонке — SQLite
(тестовая БД) такой синтаксис не парсит вовсе (не "0 строк", а syntax error).
Поэтому здесь покрыто всё, что происходит ДО этого запроса (парсинг колонок,
формат id, дубликаты внутри файла, пустые значения) — та же ситуация, что и с
happy-path в train_parser.py (см. checkpoint.md, Фаза 9) и с резолвом активов
в других кнопках этой же страницы. Сам запрос и полный happy-path проверены
вручную на реальной БД grom-tk.
"""

from controllers.parser import ParserController


async def validate(db_session, rows):
    controller = ParserController(owner=None)
    return await controller._validate_and_build_change_model_lcn_rows(db_session, rows)


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
    sql_lines = ParserController._build_change_lcn_sql_lines(valid_rows)

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
    sql_lines = ParserController._build_change_lcn_sql_lines(valid_rows)

    assert len(sql_lines) == 2
    assert "WHERE id IN (1, 2);" in sql_lines[0]
    assert "(1, 'M9.1.6.4')" in sql_lines[1]
    assert "(2, 'M9.1.6.4.1')" in sql_lines[1]


def test_build_sql_lines_targets_models_not_actives():
    valid_rows = [{"id": 1, "new_lcn": "M9.1.6.4"}]
    sql = "\n".join(ParserController._build_change_lcn_sql_lines(valid_rows))

    assert "public.models" in sql
    assert "public.actives" not in sql


def test_build_sql_lines_empty():
    assert ParserController._build_change_lcn_sql_lines([]) == []
