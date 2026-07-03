"""Тесты FK/UNIQUE-валидации ParserController._validate_and_build_rows (controllers/parser.py)."""

from models import Models
from controllers.parser import ParserController
from tests.conftest import make_car_place, make_design_number, make_train_type


def make_row(model="", position="", itemnum="", lsn="", is_default=False) -> dict:
    return {
        "model": model,
        "position": position,
        "itemnum": itemnum,
        "lsn": lsn,
        "isdefault": "true" if is_default else "false",
    }


async def validate(db_session, rows):
    controller = ParserController(owner=None)
    return await controller._validate_and_build_rows(db_session, rows)


def error_fields(errors: list[dict]) -> list[str]:
    return [e["field"] for e in errors]


async def test_valid_row_passes(db_session):
    tt_id = await make_train_type(db_session, "Ласточка")
    cp_id = await make_car_place(db_session, "Вагон 1")
    dn_id = await make_design_number(db_session, "DN-001")

    errors, valid_rows = await validate(
        db_session, [make_row("Ласточка", "Вагон 1", "DN-001", lsn="M1.1")]
    )

    assert errors == []
    assert valid_rows == [(tt_id, cp_id, dn_id, "M1.1", False)]


async def test_missing_train_type_reported(db_session):
    await make_car_place(db_session, "Вагон 1")
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await validate(
        db_session, [make_row("Неизвестный поезд", "Вагон 1", "DN-001")]
    )

    assert valid_rows == []
    assert error_fields(errors) == ["model"]
    assert "train_type не найден" in errors[0]["message"]


async def test_missing_car_place_reported(db_session):
    await make_train_type(db_session, "Ласточка")
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await validate(
        db_session, [make_row("Ласточка", "Неизвестный вагон", "DN-001")]
    )

    assert valid_rows == []
    assert error_fields(errors) == ["position"]
    assert "car_place не найден" in errors[0]["message"]


async def test_ambiguous_car_place_reported(db_session):
    """Регрессия: car_place.name неуникален в БД -> раньше падало MultipleResultsFound."""
    await make_train_type(db_session, "Ласточка")
    await make_car_place(db_session, "Вагон 1")
    await make_car_place(db_session, "Вагон 1")  # дубликат имени, другой id
    await make_design_number(db_session, "DN-001")

    errors, valid_rows = await validate(
        db_session, [make_row("Ласточка", "Вагон 1", "DN-001")]
    )

    assert valid_rows == []
    assert error_fields(errors) == ["position"]
    assert "неоднозначен" in errors[0]["message"]


async def test_missing_design_number_reported(db_session):
    await make_train_type(db_session, "Ласточка")
    await make_car_place(db_session, "Вагон 1")

    errors, valid_rows = await validate(
        db_session, [make_row("Ласточка", "Вагон 1", "DN-999")]
    )

    assert valid_rows == []
    assert error_fields(errors) == ["itemnum"]
    assert "design_number не найден" in errors[0]["message"]


async def test_duplicate_against_existing_db_row(db_session):
    tt_id = await make_train_type(db_session, "Ласточка")
    cp_id = await make_car_place(db_session, "Вагон 1")
    dn_id = await make_design_number(db_session, "DN-001")
    db_session.add(Models(id_train_type=tt_id, lcn="M1.1", id_car_place=cp_id,
                           id_design_number=dn_id, is_default=False))
    await db_session.flush()

    errors, valid_rows = await validate(
        db_session, [make_row("Ласточка", "Вагон 1", "DN-001", lsn="M1.1")]
    )

    assert valid_rows == []
    assert error_fields(errors) == ["*"]
    assert "Дубликат" in errors[0]["message"]


async def test_duplicate_within_batch(db_session):
    tt_id = await make_train_type(db_session, "Ласточка")
    cp_id = await make_car_place(db_session, "Вагон 1")
    dn_id = await make_design_number(db_session, "DN-001")

    rows = [
        make_row("Ласточка", "Вагон 1", "DN-001", lsn="M1.1"),
        make_row("Ласточка", "Вагон 1", "DN-001", lsn="M1.1"),
    ]
    errors, valid_rows = await validate(db_session, rows)

    assert valid_rows == [(tt_id, cp_id, dn_id, "M1.1", False)]
    assert len(errors) == 1
    assert errors[0]["row"] == 2
    assert "Дубликат" in errors[0]["message"]


