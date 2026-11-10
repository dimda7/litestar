"""Login lockout kept in fdw_users.remaining_attempts / unlock_date.

fdw_users is a postgres_fdw foreign table over the administration database, and
the main Grom system keeps the same two fields, so a lock set by either system
holds in both. The rules follow that system's data: remaining_attempts = 0 means
locked since unlock_date, and unlock_date stays in place after a successful
login. Time is the database's LOCALTIMESTAMP, never the app server's clock; it is
read first and bound as a parameter, because postgres_fdw only ships an UPDATE
whole to the remote side when every expression in it is shippable, and
LOCALTIMESTAMP is not.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Update, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import User

WRONG_CREDENTIALS = "Неверный логин или пароль"
BANNED = "Учётная запись заблокирована, обратитесь к администратору"


def locked_until(until: datetime) -> str:
    return f"Учётная запись заблокирована до {until:%H:%M}"


@dataclass(frozen=True)
class Gate:
    """What a login attempt may do before its password is checked.

    error refuses the attempt; start_lock starts the countdown of a lock that has
    no unlock_date (set by hand); restore gives an expired lock its attempts back.
    """

    error: str | None
    start_lock: bool = False
    restore: bool = False


def gate(remaining: int | None, unlock_date: datetime | None, now: datetime, lock_minutes: int) -> Gate:
    if remaining is None:
        return Gate(BANNED)
    if remaining > 0:
        return Gate(None)
    lock = timedelta(minutes=lock_minutes)
    if unlock_date is None:
        return Gate(locked_until(now + lock), start_lock=True)
    if now < unlock_date + lock:
        return Gate(locked_until(unlock_date + lock))
    return Gate(None, restore=True)


async def _now(session: AsyncSession) -> datetime:
    now = await session.scalar(select(func.localtimestamp()))
    assert now is not None
    return now


def _update(user_id: int) -> Update:
    return update(User).where(User.id == user_id).execution_options(synchronize_session=False)


async def check_before_password(session: AsyncSession, user: User, max_attempts: int, lock_minutes: int) -> str | None:
    """The refusal message while the user is locked, or None to go on to the password."""
    now = await _now(session)
    verdict = gate(user.remaining_attempts, user.unlock_date, now, lock_minutes)
    if verdict.start_lock:
        await session.execute(_update(user.id).where(User.unlock_date.is_(None)).values(unlock_date=now))
        await session.commit()
    elif verdict.restore:
        await session.execute(_update(user.id).values(remaining_attempts=max_attempts))
        await session.commit()
    return verdict.error


async def record_wrong_password(session: AsyncSession, user_id: int, lock_minutes: int) -> str:
    """Take one attempt away in a single statement (concurrent attempts cannot
    slip past the limit) and start the lock on the last one."""
    now = await _now(session)
    row = (
        await session.execute(
            _update(user_id)
            .where(User.remaining_attempts > 0)
            .values(
                remaining_attempts=User.remaining_attempts - 1,
                unlock_date=case((User.remaining_attempts <= 1, now), else_=User.unlock_date),
            )
            .returning(User.remaining_attempts, User.unlock_date)
        )
    ).one_or_none()
    await session.commit()
    if row is None:
        # a concurrent attempt took the last one
        return WRONG_CREDENTIALS
    remaining, unlock_date = row
    if remaining == 0:
        assert unlock_date is not None  # the CASE above sets it on the last attempt
        return locked_until(unlock_date + timedelta(minutes=lock_minutes))
    return f"{WRONG_CREDENTIALS}, осталось попыток: {remaining}"


async def record_success(session: AsyncSession, user: User, max_attempts: int) -> None:
    """Give the attempts back; unlock_date is left as the main system leaves it."""
    if user.remaining_attempts != max_attempts:
        await session.execute(_update(user.id).values(remaining_attempts=max_attempts))
        await session.commit()
