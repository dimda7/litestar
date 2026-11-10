"""Validation tests for 'Изменить ТМЦ в модели' (controllers/parser/change_design_number.py)."""

from controllers.parser.change_design_number import validate_change_model_design_number_rows
from models import Models
from tests.conftest import make_design_number


async def validate(db_session, rows):
    return await validate_change_model_design_number_rows(db_session, rows)


async def make_model(db_session, id_design_number, lcn="M1.1", id_car_place=1, is_default=True, id_train_type=1):
    model = Models(
        id_train_type=id_train_type,
        lcn=lcn,
        id_car_place=id_car_place,
        id_design_number=id_design_number,
        is_default=is_default,
    )
    db_session.add(model)
    await db_session.flush()
    return model.id


async def test_model_gets_the_new_design_number(db_session):
    old = await make_design_number(db_session, "KP00008507")
    new = await make_design_number(db_session, "KP00008505")
    model = await make_model(db_session, old)

    errors, valid_rows = await validate(
        db_session, [{"id": str(model), "itemnum": "KP00008507", "new_itemnum": " KP00008505 "}]
    )

    assert errors == []
    assert valid_rows == [
        {
            "id": model,
            "id_design_number": new,
            "old_id_design_number": old,
            "old_number": "KP00008507",
            "new_number": "KP00008505",
        }
    ]


async def test_missing_columns_reported(db_session):
    no_id, _ = await validate(db_session, [{"new_itemnum": "KP1"}])
    no_new, _ = await validate(db_session, [{"ID": "1", "itemnum": "KP1"}])

    assert no_id == [{"row": 0, "field": "id", "message": "В файле не найдена колонка 'id'"}]
    assert no_new == [{"row": 0, "field": "new_itemnum", "message": "В файле не найдена колонка 'new_itemnum'"}]


async def test_bad_rows_reported(db_session):
    await make_design_number(db_session, "KP00008505")

    errors, valid_rows = await validate(
        db_session,
        [
            {"id": "", "new_itemnum": "KP00008505"},
            {"id": "abc", "new_itemnum": "KP00008505"},
            {"id": "999", "new_itemnum": "KP00008505"},
        ],
    )

    assert valid_rows == []
    assert errors == [
        {"row": 1, "field": "id", "message": "Поле 'id' пустое"},
        {"row": 2, "field": "id", "message": "Некорректный id: 'abc'"},
        {"row": 3, "field": "id", "message": "Модель с id=999 не найдена"},
    ]


async def test_new_itemnum_checked_and_itemnum_compared_with_the_db(db_session):
    old = await make_design_number(db_session, "KP00008507")
    new = await make_design_number(db_session, "KP00008505")
    first = await make_model(db_session, old, lcn="M1.1")
    second = await make_model(db_session, old, lcn="M1.2")
    third = await make_model(db_session, old, lcn="M1.3")
    unchanged = await make_model(db_session, new, lcn="M1.4", id_car_place=9)

    errors, valid_rows = await validate(
        db_session,
        [
            {"id": str(first), "itemnum": "KP00008507", "new_itemnum": ""},
            {"id": str(first), "itemnum": "KP00008507", "new_itemnum": "KP99"},
            {"id": str(second), "itemnum": "KP00008506", "new_itemnum": "KP00008505"},
            {"id": str(third), "itemnum": None, "new_itemnum": "KP00008505"},
            {"id": str(unchanged), "itemnum": "KP00008505", "new_itemnum": "KP00008505"},
        ],
    )

    assert [vr["id"] for vr in valid_rows] == [third]
    assert errors == [
        {"row": 1, "field": "new_itemnum", "message": "Поле 'new_itemnum' пустое"},
        {"row": 2, "field": "new_itemnum", "message": "ТМЦ не найден: 'KP99'"},
        {
            "row": 3,
            "field": "itemnum",
            "message": f"У модели id={second} в БД ТМЦ 'KP00008507', а в файле 'KP00008506'",
        },
    ]


async def test_repeated_id_counted_once_or_reported_as_conflict(db_session):
    old = await make_design_number(db_session, "KP00008507")
    await make_design_number(db_session, "KP00008505")
    await make_design_number(db_session, "KP00013484")
    model = await make_model(db_session, old)

    errors, valid_rows = await validate(
        db_session,
        [
            {"id": str(model), "new_itemnum": "KP00008505"},
            {"id": f"{model}.0", "new_itemnum": "KP00008505"},
            {"id": str(model), "new_itemnum": "KP00013484"},
        ],
    )

    assert [vr["id"] for vr in valid_rows] == [model]
    assert errors == [
        {
            "row": 3,
            "field": "id",
            "message": (f"Конфликт: id={model} уже сопоставлен другому ТМЦ ('KP00008505', а не 'KP00013484')"),
        }
    ]


