"""Validation tests for validate_techcard_rows (controllers/techcard_parser.py)."""

import pytest

from models import (
    Classifier, ClassifierType, GroupType, Priorities, Specification, Techcard, TechcardStatusTypes, WorkType,
)
from controllers.techcard_parser.create import validate_techcard_rows


async def add(db_session, obj) -> int:
    db_session.add(obj)
    await db_session.flush()
    return obj.id


async def make_references(db_session) -> dict[str, int]:
    ids = {
        "work_type": await add(db_session, WorkType(name="PM")),
        "group_type": await add(db_session, GroupType(name="SERVICE")),
        "priority": await add(db_session, Priorities(priority=3)),
        "status": await add(db_session, TechcardStatusTypes(status="ACTIVE")),
        "classifier_type": await add(db_session, ClassifierType(name="RUS_INSPECTION")),
    }
    ids["train_type_maint"] = await add(db_session, Classifier(name="TRAIN_TYPE_MAINT",
                                                               id_classifier_type=ids["classifier_type"]))
    ids["inspection_type"] = await add(db_session, Classifier(name="INSPECTION_TYPE",
                                                              id_classifier_type=ids["classifier_type"]))
    ids["spec_es1"] = await add(db_session, Specification(description="ЭС1", id_classifier=ids["train_type_maint"],
                                                          is_active=True, is_delete=False))
    ids["spec_i5"] = await add(db_session, Specification(description="И5", id_classifier=ids["inspection_type"],
                                                         is_active=False, is_delete=False))
    return ids


def make_row(**overrides) -> dict:
    row = {
        "Номер техкарты": " I5_DESRUS ",
        "Статус": "ACTIVE",
        "Название техкарты": "Инспекция I5",
        "Полное описание": "<p>O'Brien</p>",
        "Тип работы": "PM",
        "Приоритет": 3,
        "Бригада": "SERVICE",
        "Использовать техкарту только в БПТОиР?": "Да",
        "Классификация техкарты": "RUS_INSPECTION",
        "TRAIN_TYPE_MAINT": "ЭС1",
        "INSPECTION_TYPE": "И5",
    }
    row.update(overrides)
    return row


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def test_row_is_resolved_to_ids(db_session):
    ids = await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row()])

    assert errors == []
    assert valid_rows == [{
        "name": "I5_DESRUS",
        "description": "Инспекция I5",
        "description_full": "<p>O'Brien</p>",
        "id_work_type": ids["work_type"],
        "id_priorities": ids["priority"],
        "id_group_type": ids["group_type"],
        "use_only_main_ptoir": True,
        "id_classifier_type": ids["classifier_type"],
        "id_status_type": ids["status"],
        "specification_ids": [ids["spec_es1"], ids["spec_i5"]],
    }]


@pytest.mark.parametrize("field", ["Номер техкарты", "Название техкарты", "Тип работы", "Статус",
                                   "Классификация техкарты"])
@pytest.mark.parametrize("empty", [None, "", "  "])
async def test_required_field_empty(db_session, field, empty):
    await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row(**{field: empty})])

    assert valid_rows == []
    assert error_fields(errors) == [field]
    assert errors[0]["row"] == 1


@pytest.mark.parametrize("field, value", [("Тип работы", "XX"), ("Статус", "XX"), ("Классификация техкарты", "XX"),
                                          ("Бригада", "XX"), ("Приоритет", 42), ("Приоритет", "abc")])
async def test_unknown_reference_value(db_session, field, value):
    await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row(**{field: value})])

    assert valid_rows == []
    assert error_fields(errors) == [field]
    assert str(value) in errors[0]["message"]


async def test_reference_value_found_twice_is_an_error(db_session):
    await make_references(db_session)
    await add(db_session, WorkType(name="PM"))

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row()])

    assert valid_rows == []
    assert error_fields(errors) == ["Тип работы"]


@pytest.mark.parametrize("field", ["Приоритет", "Бригада", "Полное описание"])
@pytest.mark.parametrize("empty", [None, "", " "])
async def test_optional_field_empty_becomes_null(db_session, field, empty):
    await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row(**{field: empty})])

    assert errors == []
    key = {"Приоритет": "id_priorities", "Бригада": "id_group_type", "Полное описание": "description_full"}[field]
    assert valid_rows[0][key] is None


@pytest.mark.parametrize("empty", [None, "", " "])
async def test_empty_classifier_cell_creates_no_link(db_session, empty):
    ids = await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row(INSPECTION_TYPE=empty)])

    assert errors == []
    assert valid_rows[0]["specification_ids"] == [ids["spec_es1"]]


