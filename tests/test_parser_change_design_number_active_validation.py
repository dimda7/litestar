"""Validation tests for 'Изменить ТМЦ в активе по модели' (controllers/parser/change_design_number_active.py)."""

from controllers.parser.change_design_number_active import validate_change_design_number_active_rows
from models import Models
from tests.conftest import make_design_number, make_train


async def validate(db_session, rows):
    return await validate_change_design_number_active_rows(db_session, rows)


async def make_model(db_session, lcn: str, id_design_number: int | None = None) -> int:
    model = Models(id_train_type=9, lcn=lcn, id_car_place=1, id_design_number=id_design_number, is_default=True)
    db_session.add(model)
    await db_session.flush()
    return model.id


async def test_positions_come_from_the_model_lcn_on_every_train_of_its_type(db_session):
    old = await make_design_number(db_session, "KP00008506")
    new = await make_design_number(db_session, "KP00008505")
    first, second = await make_train(db_session, 9, "Поезд 1"), await make_train(db_session, 9, "Поезд 2")
    await make_train(db_session, 4, "Чужой тип")
    model = await make_model(db_session, "M9.1.9.2", old)

    errors, valid_rows = await validate(db_session, [{"id": str(model), "lsn": "M9.1.9.4", "itemnum": " KP00008506",
                                                      "new_itemnum": "KP00008505 "}])

    assert errors == []
    assert valid_rows == [{"lcn_trains": [f"{first}.1.9.2", f"{second}.1.9.2"], "old_id_design_number": old,
                           "id_design_number": new, "old_number": "KP00008506", "new_number": "KP00008505"}]


async def test_missing_columns_reported(db_session):
    results = [await validate(db_session, [row]) for row in (
        {"itemnum": "A", "new_itemnum": "B"}, {"id": "1", "new_itemnum": "B"}, {"id": "1", "itemnum": "A"})]

    assert [errors for errors, _ in results] == [
        [{"row": 0, "field": "id", "message": "В файле не найдена колонка 'id'"}],
        [{"row": 0, "field": "itemnum", "message": "В файле не найдена колонка 'itemnum'"}],
        [{"row": 0, "field": "new_itemnum", "message": "В файле не найдена колонка 'new_itemnum'"}],
    ]


async def test_bad_ids_reported(db_session):
    errors, valid_rows = await validate(db_session, [
        {"id": "", "itemnum": "A", "new_itemnum": "B"},
        {"id": "1.5", "itemnum": "A", "new_itemnum": "B"},
        {"id": "999", "itemnum": "A", "new_itemnum": "B"},
    ])

    assert valid_rows == []
    assert errors == [
        {"row": 1, "field": "id", "message": "Поле 'id' пустое"},
        {"row": 2, "field": "id", "message": "Некорректный id: '1.5'"},
        {"row": 3, "field": "id", "message": "Модель с id=999 не найдена"},
    ]


async def test_design_numbers_checked_and_no_op_rows_skipped(db_session):
    await make_design_number(db_session, "KP00008506")
    await make_design_number(db_session, "KP00008505")
    await make_train(db_session, 9)
    model = await make_model(db_session, "M9.1.9.2")
    orphan = Models(id_train_type=7, lcn="M7.1.1", id_car_place=1, is_default=True)
    db_session.add(orphan)
    await db_session.flush()

    errors, valid_rows = await validate(db_session, [
        {"id": str(model), "itemnum": "", "new_itemnum": "KP00008505"},
        {"id": str(model), "itemnum": "KP00008506", "new_itemnum": " "},
        {"id": str(model), "itemnum": "KP-OLD?", "new_itemnum": "KP00008505"},
        {"id": str(model), "itemnum": "KP00008506", "new_itemnum": "KP-NEW?"},
        {"id": str(model), "itemnum": "KP00008505", "new_itemnum": "KP00008505"},
        {"id": str(orphan.id), "itemnum": "KP00008506", "new_itemnum": "KP00008505"},
    ])

    assert valid_rows == []
    assert errors == [
        {"row": 1, "field": "itemnum", "message": "Поле 'itemnum' пустое"},
        {"row": 2, "field": "new_itemnum", "message": "Поле 'new_itemnum' пустое"},
        {"row": 3, "field": "itemnum", "message": "ТМЦ не найден: 'KP-OLD?'"},
        {"row": 4, "field": "new_itemnum", "message": "ТМЦ не найден: 'KP-NEW?'"},
        {"row": 6, "field": "id", "message": "Поезда с id_train_type=7 не найдены"},
    ]