async def test_default_index_conflict_with_the_db_reported(db_session):
    old = await make_design_number(db_session, "KP00008507")
    new = await make_design_number(db_session, "KP00008505")
    model = await make_model(db_session, old, lcn="M1.1")
    taken = await make_model(db_session, new, lcn="M1.2")
    other_place = await make_model(db_session, old, lcn="M1.3", id_car_place=2)
    not_default = await make_model(db_session, old, lcn="M1.4", is_default=False)

    errors, valid_rows = await validate(
        db_session,
        [
            {"id": str(model), "new_itemnum": "KP00008505"},
            {"id": str(other_place), "new_itemnum": "KP00008505"},
            {"id": str(not_default), "new_itemnum": "KP00008505"},
        ],
    )

    assert [vr["id"] for vr in valid_rows] == [other_place, not_default]
    assert errors == [
        {
            "row": 1,
            "field": "new_itemnum",
            "message": f"Конфликт с моделью id={taken}: такой ТМЦ уже стоит на этом месте",
        }
    ]


async def test_full_index_conflict_with_the_db_reported(db_session):
    old = await make_design_number(db_session, "KP00008507")
    new = await make_design_number(db_session, "KP00008505")
    model = await make_model(db_session, old, lcn="M1.1", is_default=False)
    twin = await make_model(db_session, new, lcn="M1.1", is_default=False)

    errors, _ = await validate(db_session, [{"id": str(model), "new_itemnum": "KP00008505"}])

    assert errors == [
        {
            "row": 1,
            "field": "new_itemnum",
            "message": f"Конфликт с моделью id={twin}: такой ТМЦ уже стоит на этом месте",
        }
    ]


async def test_conflict_between_rows_and_swaps_reported(db_session):
    a = await make_design_number(db_session, "KP-A")
    b = await make_design_number(db_session, "KP-B")
    await make_design_number(db_session, "KP-C")
    first = await make_model(db_session, a, lcn="M1.1")
    second = await make_model(db_session, b, lcn="M1.2")
    third = await make_model(db_session, a, lcn="M2.1", id_car_place=3)
    fourth = await make_model(db_session, b, lcn="M2.2", id_car_place=3)

    errors, _ = await validate(
        db_session,
        [
            {"id": str(first), "new_itemnum": "KP-B"},
            {"id": str(second), "new_itemnum": "KP-A"},
            {"id": str(third), "new_itemnum": "KP-C"},
            {"id": str(fourth), "new_itemnum": "KP-C"},
        ],
    )

    assert errors == [
        {
            "row": 1,
            "field": "new_itemnum",
            "message": f"Конфликт с моделью id={second}: такой ТМЦ уже стоит на этом месте",
        },
        {
            "row": 2,
            "field": "new_itemnum",
            "message": f"Конфликт с моделью id={first}: такой ТМЦ уже стоит на этом месте",
        },
        {"row": 4, "field": "new_itemnum", "message": "Конфликт со строкой 3: такой ТМЦ уже ставится на это место"},
    ]


async def test_fractional_and_non_finite_ids_reported(db_session):
    await make_design_number(db_session, "KP00008505")

    errors, _ = await validate(
        db_session, [{"id": "12.7", "new_itemnum": "KP00008505"}, {"id": "inf", "new_itemnum": "KP00008505"}]
    )

    assert errors == [
        {"row": 1, "field": "id", "message": "Некорректный id: '12.7'"},
        {"row": 2, "field": "id", "message": "Некорректный id: 'inf'"},
    ]


async def test_repeated_id_still_has_its_itemnum_checked(db_session):
    old = await make_design_number(db_session, "KP00008507")
    await make_design_number(db_session, "KP00008505")
    model = await make_model(db_session, old)

    errors, _ = await validate(
        db_session,
        [
            {"id": str(model), "itemnum": "KP00008507", "new_itemnum": "KP00008505"},
            {"id": str(model), "itemnum": "KP-STALE", "new_itemnum": "KP00008505"},
        ],
    )

    assert errors == [
        {"row": 2, "field": "itemnum", "message": f"У модели id={model} в БД ТМЦ 'KP00008507', а в файле 'KP-STALE'"}
    ]


async def test_model_without_a_design_number_shows_null(db_session):
    await make_design_number(db_session, "KP00008505")
    model = await make_model(db_session, None)

    errors, valid_rows = await validate(db_session, [{"id": str(model), "itemnum": "KP1", "new_itemnum": "KP00008505"}])
    _, ok = await validate(db_session, [{"id": str(model), "new_itemnum": "KP00008505"}])

    assert errors == [{"row": 1, "field": "itemnum", "message": f"У модели id={model} в БД ТМЦ NULL, а в файле 'KP1'"}]
    assert (ok[0]["old_number"], ok[0]["old_id_design_number"]) == (None, None)
