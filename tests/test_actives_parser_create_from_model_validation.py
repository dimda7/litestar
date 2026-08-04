from controllers.actives_parser import ActivesParserController, _parse_model_lcn
from models import IteratorNumberLast
from tests.conftest import make_design_number

controller = ActivesParserController(owner=None)


async def make_counter(db_session, number: int = 100) -> None:
    db_session.add(IteratorNumberLast(id=2, number=number, description="Номер следующего актива"))
    await db_session.flush()


BASE_ROW = {"АРТИКУЛ": "A2V00001789551", "Тип актива": "SPV", "Серийный номер": "none", "lsn": "M9.1.6.4"}


def test_parse_model_lcn_with_path():
    assert _parse_model_lcn("M9.1.6.4") == (9, "1.6.4")


def test_parse_model_lcn_without_path():
    assert _parse_model_lcn("M9") == (9, "")


def test_parse_model_lcn_invalid():
    assert _parse_model_lcn("no-digits-here") is None


async def test_tmc_column_missing(db_session):
    await make_counter(db_session)

    rows = [{"Другое": "x", "lsn": "M9.1.6.4"}]
    errors, valid_rows, skipped = await controller._validate_create_active_from_model_rows(db_session, rows)

    assert valid_rows == []
    assert skipped == 0
    assert "не найдена колонка 'АРТИКУЛ'" in errors[0]["message"]


async def test_lsn_column_missing(db_session):
    await make_counter(db_session)

    rows = [{"АРТИКУЛ": "A2V00001789551", "Другое": "x"}]
    errors, valid_rows, skipped = await controller._validate_create_active_from_model_rows(db_session, rows)

    assert valid_rows == []
    assert "не найдена колонка 'lsn'" in errors[0]["message"]


async def test_counter_missing(db_session):
    rows = [dict(BASE_ROW)]
    errors, valid_rows, skipped = await controller._validate_create_active_from_model_rows(db_session, rows)

    assert valid_rows == []
    assert "Не найден счётчик" in errors[0]["message"]


async def test_empty_type_active(db_session):
    await make_counter(db_session)

    rows = [{**BASE_ROW, "Тип актива": ""}]
    errors, valid_rows, skipped = await controller._validate_create_active_from_model_rows(db_session, rows)

    assert valid_rows == []
    assert errors[0]["field"] == "Тип актива"
    assert "пустое" in errors[0]["message"]


async def test_type_active_too_long(db_session):
    await make_counter(db_session)

    rows = [{**BASE_ROW, "Тип актива": "TOOLONGPREFIX"}]
    errors, valid_rows, skipped = await controller._validate_create_active_from_model_rows(db_session, rows)

    assert valid_rows == []
    assert "слишком длинный" in errors[0]["message"]


async def test_empty_lsn(db_session):
    await make_counter(db_session)

    rows = [{**BASE_ROW, "lsn": ""}]
    errors, valid_rows, skipped = await controller._validate_create_active_from_model_rows(db_session, rows)

    assert valid_rows == []
    assert "Пустой lsn" in errors[0]["message"]


async def test_unparseable_lsn(db_session):
    await make_counter(db_session)

    rows = [{**BASE_ROW, "lsn": "no-digits-here"}]
    errors, valid_rows, skipped = await controller._validate_create_active_from_model_rows(db_session, rows)

    assert valid_rows == []
    assert "Не удалось распознать id_train_type" in errors[0]["message"]


async def test_tmc_not_found(db_session):
    await make_counter(db_session)

    rows = [dict(BASE_ROW)]
    errors, valid_rows, skipped = await controller._validate_create_active_from_model_rows(db_session, rows)

    assert valid_rows == []
    assert errors[0]["field"] == "АРТИКУЛ"
    assert "ТМЦ не найдена" in errors[0]["message"]


async def test_empty_row_skipped_silently(db_session):
    await make_counter(db_session)
    await make_design_number(db_session, "A2V00001789551")

    rows = [{**BASE_ROW, "АРТИКУЛ": ""}]
    errors, valid_rows, skipped = await controller._validate_create_active_from_model_rows(db_session, rows)

    assert errors == []
    assert valid_rows == []
    assert skipped == 0


def test_build_sql_body_single_active():
    valid_rows = [{
        "row_num": 1, "id_design_number": 55, "type_active": "SPV",
        "id_train": 700, "car_number": 1, "id_car_place": 12,
        "lcn": "700.1.6.4", "serial_number": "none",
    }]
    lines = ActivesParserController._build_create_active_from_model_sql_body(valid_rows)
    sql = "\n".join(lines)

    assert lines[0] == "DO $$"
    assert "loc_ids bigint[] := ARRAY(SELECT nextval('public.location_id_seq') FROM generate_series(1, 1));" in sql
    assert "act_ids bigint[] := ARRAY(SELECT nextval('public.actives_id_seq') FROM generate_series(1, 1));" in sql
    assert "SELECT number INTO active_num FROM public.iterator_number_last" in sql
    assert "FOR UPDATE;" in sql
    assert "active_num := active_num + 1;" in sql
    assert ("INSERT INTO public.location (id, id_type_location, id_train, car_number, id_car_place) "
            "VALUES (loc_ids[1], 2, 700, 1, 12);") in sql
    assert ("INSERT INTO public.actives (id, active_number, id_design_number, id_location, "
            "serial_number, lcn) VALUES (act_ids[1], 'SPV' || lpad(active_num::text, 7, '0'), 55, "
            "loc_ids[1], 'none', '700.1.6.4'::ltree);") in sql
    assert "UPDATE public.iterator_number_last SET number = active_num" in sql
    assert lines[-1] == "END $$;"
    # в отличие от create-actives здесь нет складского счётчика storage.last_lcn
    assert "storage" not in sql.lower()


def test_build_sql_body_null_serial_and_car_place():
    valid_rows = [{
        "row_num": 1, "id_design_number": 55, "type_active": "SPV",
        "id_train": 700, "car_number": None, "id_car_place": None,
        "lcn": "700", "serial_number": None,
    }]
    sql = "\n".join(ActivesParserController._build_create_active_from_model_sql_body(valid_rows))

    assert "VALUES (loc_ids[1], 2, 700, NULL, NULL);" in sql
    assert "NULL, '700'::ltree);" in sql


def test_build_sql_body_multiple_actives_increment_counter():
    valid_rows = [
        {"row_num": 1, "id_design_number": 1, "type_active": "SPV", "id_train": 700,
         "car_number": 1, "id_car_place": 12, "lcn": "700.1.6.4", "serial_number": "none"},
        {"row_num": 1, "id_design_number": 1, "type_active": "SPV", "id_train": 701,
         "car_number": 1, "id_car_place": 12, "lcn": "701.1.6.4", "serial_number": "none"},
    ]
    lines = ActivesParserController._build_create_active_from_model_sql_body(valid_rows)
    sql = "\n".join(lines)

    assert sql.count("active_num := active_num + 1;") == 2
    assert "loc_ids[1]" in sql and "loc_ids[2]" in sql
    assert "act_ids[1]" in sql and "act_ids[2]" in sql
    assert "generate_series(1, 2)" in sql