async def test_repeated_id_and_two_models_on_one_position_reported(db_session):
    await make_design_number(db_session, "KP00008506")
    await make_design_number(db_session, "KP00008505")
    await make_design_number(db_session, "KP00013484")
    await make_train(db_session, 9)
    model = await make_model(db_session, "M9.1.9.2")
    twin = Models(id_train_type=9, lcn="M9.1.9.2", id_car_place=2, is_default=False)
    db_session.add(twin)
    await db_session.flush()

    errors, valid_rows = await validate(db_session, [
        {"id": str(model), "itemnum": "KP00008506", "new_itemnum": "KP00008505"},
        {"id": str(model), "itemnum": "KP00008506", "new_itemnum": "KP00008505"},
        {"id": str(model), "itemnum": "KP00008506", "new_itemnum": "KP00013484"},
        {"id": str(twin.id), "itemnum": "KP00008506", "new_itemnum": "KP00013484"},
    ])

    assert len(valid_rows) == 1
    assert errors == [
        {"row": 3, "field": "id", "message": (
            f"Конфликт: id={model} уже сопоставлен другому ТМЦ ('KP00008505', а не 'KP00013484')")},
        {"row": 4, "field": "id", "message": (
            "Конфликт со строкой 1: те же активы (позиция 1.9.2, ТМЦ 'KP00008506') уже меняются")},
    ]


async def test_unparsable_model_lcn_reported(db_session):
    await make_design_number(db_session, "KP00008506")
    await make_design_number(db_session, "KP00008505")
    model = await make_model(db_session, "MX")

    errors, _ = await validate(db_session, [{"id": str(model), "itemnum": "KP00008506", "new_itemnum": "KP00008505"}])

    assert errors == [{"row": 1, "field": "id",
                       "message": f"Не удалось распознать id_train_type в lcn модели id={model}: 'MX'"}]


async def test_one_model_with_several_old_numbers_keeps_every_row(db_session):
    first_old = await make_design_number(db_session, "KP00008506")
    second_old = await make_design_number(db_session, "KP00008507")
    await make_design_number(db_session, "KP00008505")
    await make_train(db_session, 9)
    model = await make_model(db_session, "M9.1.9.2")

    errors, valid_rows = await validate(db_session, [
        {"id": str(model), "itemnum": "KP00008506", "new_itemnum": "KP00008505"},
        {"id": str(model), "itemnum": "KP00008507", "new_itemnum": "KP00008505"},
    ])

    assert errors == []
    assert [vr["old_id_design_number"] for vr in valid_rows] == [first_old, second_old]


async def test_chain_on_one_position_reported(db_session):
    await make_design_number(db_session, "KP-X")
    await make_design_number(db_session, "KP-Y")
    await make_design_number(db_session, "KP-Z")
    await make_train(db_session, 9)
    model = await make_model(db_session, "M9.1.9.2")
    twin = Models(id_train_type=9, lcn="M9.1.9.2", id_car_place=2, is_default=False)
    db_session.add(twin)
    await db_session.flush()

    errors, _ = await validate(db_session, [
        {"id": str(model), "itemnum": "KP-X", "new_itemnum": "KP-Y"},
        {"id": str(twin.id), "itemnum": "KP-Y", "new_itemnum": "KP-Z"},
    ])

    assert errors == [{"row": 2, "field": "id", "message": (
        "Цепочка со строкой 1 на позиции 1.9.2: ТМЦ 'KP-Y' там и ставится, и заменяется — разнесите по двум запускам")}]


async def test_no_op_row_still_pins_the_model_and_old_number(db_session):
    await make_design_number(db_session, "KP00008506")
    await make_design_number(db_session, "KP00008505")
    await make_train(db_session, 9)
    model = await make_model(db_session, "M9.1.9.2")

    errors, _ = await validate(db_session, [
        {"id": str(model), "itemnum": "KP00008506", "new_itemnum": "KP00008506"},
        {"id": str(model), "itemnum": "KP00008506", "new_itemnum": "KP00008505"},
        {"id": str(model), "itemnum": "KP00008506", "new_itemnum": ""},
    ])

    assert errors == [
        {"row": 2, "field": "id", "message": (
            f"Конфликт: id={model} уже сопоставлен другому ТМЦ ('KP00008506', а не 'KP00008505')")},
        {"row": 3, "field": "new_itemnum", "message": "Поле 'new_itemnum' пустое"},
    ]
