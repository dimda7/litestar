from datetime import date, datetime

from controllers.actives_parser import ActivesParserController
from models import Actives, CounterActive, Location, MileageStart, MileageTrain, Relocate
from tests.conftest import make_active, make_design_number

controller = ActivesParserController(owner=None)


async def test_design_number_old_column_name(db_session):
    await make_active(db_session, "ES1040010328")
    dn_id = await make_design_number(db_session, "A2V00002691454")

    rows = [{"Актив": "ES1040010328", "Новая Позиция ТМЦ": "A2V00002691454"}]
    errors, valid_rows = await controller._validate_design_number(db_session, rows)

    assert errors == []
    assert valid_rows == [("ES1040010328", dn_id, "A2V00002691454")]


async def test_design_number_new_column_name(db_session):
    await make_active(db_session, "ES1040010328")
    dn_id = await make_design_number(db_session, "A2V00002691454")

    rows = [{"Актив": "ES1040010328", "Новый ТМЦ номер": "A2V00002691454"}]
    errors, valid_rows = await controller._validate_design_number(db_session, rows)

    assert errors == []
    assert valid_rows == [("ES1040010328", dn_id, "A2V00002691454")]


async def test_design_number_column_missing(db_session):
    rows = [{"Актив": "ES1040010328", "Другая колонка": "x"}]
    errors, valid_rows = await controller._validate_design_number(db_session, rows)

    assert valid_rows == []
    assert len(errors) == 1
    assert "не найдена колонка" in errors[0]["message"]


async def test_design_number_new_column_empty_value(db_session):
    await make_active(db_session, "ES1040010328")

    rows = [{"Актив": "ES1040010328", "Новый ТМЦ номер": ""}]
    errors, valid_rows = await controller._validate_design_number(db_session, rows)

    assert valid_rows == []
    assert len(errors) == 1
    assert "пустое" in errors[0]["message"]


async def make_recount_active(
    db_session,
    active_number: str = "UL0000001",
    id_unit_type: int | None = None,
    with_mileage_start: bool = True,
    with_counter: bool = True,
) -> int:
    active = Actives(active_number=active_number, id_unit_type=id_unit_type)
    db_session.add(active)
    await db_session.flush()
    if with_mileage_start:
        db_session.add(MileageStart(id_active=active.id, milage=0, milage_const=100))
    if with_counter:
        db_session.add(CounterActive(
            id_active=active.id, id_counter_type=3, date=datetime(2023, 1, 1, 12, 0), value=0,
        ))
    await db_session.flush()
    return active.id


async def test_recount_mileage_column_missing(db_session):
    errors, valid_rows = await controller._validate_recount_mileage(db_session, [{"Другое": "x"}])

    assert valid_rows == []
    assert len(errors) == 1
    assert "не найдена колонка 'Актив'" in errors[0]["message"]


async def test_recount_mileage_active_not_found(db_session):
    errors, valid_rows = await controller._validate_recount_mileage(db_session, [{"Актив": "UL0000001"}])

    assert valid_rows == []
    assert len(errors) == 1
    assert "Актив не найден" in errors[0]["message"]


async def test_recount_mileage_missing_mileage_start(db_session):
    await make_recount_active(db_session, with_mileage_start=False)

    errors, valid_rows = await controller._validate_recount_mileage(db_session, [{"Актив": "UL0000001"}])

    assert valid_rows == []
    assert "mileage_start не найдена" in errors[0]["message"]


async def test_recount_mileage_missing_counter(db_session):
    await make_recount_active(db_session, with_counter=False)

    errors, valid_rows = await controller._validate_recount_mileage(db_session, [{"Актив": "UL0000001"}])

    assert valid_rows == []
    assert "Счётчик пробега" in errors[0]["message"]


async def test_recount_mileage_train_needs_no_counter(db_session):
    await make_recount_active(db_session, id_unit_type=1, with_counter=False)

    errors, valid_rows = await controller._validate_recount_mileage(db_session, [{"Актив": "UL0000001"}])

    assert errors == []
    assert len(valid_rows) == 1
    assert valid_rows[0]["is_train"] is True
    assert valid_rows[0]["total"] == 0


async def test_recount_mileage_total_from_relocate_history(db_session):
    active_id = await make_recount_active(db_session)

    loc_storage = Location(id_storage=1)
    loc_train = Location(id_train=5)
    db_session.add_all([loc_storage, loc_train])
    await db_session.flush()

    db_session.add_all([
        # склад -> поезд до отсечки: пробег поезда прибавляется
        Relocate(id_active=active_id, id_location_old=loc_storage.id,
                 id_location_new=loc_train.id, date=datetime(2022, 1, 10, 8, 0)),
        # перемещение после отсечки — игнорируется
        Relocate(id_active=active_id, id_location_old=loc_train.id,
                 id_location_new=loc_storage.id, date=datetime(2023, 1, 1, 8, 0)),
        MileageTrain(id_train=5, mileage_average=10, date_average=date(2022, 2, 1)),
        MileageTrain(id_train=5, mileage_average=15, date_average=date(2022, 3, 1)),
        # после отсечки — не суммируется
        MileageTrain(id_train=5, mileage_average=99, date_average=date(2022, 6, 1)),
        # чужой поезд — не суммируется
        MileageTrain(id_train=7, mileage_average=50, date_average=date(2022, 2, 1)),
    ])
    await db_session.flush()

    errors, valid_rows = await controller._validate_recount_mileage(db_session, [{"Актив": "UL0000001"}])

    assert errors == []
    assert len(valid_rows) == 1
    assert valid_rows[0]["total"] == 25
    assert valid_rows[0]["is_train"] is False


def test_recount_mileage_sql_body_order_and_trains():
    valid_rows = [
        {"row_num": 1, "active_number": "UL0000001", "id_active": 10, "total": 25, "is_train": False},
        {"row_num": 2, "active_number": "ES0000001", "id_active": 20, "total": -5, "is_train": True},
    ]
    lines = ActivesParserController._build_recount_mileage_sql_body(valid_rows)

    assert len(lines) == 3
    assert "UPDATE public.mileage_start SET milage = COALESCE(milage_const, 0) + (25)" in lines[0]
    assert "WHERE id_active = 10" in lines[0]
    assert "UPDATE public.mileage_start" in lines[1] and "(-5)" in lines[1]
    # счётчик пересчитывается только для не-поездов и только после mileage_start
    assert "UPDATE public.counter_active" in lines[2]
    assert "function_get_mileage" in lines[2]
    assert "id_active = 10" in lines[2]
