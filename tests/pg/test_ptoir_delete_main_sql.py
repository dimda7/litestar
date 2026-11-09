"""'Удалить БПТОиР' against a real PostgreSQL copy.

The FKs into main_ptoir (ptoir and five link tables) and from main_ptoir to
main_ptoir_status only exist on the real schema, and they set the statement order.
The downloadable BEGIN; ... COMMIT; variant is not run — its COMMIT would end the
fixture's transaction.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.ptoir_parser.delete_main import validate_delete_main_rows
from sql_builders import ptoir as ptoir_sql
from tests.pg.conftest import run_generated_sql
from tests.pg.factories import TEST_PREFIX


async def insert(session: AsyncSession, sql: str, **params: object) -> int:
    return await session.scalar(text(f"{sql} RETURNING id"), params)


async def execute(session: AsyncSession, sql: str, **params: object) -> None:
    await session.execute(text(sql), params)


async def count(session: AsyncSession, sql: str, **params: object) -> int:
    return await session.scalar(text(f"SELECT count(*) FROM {sql}"), params)


async def make_main_ptoir(session: AsyncSession, number: str, id_config: int) -> dict[str, int]:
    """A БПТОиР with a status history, one ПТОиР (status, level warning, an order with and one without an asset)
    and every link."""
    # two status rows: main_ptoir points at the current one, the other is history linked by number only
    id_status = await insert(session, "INSERT INTO public.main_ptoir_status (number_ptoir) VALUES (:n)", n=number)
    await insert(session, "INSERT INTO public.main_ptoir_status (number_ptoir) VALUES (:n)", n=number)
    id_main = await insert(session, "INSERT INTO public.main_ptoir (number_ptoir, id_main_ptoir_status) "
                           "VALUES (:n, :s)", n=number, s=id_status)

    ptoir_number = f"{number}-ТО"
    id_ptoir_status = await insert(session, "INSERT INTO public.ptoir_status (number_ptoir) VALUES (:n)",
                                   n=ptoir_number)
    id_ptoir = await insert(session, "INSERT INTO public.ptoir (number_ptoir, id_main_ptoir, id_ptoir_status) "
                            "VALUES (:n, :m, :s)", n=ptoir_number, m=id_main, s=id_ptoir_status)
    await insert(session, "INSERT INTO public.ptoir_level_warning (id_ptoir) VALUES (:p)", p=id_ptoir)
    id_active, id_design_number, id_techcard = (await session.execute(text(
        "SELECT (SELECT min(id) FROM public.actives), (SELECT min(id) FROM public.design_number), "
        "(SELECT min(id) FROM public.techcard)"))).one()
    id_order = await insert(session, "INSERT INTO public.orders (order_number, id_ptoir) VALUES (:n, :p)",
                            n=f"{number}-ЗН", p=id_ptoir)
    id_order_with_active = await insert(
        session, "INSERT INTO public.orders (order_number, id_ptoir, id_active) VALUES (:n, :p, :a)",
        n=f"{number}-ЗН-актив", p=id_ptoir, a=id_active)
    await execute(session, "INSERT INTO public.actives_to_main_ptoir (id_main_ptoir, id_actives) VALUES (:m, :a)",
                  m=id_main, a=id_active)
    await execute(session, "INSERT INTO public.design_number_to_main_ptoir (id_main_ptoir, id_design_number) "
                  "VALUES (:m, :d)", m=id_main, d=id_design_number)
    await execute(session, "INSERT INTO public.main_ptoir_trigger (id, id_main_ptoir) "
                  "VALUES ((SELECT coalesce(max(id), 0) + 1 FROM public.main_ptoir_trigger), :m)", m=id_main)
    await execute(session, "INSERT INTO public.main_ptoir_to_config_counter (id_main_ptoir, id_main_ptoir_config) "
                  "VALUES (:m, :c)", m=id_main, c=id_config)
    id_interval = await insert(session, "INSERT INTO public.techcard_interval (id_techcard) VALUES (:t)",
                               t=id_techcard)
    await execute(session, "INSERT INTO public.main_ptoir_to_techcard_interval (id_main_ptoir, id_techcard_interval) "
                  "VALUES (:m, :i)", m=id_main, i=id_interval)
    return {"main": id_main, "ptoir": id_ptoir, "order": id_order, "order_with_active": id_order_with_active,
            "interval": id_interval,
            "active": id_active, "design_number": id_design_number, "techcard": id_techcard}


async def remaining(session: AsyncSession, number: str, ids: dict[str, int], id_config: int) -> dict[str, int]:
    return {
        "main_ptoir": await count(session, "public.main_ptoir WHERE id = :m", m=ids["main"]),
        "main_ptoir_status": await count(session, "public.main_ptoir_status WHERE number_ptoir = :n", n=number),
        "ptoir": await count(session, "public.ptoir WHERE id = :p", p=ids["ptoir"]),
        "ptoir_status": await count(session, "public.ptoir_status WHERE number_ptoir = :n", n=f"{number}-ТО"),
        "ptoir_level_warning": await count(session, "public.ptoir_level_warning WHERE id_ptoir = :p", p=ids["ptoir"]),
        "attached orders": await count(session, "public.orders WHERE id IN (:o, :a) AND id_ptoir IS NOT NULL",
                                       o=ids["order"], a=ids["order_with_active"]),
        "actives_to_main_ptoir": await count(session, "public.actives_to_main_ptoir WHERE id_main_ptoir = :m",
                                             m=ids["main"]),
        "design_number_to_main_ptoir": await count(
            session, "public.design_number_to_main_ptoir WHERE id_main_ptoir = :m", m=ids["main"]),
        "main_ptoir_trigger": await count(session, "public.main_ptoir_trigger WHERE id_main_ptoir = :m",
                                          m=ids["main"]),
        "main_ptoir_to_config_counter": await count(
            session, "public.main_ptoir_to_config_counter WHERE id_main_ptoir = :m", m=ids["main"]),
        "main_ptoir_to_techcard_interval": await count(
            session, "public.main_ptoir_to_techcard_interval WHERE id_main_ptoir = :m", m=ids["main"]),
        "techcard_interval": await count(session, "public.techcard_interval WHERE id = :i", i=ids["interval"]),
        "main_ptoir_config": await count(session, "public.main_ptoir_config WHERE id = :c", c=id_config),
    }


async def test_main_ptoir_is_deleted_with_its_ptoir_links_and_orphans(pg_session: AsyncSession) -> None:
    number, other = f"{TEST_PREFIX}-main-del", f"{TEST_PREFIX}-main-keep"
    own_config = await insert(pg_session, "INSERT INTO public.main_ptoir_config DEFAULT VALUES")
    shared_config = await insert(pg_session, "INSERT INTO public.main_ptoir_config DEFAULT VALUES")
    deleted = await make_main_ptoir(pg_session, number, own_config)
    kept = await make_main_ptoir(pg_session, other, shared_config)
    # a config and an interval still linked to a БПТОиР that stays must survive the NOT EXISTS guard
    await execute(pg_session, "INSERT INTO public.main_ptoir_to_config_counter (id_main_ptoir, id_main_ptoir_config) "
                  "VALUES (:m, :c)", m=deleted["main"], c=shared_config)
    await execute(pg_session, "INSERT INTO public.main_ptoir_to_techcard_interval "
                  "(id_main_ptoir, id_techcard_interval) VALUES (:m, :i)", m=deleted["main"], i=kept["interval"])

    errors, valid_rows = await validate_delete_main_rows(pg_session, [{"БПТОиР": number}])
    assert errors == []
    assert valid_rows[0]["ptoirs"][0]["order_numbers"] == [f"{number}-ЗН", f"{number}-ЗН-актив"]

    await run_generated_sql(pg_session, "\n".join(ptoir_sql.delete_main(valid_rows)))

    assert set((await remaining(pg_session, number, deleted, own_config)).values()) == {0}
    assert await count(pg_session, "public.orders WHERE id IN (:o, :a) AND id_ptoir IS NULL",
                       o=deleted["order"], a=deleted["order_with_active"]) == 2
    assert await count(pg_session, "public.main_ptoir_config WHERE id = :c", c=shared_config) == 1
    for table, id_ in (("actives", deleted["active"]), ("design_number", deleted["design_number"]),
                       ("techcard", deleted["techcard"])):
        assert await count(pg_session, f"public.{table} WHERE id = :i", i=id_) == 1

    kept_state = await remaining(pg_session, other, kept, shared_config)
    assert kept_state == {"main_ptoir": 1, "main_ptoir_status": 2, "ptoir": 1, "ptoir_status": 1,
                          "ptoir_level_warning": 1, "attached orders": 2, "actives_to_main_ptoir": 1,
                          "design_number_to_main_ptoir": 1, "main_ptoir_trigger": 1,
                          "main_ptoir_to_config_counter": 1, "main_ptoir_to_techcard_interval": 1,
                          "techcard_interval": 1, "main_ptoir_config": 1}
