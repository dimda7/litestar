"""'Удалить ПТОиР' against a real PostgreSQL copy.

The FKs that shape the statement order (orders.id_ptoir and ptoir_level_warning.id_ptoir
point at ptoir, ptoir.id_ptoir_status at ptoir_status) only exist on the real schema.
The downloadable BEGIN; ... COMMIT; variant is not run — its COMMIT would end the
fixture's transaction.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.ptoir_parser.delete import validate_delete_rows
from sql_builders import ptoir as ptoir_sql
from tests.pg.conftest import run_generated_sql
from tests.pg.factories import TEST_PREFIX


async def insert(session: AsyncSession, sql: str, **params: object) -> int:
    return await session.scalar(text(f"{sql} RETURNING id"), params)


async def make_ptoir(session: AsyncSession, number: str) -> int:
    # two status rows: ptoir points at the current one, the other is history linked by number only
    id_status = await insert(session, "INSERT INTO public.ptoir_status (number_ptoir) VALUES (:n)", n=number)
    await insert(session, "INSERT INTO public.ptoir_status (number_ptoir) VALUES (:n)", n=number)
    id_ptoir = await insert(session, "INSERT INTO public.ptoir (number_ptoir, id_ptoir_status) VALUES (:n, :s)",
                            n=number, s=id_status)
    await insert(session, "INSERT INTO public.ptoir_level_warning (id_ptoir) VALUES (:p)", p=id_ptoir)
    return id_ptoir


async def count(session: AsyncSession, sql: str, **params: object) -> int:
    return await session.scalar(text(f"SELECT count(*) FROM {sql}"), params)


async def test_ptoir_is_deleted_and_its_orders_are_detached(pg_session: AsyncSession) -> None:
    number = f"{TEST_PREFIX}-ptoir-del"
    other = f"{TEST_PREFIX}-ptoir-keep"
    id_ptoir = await make_ptoir(pg_session, number)
    id_other = await make_ptoir(pg_session, other)
    id_active = await pg_session.scalar(text("SELECT min(id) FROM public.actives"))
    with_active = await insert(pg_session, "INSERT INTO public.orders (order_number, id_ptoir, id_active) "
                               "VALUES (:n, :p, :a)", n=f"{TEST_PREFIX}-o1", p=id_ptoir, a=id_active)
    without_active = await insert(pg_session, "INSERT INTO public.orders (order_number, id_ptoir) VALUES (:n, :p)",
                                  n=f"{TEST_PREFIX}-o2", p=id_ptoir)

    errors, valid_rows = await validate_delete_rows(pg_session, [{"ПТОиР": number}])
    assert errors == []
    assert valid_rows[0]["order_numbers"] == [f"{TEST_PREFIX}-o1", f"{TEST_PREFIX}-o2"]

    await run_generated_sql(pg_session, "\n".join(ptoir_sql.delete(valid_rows)))

    assert await count(pg_session, "public.ptoir WHERE id = :p", p=id_ptoir) == 0
    assert await count(pg_session, "public.ptoir_level_warning WHERE id_ptoir = :p", p=id_ptoir) == 0
    assert await count(pg_session, "public.ptoir_status WHERE number_ptoir = :n", n=number) == 0
    assert (await pg_session.execute(text(
        "SELECT id_ptoir FROM public.orders WHERE id IN (:a, :b)"), {"a": with_active, "b": without_active})
    ).scalars().all() == [None, None]

    assert await count(pg_session, "public.ptoir WHERE id = :p", p=id_other) == 1
    assert await count(pg_session, "public.ptoir_level_warning WHERE id_ptoir = :p", p=id_other) == 1
    assert await count(pg_session, "public.ptoir_status WHERE number_ptoir = :n", n=other) == 2