async def test_unknown_classifier_header(db_session):
    await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row(SIZE="Большой")])

    assert valid_rows == []
    assert error_fields(errors) == ["SIZE"]


async def test_classifier_of_another_classifier_type_is_not_used(db_session):
    await make_references(db_session)
    other_type = await add(db_session, ClassifierType(name="SEASON"))
    await add(db_session, Classifier(name="SIZE", id_classifier_type=other_type))

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row(SIZE="Большой")])

    assert error_fields(errors) == ["SIZE"]


async def test_specification_not_found(db_session):
    await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row(TRAIN_TYPE_MAINT="ЭС999")])

    assert valid_rows == []
    assert error_fields(errors) == ["TRAIN_TYPE_MAINT"]
    assert "ЭС999" in errors[0]["message"]


async def test_specification_of_another_classifier_is_not_matched(db_session):
    await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row(TRAIN_TYPE_MAINT="И5")])

    assert error_fields(errors) == ["TRAIN_TYPE_MAINT"]


async def test_deleted_specification_is_not_matched(db_session):
    ids = await make_references(db_session)
    await add(db_session, Specification(description="ЭС2ГП", id_classifier=ids["train_type_maint"],
                                        is_active=True, is_delete=True))

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row(TRAIN_TYPE_MAINT="ЭС2ГП")])

    assert error_fields(errors) == ["TRAIN_TYPE_MAINT"]


async def test_specification_found_twice_is_an_error(db_session):
    ids = await make_references(db_session)
    await add(db_session, Specification(description="ЭС1", id_classifier=ids["train_type_maint"],
                                        is_active=False, is_delete=False))

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row()])

    assert valid_rows == []
    assert error_fields(errors) == ["TRAIN_TYPE_MAINT"]


@pytest.mark.parametrize("raw, expected", [("Да", True), (" да ", True), ("Нет", False), ("нет", False),
                                           (None, False), ("", False)])
async def test_main_ptoir_flag(db_session, raw, expected):
    await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(
        db_session, [make_row(**{"Использовать техкарту только в БПТОиР?": raw})])

    assert errors == []
    assert valid_rows[0]["use_only_main_ptoir"] is expected


@pytest.mark.parametrize("raw", ["Yes", "1", "Дa-да"])
async def test_main_ptoir_flag_other_value_is_an_error(db_session, raw):
    await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(
        db_session, [make_row(**{"Использовать техкарту только в БПТОиР?": raw})])

    assert valid_rows == []
    assert error_fields(errors) == ["Использовать техкарту только в БПТОиР?"]


async def test_name_already_in_database(db_session):
    await make_references(db_session)
    await add(db_session, Techcard(name="I5_DESRUS"))

    errors, valid_rows = await validate_techcard_rows(db_session, [make_row()])

    assert valid_rows == []
    assert error_fields(errors) == ["Номер техкарты"]
    assert "I5_DESRUS" in errors[0]["message"]


async def test_name_repeated_in_file(db_session):
    await make_references(db_session)

    errors, valid_rows = await validate_techcard_rows(
        db_session, [make_row(), make_row(), make_row(**{"Номер техкарты": "I7_DESRUS"})])

    assert [(e["row"], e["field"]) for e in errors] == [(2, "Номер техкарты")]
    assert [vr["name"] for vr in valid_rows] == ["I5_DESRUS", "I7_DESRUS"]


async def test_headers_are_case_insensitive(db_session):
    ids = await make_references(db_session)
    row = {f" {k.upper()} ": v for k, v in make_row().items()}

    errors, valid_rows = await validate_techcard_rows(db_session, [row])

    assert errors == []
    assert valid_rows[0]["specification_ids"] == [ids["spec_es1"], ids["spec_i5"]]


@pytest.mark.parametrize("column", ["Номер техкарты", "Статус", "Название техкарты", "Полное описание", "Тип работы",
                                    "Приоритет", "Бригада", "Использовать техкарту только в БПТОиР?",
                                    "Классификация техкарты"])
async def test_missing_column(db_session, column):
    await make_references(db_session)
    row = make_row()
    del row[column]

    errors, valid_rows = await validate_techcard_rows(db_session, [row])

    assert valid_rows == []
    assert [(e["row"], e["field"]) for e in errors] == [(0, column)]


async def test_no_rows(db_session):
    assert await validate_techcard_rows(db_session, []) == ([], [])