async def test_unique_conflict_lcn_car_place_default_against_existing(db_session):
    tt1 = await make_train_type(db_session, "Ласточка")
    tt2 = await make_train_type(db_session, "Финист")
    cp_id = await make_car_place(db_session, "Вагон 1")
    dn1 = await make_design_number(db_session, "DN-001")
    dn2 = await make_design_number(db_session, "DN-002")
    db_session.add(Models(id_train_type=tt1, lcn="M1.1", id_car_place=cp_id,
                           id_design_number=dn1, is_default=True))
    await db_session.flush()

    # Другая (train_type, design_number), но тот же (lcn, car_place) и is_default=True
    errors, valid_rows = await validate(
        db_session, [make_row("Финист", "Вагон 1", "DN-002", lsn="M1.1", is_default=True)]
    )

    assert valid_rows == []
    assert error_fields(errors) == ["lcn"]
    assert "unique (lcn, car_place)" in errors[0]["message"]
    assert tt2 and dn2  # использованы для построения непересекающегося full_tuple


async def test_unique_conflict_lcn_car_place_default_within_batch(db_session):
    tt1 = await make_train_type(db_session, "Ласточка")
    tt2 = await make_train_type(db_session, "Финист")
    cp_id = await make_car_place(db_session, "Вагон 1")
    dn1 = await make_design_number(db_session, "DN-001")
    dn2 = await make_design_number(db_session, "DN-002")

    rows = [
        make_row("Ласточка", "Вагон 1", "DN-001", lsn="M1.1", is_default=True),
        make_row("Финист", "Вагон 1", "DN-002", lsn="M1.1", is_default=True),
    ]
    errors, valid_rows = await validate(db_session, rows)

    assert valid_rows == [(tt1, cp_id, dn1, "M1.1", True)]
    assert len(errors) == 1
    assert errors[0]["row"] == 2
    assert "unique (lcn, car_place)" in errors[0]["message"]


async def test_unique_conflict_car_place_train_type_design_against_existing(db_session):
    tt_id = await make_train_type(db_session, "Ласточка")
    cp_id = await make_car_place(db_session, "Вагон 1")
    dn_id = await make_design_number(db_session, "DN-001")
    db_session.add(Models(id_train_type=tt_id, lcn="M1.1", id_car_place=cp_id,
                           id_design_number=dn_id, is_default=True))
    await db_session.flush()

    # Тот же (car_place, train_type, design_number), но другой lcn и is_default=True
    errors, valid_rows = await validate(
        db_session, [make_row("Ласточка", "Вагон 1", "DN-001", lsn="M1.2", is_default=True)]
    )

    assert valid_rows == []
    assert error_fields(errors) == ["*"]
    assert "unique (car_place, train_type, design_number)" in errors[0]["message"]


async def test_unique_conflict_car_place_train_type_design_within_batch(db_session):
    tt_id = await make_train_type(db_session, "Ласточка")
    cp_id = await make_car_place(db_session, "Вагон 1")
    dn_id = await make_design_number(db_session, "DN-001")

    # Тот же (car_place, train_type, design_number), но разный lcn, оба is_default=True
    rows = [
        make_row("Ласточка", "Вагон 1", "DN-001", lsn="M1.1", is_default=True),
        make_row("Ласточка", "Вагон 1", "DN-001", lsn="M1.2", is_default=True),
    ]
    errors, valid_rows = await validate(db_session, rows)

    assert valid_rows == [(tt_id, cp_id, dn_id, "M1.1", True)]
    assert len(errors) == 1
    assert errors[0]["row"] == 2
    assert "unique (car_place, train_type, design_number)" in errors[0]["message"]


async def test_non_default_rows_skip_unique_default_checks(db_session):
    """Проверки (lcn,car_place) и (car_place,train_type,design_number) действуют только при is_default=True."""
    tt1 = await make_train_type(db_session, "Ласточка")
    tt2 = await make_train_type(db_session, "Финист")
    cp_id = await make_car_place(db_session, "Вагон 1")
    dn1 = await make_design_number(db_session, "DN-001")
    dn2 = await make_design_number(db_session, "DN-002")

    rows = [
        make_row("Ласточка", "Вагон 1", "DN-001", lsn="M1.1", is_default=False),
        make_row("Финист", "Вагон 1", "DN-002", lsn="M1.1", is_default=False),
    ]
    errors, valid_rows = await validate(db_session, rows)

    assert errors == []
    assert valid_rows == [
        (tt1, cp_id, dn1, "M1.1", False),
        (tt2, cp_id, dn2, "M1.1", False),
    ]


async def test_lcn_falls_back_to_lcn_key_when_lsn_absent(db_session):
    tt_id = await make_train_type(db_session, "Ласточка")
    cp_id = await make_car_place(db_session, "Вагон 1")
    dn_id = await make_design_number(db_session, "DN-001")

    row = {
        "model": "Ласточка",
        "position": "Вагон 1",
        "itemnum": "DN-001",
        "lcn": "M1.9",
        "isdefault": "false",
    }
    errors, valid_rows = await validate(db_session, [row])

    assert errors == []
    assert valid_rows == [(tt_id, cp_id, dn_id, "M1.9", False)]
