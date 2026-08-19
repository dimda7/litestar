"""Fixtures for the tests that run against a real PostgreSQL copy of grom.

Everything under tests/pg/ is marked `pg` and is skipped when no database is
reachable, so `pytest` stays runnable on a machine that only has the SQLite
suite. Point them at a database with TEST_DB_URL, or leave it unset to fall
back to the DB_*_MY set in .env (the grom copy). The production DB_* set is
deliberately never used: every test writes.
"""
import os
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import db_profiles

load_dotenv()


def _url() -> str | None:
    if os.environ.get("TEST_DB_URL"):
        return os.environ["TEST_DB_URL"]
    values = [os.environ.get(f"DB_{key}_MY") for key in ("HOST", "PORT", "USER", "PASSWORD", "NAME")]
    if not all(values):
        return None
    return db_profiles.build_url(*values)


_unreachable: str | None = None


@pytest_asyncio.fixture
async def pg_engine():
    """A fresh engine per test: asyncpg connections cannot cross event loops, and
    pytest-asyncio gives each test its own loop."""
    global _unreachable
    url = _url()
    if url is None:
        pytest.skip("no test database configured (TEST_DB_URL or the DB_*_MY set in .env)")
    if _unreachable:
        pytest.skip(_unreachable)

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as e:
        await engine.dispose()
        _unreachable = f"test database is not reachable: {e}"
        pytest.skip(_unreachable)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine):
    """A session inside a transaction that is always rolled back.

    join_transaction_mode="create_savepoint" keeps a commit() inside the code
    under test from ending the outer transaction, so a test may exercise the
    real execute paths and still leave the database untouched.
    """
    conn = await pg_engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()


async def run_generated_sql(session: AsyncSession, sql: str) -> None:
    """Run generated SQL the way the controllers do.

    A multi-statement body (a DO $$ block plus the statements around it) cannot
    go through a parameterised execute(), so the app hands it to the raw asyncpg
    connection — the tests must use the same path to prove that what the app
    sends really runs.
    """
    conn = await session.connection()
    raw_conn = await conn.get_raw_connection()
    await raw_conn.driver_connection.execute(sql)


def pytest_collection_modifyitems(items):
    """Mark the tests of this directory `pg`.

    The hook is global — pytest calls it once with every collected item, this
    conftest included — so the path check is what keeps the marker off the
    SQLite suite.
    """
    here = Path(__file__).parent
    for item in items:
        if Path(item.path).is_relative_to(here):
            item.add_marker(pytest.mark.pg)
