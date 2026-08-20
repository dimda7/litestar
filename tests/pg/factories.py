"""Synthetic rows for the real-database tests.

Everything here is created inside the test transaction and disappears with the
rollback. Reference data that the generated SQL only points at (train types,
design numbers, storages) is taken from the copy as it is — creating it would
mean reproducing half the schema's foreign keys.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TEST_PREFIX = "PGTEST"


async def reference_ids(session: AsyncSession) -> dict[str, int]:
    row = (await session.execute(text(
        "SELECT (SELECT id FROM public.train_type WHERE is_active ORDER BY id LIMIT 1) AS train_type,"
        "       (SELECT id_train_series FROM public.train_type WHERE is_active ORDER BY id LIMIT 1) AS train_series,"
        "       (SELECT id FROM public.design_number ORDER BY id LIMIT 1) AS design_number,"
        "       (SELECT id FROM public.storage ORDER BY id LIMIT 1) AS storage,"
        "       (SELECT id FROM public.consignment ORDER BY id LIMIT 1) AS consignment,"
        "       (SELECT id FROM public.car_place ORDER BY id LIMIT 1) AS car_place,"
        "       (SELECT id FROM public.unit_type ORDER BY id LIMIT 1) AS unit_type,"
        "       (SELECT id FROM public.fdw_users ORDER BY id LIMIT 1) AS user_id"
    ))).mappings().first()
    return dict(row)


async def next_id(session: AsyncSession, sequence: str) -> int:
    return (await session.execute(text(f"SELECT nextval('public.{sequence}')"))).scalar_one()


async def second_car_place_id(session: AsyncSession, exclude_id: int) -> int:
    return await session.scalar(
        text("SELECT id FROM public.car_place WHERE id != :exclude ORDER BY id LIMIT 1"),
        {"exclude": exclude_id},
    )


async def car_place_name(session: AsyncSession, id_car_place: int) -> str:
    return await session.scalar(
        text("SELECT name FROM public.car_place WHERE id = :id"), {"id": id_car_place}
    )


async def make_train(session: AsyncSession, id_train_type: int, name: str = f"{TEST_PREFIX}-train") -> int:
    id_train = await next_id(session, "train_id_seq")
    await session.execute(
        text("INSERT INTO public.train (id, id_train_type, name, is_active, is_delete) "
             "VALUES (:id, :type, :name, true, false)"),
        {"id": id_train, "type": id_train_type, "name": f"{name}-{id_train}"},
    )
    return id_train


async def make_location(session: AsyncSession, **columns) -> int:
    id_location = await next_id(session, "location_id_seq")
    names = ", ".join(columns)
    values = ", ".join(f":{c}" for c in columns)
    await session.execute(
        text(f"INSERT INTO public.location (id{', ' + names if names else ''}) VALUES (:id{', ' + values if values else ''})"),
        {"id": id_location, **columns},
    )
    return id_location


async def make_active(
    session: AsyncSession, lcn: str, id_design_number: int, id_location: int | None = None,
    active_number: str | None = None, serial_number: str | None = None, id_unit_type: int | None = None,
) -> int:
    id_active = await next_id(session, "actives_id_seq")
    await session.execute(
        text("INSERT INTO public.actives (id, active_number, id_design_number, id_location, serial_number, "
             "id_unit_type, lcn) VALUES (:id, :number, :dn, :loc, :serial, :unit, CAST(:lcn AS ltree))"),
        {"id": id_active, "number": active_number or f"{TEST_PREFIX}{id_active}", "dn": id_design_number,
         "loc": id_location, "serial": serial_number, "unit": id_unit_type, "lcn": lcn},
    )
    return id_active


async def lcn_of(session: AsyncSession, id_active: int) -> str:
    return await session.scalar(text("SELECT lcn::text FROM public.actives WHERE id = :id"), {"id": id_active})
