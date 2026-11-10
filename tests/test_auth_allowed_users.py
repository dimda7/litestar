import pytest

from controllers.auth import find_login_user
from models import User

ALLOWED = {"id_role": 6, "is_user": True, "id_employee_position": 1}


async def add_user(db_session, **fields) -> None:
    db_session.add(User(username="ivanov", **{**ALLOWED, **fields}))
    await db_session.flush()


async def test_user_matching_the_rule_is_found(db_session):
    await add_user(db_session)

    user = await find_login_user(db_session, "ivanov")

    assert user is not None and user.username == "ivanov"


@pytest.mark.parametrize(
    "fields",
    [
        {"id_role": 5},
        {"id_role": None},
        {"is_user": False},
        {"is_user": None},
        {"id_employee_position": 2},
        {"id_employee_position": None},
    ],
)
async def test_user_outside_the_rule_is_not_found(db_session, fields):
    await add_user(db_session, **fields)

    assert await find_login_user(db_session, "ivanov") is None
