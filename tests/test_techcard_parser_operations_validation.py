"""Validation tests for validate_operation_rows (controllers/techcard_parser/operations.py)."""

import pytest

from controllers.techcard_parser.operations import validate_operation_rows
from models import Techcard, TechcardStatus, TechcardStatusTypes, TechcardWorkOperation


async def make_techcard(db_session, name: str, description: str | None = None) -> int:
    obj = Techcard(name=name, description=description)
    db_session.add(obj)
    await db_session.flush()
    return obj.id


def make_row(**overrides) -> dict:
    row = {
        "Техкарта": " I5_DESRUS ",
        "Номер операции в техкарте": 40,
        "Наименование операции": "Замеры КП",
        "Вложенная техкарта": "USI",
        "Описание вложенной техкарты": "Замеры КП",
    }
    row.update(overrides)
    return row


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def test_rows_are_resolved_to_ids(db_session):
    head = await make_techcard(db_session, "I5_DESRUS")
    nested = await make_techcard(db_session, "USI", "Замеры КП")

    errors, valid_rows = await validate_operation_rows(db_session, [
        make_row(),
        make_row(**{"Номер операции в техкарте": 10, "Наименование операции": "Проверить салонное оборудование",
                    "Вложенная техкарта": None, "Описание вложенной техкарты": None}),
    ])

    assert errors == []
    assert valid_rows == [
        {"id_techcard_head": head, "task": 40, "task_name": "Замеры КП", "id_techcard_early": nested},
        {"id_techcard_head": head, "task": 10, "task_name": "Проверить салонное оборудование",
         "id_techcard_early": None},
    ]


async def make_head_and_nested(db_session) -> None:
    await make_techcard(db_session, "I5_DESRUS")
    await make_techcard(db_session, "USI", "Замеры КП")


@pytest.mark.parametrize("empty", [None, "", " "])
async def test_row_with_only_the_techcard_is_skipped(db_session, empty):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(db_session, [
        make_row(**{"Номер операции в техкарте": empty, "Наименование операции": empty,
                    "Вложенная техкарта": None, "Описание вложенной техкарты": None}),
        make_row(),
    ])

    assert errors == []
    assert [vr["task"] for vr in valid_rows] == [40]


@pytest.mark.parametrize("field", ["Номер операции в техкарте", "Наименование операции"])
async def test_only_one_of_number_and_name_empty(db_session, field):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(db_session, [make_row(**{field: None})])

    assert valid_rows == []
    assert error_fields(errors) == [field]


@pytest.mark.parametrize("empty", [None, ""])
async def test_techcard_empty(db_session, empty):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(db_session, [make_row(**{"Техкарта": empty})])

    assert valid_rows == []
    assert error_fields(errors) == ["Техкарта"]


@pytest.mark.parametrize("raw", [0, -10, 10.5, "abc", "1e400", True])
async def test_bad_task_number(db_session, raw):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(db_session, [make_row(**{"Номер операции в техкарте": raw})])

    assert valid_rows == []
    assert error_fields(errors) == ["Номер операции в техкарте"]


@pytest.mark.parametrize("raw, expected", [(132, 132), ("133", 133), (10.0, 10), (" 20 ", 20)])
async def test_task_number_forms(db_session, raw, expected):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(db_session, [make_row(**{"Номер операции в техкарте": raw})])

    assert errors == []
    assert valid_rows[0]["task"] == expected


async def test_task_name_longer_than_column(db_session):
    await make_head_and_nested(db_session)

    ok_errors, _ = await validate_operation_rows(db_session, [make_row(**{"Наименование операции": "я" * 256})])
    errors, valid_rows = await validate_operation_rows(db_session, [make_row(**{"Наименование операции": "я" * 257})])

    assert ok_errors == []
    assert valid_rows == []
    assert error_fields(errors) == ["Наименование операции"]


@pytest.mark.parametrize("field", ["Техкарта", "Вложенная техкарта"])
async def test_techcard_not_found(db_session, field):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(db_session, [make_row(**{field: "NO_SUCH"})])

    assert valid_rows == []
    assert error_fields(errors) == [field]
    assert "NO_SUCH" in errors[0]["message"]


async def test_nested_description_mismatch(db_session):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(
        db_session, [make_row(**{"Описание вложенной техкарты": "Дефектоскопия оси"})])

    assert valid_rows == []
    assert error_fields(errors) == ["Описание вложенной техкарты"]
    assert "Замеры КП" in errors[0]["message"]


@pytest.mark.parametrize("description", [None, "", " Замеры КП  "])
async def test_nested_description_empty_or_padded_is_accepted(db_session, description):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(
        db_session, [make_row(**{"Описание вложенной техкарты": description})])

    assert errors == []
    assert len(valid_rows) == 1


