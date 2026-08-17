import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

import db_profiles

logger = logging.getLogger("db_manager")

# Идентификатор активного подключения (uuid из db_profiles.json). Пустая строка —
# ещё не разрешён; get_active_profile() подставит первое подключение из файла.
_active_profile: str = ""
_engines: dict[str, AsyncEngine] = {}
_session_makers: dict[str, async_sessionmaker[AsyncSession]] = {}

# Ставится в True только после явного выбора БД (страница /auth/db-select или
# Настройки). Пока False — миддлварь гонит любой запрос на выбор БД, логин
# по fdw_users иначе физически не по чему проверять (у каждой БД свои
# пользователи).
_connection_established: bool = False

# Номер «эпохи»: растёт каждый раз, когда приложение начинает смотреть в другую
# БД. Активное подключение общее на весь процесс, а сессии — у каждого свои,
# поэтому без этого счётчика переключение выбрасывало из системы только того,
# кто его выполнил: остальные продолжали работать в новой БД с логином,
# проверенным по fdw_users старой.
_target_epoch: int = 0

# Таймаут ожидания свободного соединения из пула / установления TCP-соединения.
# Без него зависший пул (например, исчерпанный pgbouncer) блокирует запрос навсегда.
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
    """Идентификатор активного подключения.

    Пока явного выбора не было, активным считается первое подключение из
    файла — константы «профиля по умолчанию» больше нет.
    """
    global _active_profile

    if not _active_profile:
        profiles = db_profiles.load()
        if profiles:
            _active_profile = profiles[0].id
    return _active_profile


def get_active_label() -> str:
    """Отображаемое имя активного подключения (id пользователю не нужен)."""
    active = db_profiles.get(get_active_profile())
    return active.name if active else ""


def has_active_connection() -> bool:
    return _connection_established


def get_target_epoch() -> int:
    return _target_epoch


def bump_target_epoch() -> None:
    """Объявляет все выданные сессии недействительными — БД сменилась."""
    global _target_epoch

    _target_epoch += 1
    logger.info("DB target epoch bumped to %s", _target_epoch)


def set_active_profile(profile: str) -> bool:
    """Делает подключение активным.

    Возвращает True, если активное подключение реально изменилось (было
    другим) — вызывающий код использует это, чтобы решить, сбрасывать ли
    текущую сессию логина (у разных БД разные пользователи).
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
    """Выбрасывает закэшированный движок — параметры подключения изменились.

    Без этого правка host/пароля не подействует: следующий запрос возьмёт из
    кэша движок, собранный по старому URL.
    """
    engine = _engines.pop(profile, None)
    _session_makers.pop(profile, None)
    if engine is not None:
        await engine.dispose()


async def test_connection(profile: str) -> tuple[bool, str]:
    """Пробное подключение без переключения активного соединения."""
    stored = db_profiles.get(profile)
    if stored is None:
        return False, f"Неизвестное подключение к БД: {profile}"
    return await test_url(stored.url)


async def test_url(url: str) -> tuple[bool, str]:
    """Пробное подключение по произвольному URL — для ещё не сохранённых параметров."""
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
    """Для фоновых задач вне DI (например, длительный парсинг с отчётом о прогрессе)."""
    return _get_session_maker(get_active_profile())


async def dispose_all() -> None:
    for engine in _engines.values():
        await engine.dispose()
