import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from config import DEFAULT_DB_PROFILE, settings

logger = logging.getLogger("db_manager")

_active_profile: str = DEFAULT_DB_PROFILE
_engines: dict[str, AsyncEngine] = {}
_session_makers: dict[str, async_sessionmaker[AsyncSession]] = {}

# Таймаут ожидания свободного соединения из пула / установления TCP-соединения.
# Без него зависший пул (например, исчерпанный pgbouncer) блокирует запрос навсегда.
POOL_TIMEOUT_SECONDS = 10


def _get_session_maker(profile: str) -> async_sessionmaker[AsyncSession]:
    if profile not in _session_makers:
        engine = create_async_engine(
            settings.db_profiles[profile].url,
            pool_pre_ping=True,
            pool_timeout=POOL_TIMEOUT_SECONDS,
            connect_args={"timeout": POOL_TIMEOUT_SECONDS},
        )
        _engines[profile] = engine
        _session_makers[profile] = async_sessionmaker(engine, expire_on_commit=False)
    return _session_makers[profile]


def get_active_profile() -> str:
    return _active_profile


def set_active_profile(profile: str) -> None:
    global _active_profile
    if profile not in settings.db_profiles:
        raise ValueError(f"Неизвестный профиль БД: {profile}")
    _active_profile = profile
    logger.info("Active DB profile switched to %s", profile)


async def test_connection(profile: str) -> tuple[bool, str]:
    """Пробное подключение к профилю БД без переключения активного соединения."""
    if profile not in settings.db_profiles:
        return False, f"Неизвестный профиль БД: {profile}"

    engine = create_async_engine(
        settings.db_profiles[profile].url,
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
    session_maker = _get_session_maker(_active_profile)
    async with session_maker() as session:
        yield session


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Для фоновых задач вне DI (например, длительный парсинг с отчётом о прогрессе)."""
    return _get_session_maker(_active_profile)


async def dispose_all() -> None:
    for engine in _engines.values():
        await engine.dispose()