async def test_nested_description_with_trailing_space_in_database(db_session):
    await make_techcard(db_session, "I5_DESRUS")
    await make_techcard(db_session, "TRAFO_OIL", "Забор трансформаторного масла ")

    errors, _ = await validate_operation_rows(db_session, [make_row(**{
        "Вложенная техкарта": "TRAFO_OIL", "Описание вложенной техкарты": "Забор трансформаторного масла "})])

    assert errors == []


async def test_nested_is_the_techcard_itself(db_session):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(db_session, [make_row(**{
        "Вложенная техкарта": "I5_DESRUS", "Описание вложенной техкарты": None})])

    assert valid_rows == []
    assert error_fields(errors) == ["Вложенная техкарта"]


async def test_techcard_already_has_operations(db_session):
    await make_head_and_nested(db_session)
    head = await make_techcard(db_session, "I7_DESRUS")
    db_session.add(TechcardWorkOperation(task=10, task_name="Старая операция", id_techcard_head=head))
    await db_session.flush()

    errors, valid_rows = await validate_operation_rows(db_session, [
        make_row(), make_row(**{"Техкарта": "I7_DESRUS"}), make_row(**{"Техкарта": "I7_DESRUS",
                                                                      "Номер операции в техкарте": 50})])

    assert [(e["row"], e["field"]) for e in errors] == [(2, "Техкарта"), (3, "Техкарта")]
    assert "I7_DESRUS" in errors[0]["message"]


async def test_task_number_repeated_for_one_techcard(db_session):
    await make_head_and_nested(db_session)
    await make_techcard(db_session, "I7_DESRUS")

    errors, valid_rows = await validate_operation_rows(db_session, [
        make_row(),
        make_row(**{"Техкарта": "I7_DESRUS"}),
        make_row(**{"Наименование операции": "Другая операция", "Вложенная техкарта": None}),
    ])

    assert [(e["row"], e["field"]) for e in errors] == [(3, "Номер операции в техкарте")]


async def test_headers_are_case_insensitive(db_session):
    await make_head_and_nested(db_session)
    row = {f" {k.upper()} ": v for k, v in make_row().items()}

    errors, valid_rows = await validate_operation_rows(db_session, [row])

    assert errors == []
    assert valid_rows[0]["task"] == 40


@pytest.mark.parametrize("column", ["Техкарта", "Номер операции в техкарте", "Наименование операции",
                                    "Вложенная техкарта", "Описание вложенной техкарты"])
async def test_missing_column(db_session, column):
    await make_head_and_nested(db_session)
    row = make_row()
    del row[column]

    errors, valid_rows = await validate_operation_rows(db_session, [row])

    assert valid_rows == []
    assert [(e["row"], e["field"]) for e in errors] == [(0, column)]


async def test_no_rows(db_session):
    assert await validate_operation_rows(db_session, []) == ([], [])


@pytest.mark.parametrize("filled", [{"Вложенная техкарта": "USI"}, {"Описание вложенной техкарты": "Замеры КП"}])
async def test_row_without_number_and_name_but_with_nested_data_is_an_error(db_session, filled):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(db_session, [make_row(**{
        "Номер операции в техкарте": None, "Наименование операции": None,
        "Вложенная техкарта": None, "Описание вложенной техкарты": None, **filled})])

    assert valid_rows == []
    assert error_fields(errors) == ["Номер операции в техкарте"]


async def test_nested_description_without_nested_techcard_is_an_error(db_session):
    await make_head_and_nested(db_session)

    errors, valid_rows = await validate_operation_rows(db_session, [make_row(**{"Вложенная техкарта": None})])

    assert valid_rows == []
    assert error_fields(errors) == ["Описание вложенной техкарты"]


async def test_missing_techcard_error_hints_at_sheet_one(db_session):
    errors, _ = await validate_operation_rows(db_session, [make_row(**{"Вложенная техкарта": None,
                                                                       "Описание вложенной техкарты": None})])

    assert "Создать техкарты" in errors[0]["message"]


async def test_inactive_nested_techcard_is_accepted(db_session):
    await make_techcard(db_session, "I5_DESRUS")
    status_type = TechcardStatusTypes(status="INACTIVE")
    db_session.add(status_type)
    await db_session.flush()
    status = TechcardStatus(id_techcard_status_types=status_type.id)
    db_session.add(status)
    await db_session.flush()
    db_session.add(Techcard(name="USI", description="Замеры КП", id_techcard_status=status.id))
    await db_session.flush()

    errors, valid_rows = await validate_operation_rows(db_session, [make_row()])

    assert errors == []
    assert len(valid_rows) == 1
