"""sql_tasks.execute_rows against a real PostgreSQL: the per_statement path goes
through asyncpg, whose rowcount the SQLite tests in tests/test_sql_tasks.py cannot vouch for."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from litestar.response import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import sql_tasks
from controllers.actives_parser.change_okz import CHANGE_OKZ
from progress_tasks import progress_response
from tests.pg.factories import (
    car_place_name,
    make_active,
    make_location,
    make_train,
    reference_ids,
    second_car_place_id,
)


async def settled(response: Response) -> dict:
    task_id = json.loads(response.content)["task_id"]
    while True:
        state = json.loads(progress_response(task_id).content)
        if state["status"] != "running":
            return state
        await asyncio.sleep(0.01)


@pytest.fixture
def run_in_test_session(monkeypatch: pytest.MonkeyPatch, pg_session: AsyncSession, tmp_path: Path) -> None:
    """Make sql_tasks work inside pg_session, whose transaction is always rolled back."""

    @asynccontextmanager
    async def session() -> AsyncIterator[AsyncSession]:
        yield pg_session

    monkeypatch.setattr(sql_tasks, "get_session_maker", lambda: session)
    monkeypatch.setattr(sql_tasks, "LOG_DIR", tmp_path)


async def test_change_okz_reports_the_rowcount_of_its_update(pg_session, run_in_test_session):
    ids = await reference_ids(pg_session)
    target_cp = await second_car_place_id(pg_session, ids["car_place"])
    id_train = await make_train(pg_session, ids["train_type"])
    numbers = []
    for suffix in ("1", "2"):
        location = await make_location(pg_session, id_type_location=2, id_train=id_train, id_car_place=ids["car_place"])
        id_active = await make_active(pg_session, f"{id_train}.{suffix}", ids["design_number"], location)
        numbers.append(
            await pg_session.scalar(text("SELECT active_number FROM public.actives WHERE id = :id"), {"id": id_active})
        )
    position = await car_place_name(pg_session, target_cp)

    state = await settled(sql_tasks.execute_rows([{"Актив": n, "position": position} for n in numbers], CHANGE_OKZ))

    assert state["status"] == "done", state
    assert state["message"] == "Изменено окз у активов: 2 (строк файла: 2)"
