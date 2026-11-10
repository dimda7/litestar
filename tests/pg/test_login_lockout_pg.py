"""login_lockout against fdw_users, a postgres_fdw foreign table: only a real
database shows that UPDATE ... RETURNING goes through the foreign data wrapper.
The remote transaction is rolled back together with pg_session's."""

from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

import login_lockout
from models import User


async def user_with(session: AsyncSession, remaining: int, unlock_date: datetime | None) -> User:
    user_id = await session.scalar(text("SELECT id FROM public.fdw_users ORDER BY id LIMIT 1"))
    await session.execute(
        update(User).where(User.id == user_id).values(remaining_attempts=remaining, unlock_date=unlock_date)
    )
    user = await session.get(User, user_id, populate_existing=True)
    assert user is not None
    return user


async def stored(session: AsyncSession, user_id: int) -> tuple[int | None, datetime | None]:
    row = (await session.execute(select(User.remaining_attempts, User.unlock_date).where(User.id == user_id))).one()
    return row[0], row[1]


async def db_now(session: AsyncSession) -> datetime:
    now = await session.scalar(select(func.localtimestamp()))
    assert now is not None
    return now


async def test_wrong_password_takes_one_attempt(pg_session):
    user = await user_with(pg_session, 3, None)

    message = await login_lockout.record_wrong_password(pg_session, user.id, 15)

    assert message == "Неверный логин или пароль, осталось попыток: 2"
    assert await stored(pg_session, user.id) == (2, None)


async def test_last_wrong_password_starts_the_lock(pg_session):
    user = await user_with(pg_session, 1, None)

    message = await login_lockout.record_wrong_password(pg_session, user.id, 15)

    remaining, unlock_date = await stored(pg_session, user.id)
    assert remaining == 0
    assert unlock_date == await db_now(pg_session)
    assert message == login_lockout.locked_until(unlock_date + timedelta(minutes=15))


async def test_running_lock_refuses_without_writing(pg_session):
    since = await db_now(pg_session) - timedelta(minutes=5)
    user = await user_with(pg_session, 0, since)

    message = await login_lockout.check_before_password(pg_session, user, 5, 15)

    assert message == login_lockout.locked_until(since + timedelta(minutes=15))
    assert await stored(pg_session, user.id) == (0, since)


async def test_expired_lock_gives_the_attempts_back(pg_session):
    since = await db_now(pg_session) - timedelta(minutes=16)
    user = await user_with(pg_session, 0, since)

    assert await login_lockout.check_before_password(pg_session, user, 5, 15) is None
    assert await stored(pg_session, user.id) == (5, since)


async def test_lock_without_unlock_date_starts_counting(pg_session):
    user = await user_with(pg_session, 0, None)

    message = await login_lockout.check_before_password(pg_session, user, 5, 15)

    now = await db_now(pg_session)
    assert message == login_lockout.locked_until(now + timedelta(minutes=15))
    assert await stored(pg_session, user.id) == (0, now)


async def test_success_restores_attempts_and_keeps_unlock_date(pg_session):
    since = await db_now(pg_session) - timedelta(days=1)
    user = await user_with(pg_session, 2, since)

    await login_lockout.record_success(pg_session, user, 5)

    assert await stored(pg_session, user.id) == (5, since)
