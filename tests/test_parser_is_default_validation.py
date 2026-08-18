"""Tests for validate_is_default_rows / models_sql.set_is_default
(controllers/parser/is_default.py, sql_builders/models.py) — the 'Изменить серийность в модели' button (models.is_default).
"""

from controllers.parser.is_default import validate_is_default_rows

from models import Models
from sql_builders import models as models_sql


async def validate(db_session, rows):
    return await validate_is_default_rows(db_session, rows)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def make_model(db_session, id_train_type=1, lcn="M1.1", id_car_place=1,
                      id_design_number=1, is_default=False) -> int:
    model = Models(id_train_type=id_train_type, lcn=lcn, id_car_place=id_car_place,
                    id_design_number=id_design_number, is_default=is_default)
    db_session.add(model)
    await db_session.flush()
    return model.id


async def test_missing_id_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"isdefault": "true"}])

    assert errors == [{"row": 0, "field": "id", "message": "В файле не найдена колонка 'id'"}]
    assert valid_rows == []


async def test_missing_isdefault_column_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "1"}])

    assert errors == [{"row": 0, "field": "isdefault", "message": "В файле не найдена колонка 'isdefault'"}]


async def test_empty_id_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "", "isdefault": "true"}])

    assert error_fields(errors) == ["id"]
    assert "пустое" in errors[0]["message"]


async def test_invalid_id_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "abc", "isdefault": "true"}])

    assert error_fields(errors) == ["id"]
    assert "Некорректный id" in errors[0]["message"]


async def test_invalid_isdefault_value_reported(db_session):
    model_id = await make_model(db_session)
    errors, valid_rows = await validate(db_session, [{"id": str(model_id), "isdefault": "maybe"}])

    assert error_fields(errors) == ["isdefault"]
    assert "Неверное значение isdefault" in errors[0]["message"]


async def test_model_not_found_reported(db_session):
    errors, valid_rows = await validate(db_session, [{"id": "999999", "isdefault": "true"}])

    assert error_fields(errors) == ["id"]
    assert "не найдена" in errors[0]["message"]


async def test_valid_row_da_net_accepted(db_session):
    model_id = await make_model(db_session, is_default=False)
    errors, valid_rows = await validate(db_session, [{"id": str(model_id), "isdefault": "да"}])

    assert errors == []
    assert valid_rows == [{"id": model_id, "is_default": True}]


async def test_valid_row_false_accepted(db_session):
    model_id = await make_model(db_session, is_default=True)
    errors, valid_rows = await validate(db_session, [{"id": str(model_id), "isdefault": "нет"}])

    assert errors == []
    assert valid_rows == [{"id": model_id, "is_default": False}]


async def test_duplicate_id_same_value_deduplicated(db_session):
    model_id = await make_model(db_session, is_default=False)
    errors, valid_rows = await validate(db_session, [
        {"id": str(model_id), "isdefault": "true"},
        {"id": str(model_id), "isdefault": "1"},
    ])

    assert errors == []
    assert valid_rows == [{"id": model_id, "is_default": True}]


async def test_duplicate_id_conflicting_value_reported(db_session):
    model_id = await make_model(db_session, is_default=False)
    errors, valid_rows = await validate(db_session, [
        {"id": str(model_id), "isdefault": "true"},
        {"id": str(model_id), "isdefault": "false"},
    ])

    assert error_fields(errors) == ["id"]
    assert "Конфликт" in errors[0]["message"]


async def test_switching_to_false_never_conflicts(db_session):
    model_id = await make_model(db_session, lcn="M1.1", id_car_place=1, is_default=True)
    errors, valid_rows = await validate(db_session, [{"id": str(model_id), "isdefault": "false"}])

    assert errors == []
    assert valid_rows == [{"id": model_id, "is_default": False}]


async def test_conflict_with_existing_default_lcn_car_place(db_session):
    await make_model(db_session, id_train_type=1, lcn="M1.1", id_car_place=1,
                      id_design_number=1, is_default=True)
    other_id = await make_model(db_session, id_train_type=2, lcn="M1.1", id_car_place=1,
                                 id_design_number=2, is_default=False)

    errors, valid_rows = await validate(db_session, [{"id": str(other_id), "isdefault": "true"}])

    assert error_fields(errors) == ["isdefault"]
    assert "lcn, car_place" in errors[0]["message"]
    assert valid_rows == []


async def test_conflict_with_existing_default_car_type_design(db_session):
    await make_model(db_session, id_train_type=1, lcn="M1.1", id_car_place=1,
                      id_design_number=1, is_default=True)
    other_id = await make_model(db_session, id_train_type=1, lcn="M2.2", id_car_place=1,
                                 id_design_number=1, is_default=False)

    errors, valid_rows = await validate(db_session, [{"id": str(other_id), "isdefault": "true"}])

    assert error_fields(errors) == ["isdefault"]
    assert "car_place, train_type, design_number" in errors[0]["message"]


async def test_no_false_positive_conflict_with_self(db_session):
    model_id = await make_model(db_session, lcn="M1.1", id_car_place=1, is_default=True)

    errors, valid_rows = await validate(db_session, [{"id": str(model_id), "isdefault": "true"}])

    assert errors == []
    assert valid_rows == [{"id": model_id, "is_default": True}]


async def test_batch_conflict_two_rows_same_slot(db_session):
    first_id = await make_model(db_session, id_train_type=1, lcn="M1.1", id_car_place=1,
                                 id_design_number=1, is_default=False)
    second_id = await make_model(db_session, id_train_type=2, lcn="M1.1", id_car_place=1,
                                  id_design_number=2, is_default=False)

    errors, valid_rows = await validate(db_session, [
        {"id": str(first_id), "isdefault": "true"},
        {"id": str(second_id), "isdefault": "true"},
    ])

    assert error_fields(errors) == ["isdefault"]
    assert valid_rows == [{"id": first_id, "is_default": True}]


async def test_batch_freed_slot_reused_is_not_a_conflict(db_session):
    """One file clears the old default and sets a new one at the same (lcn, car_place) —
    there must be no false conflict, since the old one is cleared by the same batch."""
    old_id = await make_model(db_session, id_train_type=1, lcn="M1.1", id_car_place=1,
                               id_design_number=1, is_default=True)
    new_id = await make_model(db_session, id_train_type=2, lcn="M1.1", id_car_place=1,
                               id_design_number=2, is_default=False)

    errors, valid_rows = await validate(db_session, [
        {"id": str(old_id), "isdefault": "false"},
        {"id": str(new_id), "isdefault": "true"},
    ])

    assert errors == []
    assert {"id": old_id, "is_default": False} in valid_rows
    assert {"id": new_id, "is_default": True} in valid_rows


def test_build_sql_lines_false_before_true():
    valid_rows = [
        {"id": 1, "is_default": True},
        {"id": 2, "is_default": False},
    ]
    sql_lines = models_sql.set_is_default(valid_rows)

    assert sql_lines == [
        "UPDATE public.models SET is_default = FALSE WHERE id = 2;",
        "UPDATE public.models SET is_default = TRUE WHERE id = 1;",
    ]


def test_build_sql_lines_empty():
    assert models_sql.set_is_default([]) == []
