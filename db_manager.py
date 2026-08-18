import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

import db_profiles

logger = logging.getLogger("db_manager")

# Id of the active connection (a uuid from db_profiles.json). An empty string
# means unresolved; get_active_profile() falls back to the first connection.
_active_profile: str = ""
_engines: dict[str, AsyncEngine] = {}
_session_makers: dict[str, async_sessionmaker[AsyncSession]] = {}

# Set to True only after a database has been chosen explicitly (the
# /auth/db-select page or Settings). While False the middleware sends every
# request to the database picker: there is physically nothing to validate a
# login against in fdw_users, since each database has its own users.
_connection_established: bool = False

# Epoch counter: incremented whenever the app starts looking at a different
# database. The active connection is process-wide while sessions are per user,
# so without this counter a switch logged out only whoever triggered it —
# everyone else kept working in the new database with a login that had been
# validated against the old one's fdw_users.
_target_epoch: int = 0

# Timeout for waiting on a free pooled connection / establishing the TCP one.
# Without it a stuck pool (an exhausted pgbouncer, say) blocks a request forever.
POOL_TIMEOUT_SECONDS = 10


def _get_session_maker(profile: str) -> async_sessionmaker[AsyncSession]:
    if profile not in _session_makers:
        stored = db_profiles.get(profile)
        if stored is None:
            raise ValueError(f"Неизвестное подключение к БД: {profile}")
        engine = create_async_engine(
            stored.url,
            pool_pre_ping=True,
            pool_timeout=POOL_TIMEOUT_SECONDS,
            connect_args={"timeout": POOL_TIMEOUT_SECONDS},
        )
        _engines[profile] = engine
        _session_makers[profile] = async_sessionmaker(engine, expire_on_commit=False)
    return _session_makers[profile]


def get_active_profile() -> str:
    """Id of the active connection.

    Until something is chosen explicitly, the first connection in the file
    counts as active — there is no "default profile" constant any more.
    """
    global _active_profile

    if not _active_profile:
        profiles = db_profiles.load()
        if profiles:
            _active_profile = profiles[0].id
    return _active_profile


def get_active_label() -> str:
    """Display name of the active connection (the user has no use for the id)."""
    active = db_profiles.get(get_active_profile())
    return active.name if active else ""


def has_active_connection() -> bool:
    return _connection_established


def get_target_epoch() -> int:
    return _target_epoch


def bump_target_epoch() -> None:
    """Invalidate every issued session — the database has changed."""
    global _target_epoch

    _target_epoch += 1
    logger.info("DB target epoch bumped to %s", _target_epoch)


def set_active_profile(profile: str) -> bool:
    """Make a connection the active one.

    Returns True if the active connection actually changed — callers use that
    to decide whether to drop the current login session, since different
    databases have different users.
    """
    global _active_profile, _connection_established

    if db_profiles.get(profile) is None:
        raise ValueError(f"Неизвестное подключение к БД: {profile}")

    changed = profile != get_active_profile()
    _get_session_maker(profile)

    _active_profile = profile
    _connection_established = True
    if changed:
        bump_target_epoch()
    logger.info("Active DB profile switched to %s", profile)
    return changed


async def forget_engine(profile: str) -> None:
    """Discard the cached engine — the connection parameters changed.

    Without this an edit to the host or password has no effect: the next
    request picks up the cached engine built from the old URL.
    """
    engine = _engines.pop(profile, None)
    _session_makers.pop(profile, None)
    if engine is not None:
        await engine.dispose()


async def test_connection(profile: str) -> tuple[bool, str]:
    """Trial connection that leaves the active connection untouched."""
    stored = db_profiles.get(profile)
    if stored is None:
        return False, f"Неизвестное подключение к БД: {profile}"
    return await test_url(stored.url)


async def test_url(url: str) -> tuple[bool, str]:
    """Trial connection to an arbitrary URL — for parameters not yet saved."""
    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        connect_args={"timeout": POOL_TIMEOUT_SECONDS},
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, "Подключение успешно"
    except Exception as e:
        return False, str(e)
    finally:
        await engine.dispose()


async def provide_db_session() -> AsyncGenerator[AsyncSession, None]:
    session_maker = _get_session_maker(get_active_profile())
    async with session_maker() as session:
        yield session


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """For background tasks outside DI (a long parse reporting progress, say)."""
    return _get_session_maker(get_active_profile())


async def dispose_all() -> None:
    for engine in _engines.values():
        await engine.dispose()
