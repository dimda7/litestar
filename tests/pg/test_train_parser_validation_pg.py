"""TrainParserController._validate_train_rows on a real PostgreSQL: the happy path
goes through `lcn::text = :lcn` lookups (the model's car place, a parent asset
already in the DB), which the SQLite suite in tests/test_train_parser_validation.py
cannot parse."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.train_parser import TrainParserController
from tests.pg.factories import TEST_PREFIX, make_active, make_design_number, make_train, next_id, reference_ids


def file_row(active: str, lsn: str, itemnum: str, position: str = "", serial: str = "") -> dict:
    return {"Актив": active, "Сер": serial, "itemnum": itemnum, "lsn": lsn, "position": position}


async def make_model(
    session: AsyncSession, id_train_type: int, lcn: str, id_car_place: int, id_design_number: int
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.models (id, id_train_type, lcn, id_car_place, id_design_number, is_default) "
            "VALUES (:id, :tt, CAST(:lcn AS ltree), :cp, :dn, false)"
        ),
        {
            "id": await next_id(session, "models_id_seq"),
            "tt": id_train_type,
            "lcn": lcn,
            "cp": id_car_place,
            "dn": id_design_number,
        },
    )


async def test_valid_file_resolves_car_places_and_parents(pg_session):
    ids = await reference_ids(pg_session)
    tt = ids["train_type"]
    id_train = await make_train(pg_session, tt)
    itemnum = f"{TEST_PREFIX}-DN-{id_train}"
    id_dn = await make_design_number(pg_session, itemnum)
    await pg_session.execute(
        text("UPDATE public.design_number SET id_unit_type = :ut WHERE id = :id"), {"ut": ids["unit_type"], "id": id_dn}
    )
    # the car place of 361.97 comes from its model; 361.97.98 has no model
    await make_model(pg_session, tt, f"M{tt}.97", ids["car_place"], id_dn)
    # 361.96 is not in the file, its asset already is in the DB
    await make_active(pg_session, f"{id_train}.96", ids["design_number"], active_number=f"{TEST_PREFIX}-db-{id_train}")

    root, car, node, from_db, orphan = (f"{TEST_PREFIX}-{n}-{id_train}" for n in ("root", "car", "node", "fdb", "orph"))
    rows = [
        file_row(root, "361", itemnum),
        file_row(car, "361.97", itemnum, position="+100_(03)", serial="S-1"),
        file_row(node, "361.97.98", itemnum),
        file_row(from_db, "361.96.95", itemnum),
        file_row(orphan, "361.94.93", itemnum),
    ]

    errors, valid = await TrainParserController(owner=None)._validate_train_rows(pg_session, rows, tt, id_train)

    assert errors == []
    by_number = {r["active_number"]: r for r in valid}
    assert by_number[root] == {
        "active_number": root,
        "serial_number": "",
        "id_unit_type": ids["unit_type"],
        "id_design_number": id_dn,
        "car_number": None,
        "car_place_id": None,
        "lcn_new": str(id_train),
        "id_actives_parent": None,
        "is_root": True,
        "root_number": None,
    }
    assert by_number[car] == {
        "active_number": car,
        "serial_number": "S-1",
        "id_unit_type": ids["unit_type"],
        "id_design_number": id_dn,
        "car_number": 3,
        "car_place_id": ids["car_place"],
        "lcn_new": f"{id_train}.97",
        "id_actives_parent": root,
        "is_root": False,
        "root_number": root,
    }
    assert (by_number[node]["car_place_id"], by_number[node]["id_actives_parent"]) == (None, car)
    assert by_number[from_db]["id_actives_parent"] == f"{TEST_PREFIX}-db-{id_train}"
    assert by_number[orphan]["id_actives_parent"] is None
    assert {r["root_number"] for r in valid if not r["is_root"]} == {root}
