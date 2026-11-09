"""The shared generate/execute flow (sql_tasks.py), driven the way a page drives it:
start a task, then poll progress_response until it settles."""

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import text
from litestar.response import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import sql_tasks
from progress_tasks import progress_response


def rows_form(rows: list[dict]) -> dict:
    return {"rows": json.dumps(rows)}


async def settled(response: Response) -> dict:
    task_id = json.loads(response.content)["task_id"]
    while True:
        state = json.loads(progress_response(task_id).content)
        if state["status"] != "running":
            return state
        await asyncio.sleep(0.01)


def validator(errors: list[dict], valid_rows: list[dict]) -> sql_tasks.Validator:
    async def validate(session: AsyncSession, rows: list[dict], progress: dict | None) -> tuple[list[dict], list[dict]]:
        return errors, valid_rows
    return validate


def operation(validate: sql_tasks.Validator,
              build: Callable[[list[dict]], list[str]] = lambda valid_rows: [f"-- row {vr['n']}" for vr in valid_rows],
              **kwargs: object) -> sql_tasks.SqlOperation:
    return sql_tasks.SqlOperation(
        validate=validate, build=build, log_name="test_op", log_title="test op",
        summary=lambda total, valid_rows, errors: (f"Готово: {len(valid_rows)}", [f"Rows: {total}"]),
        empty_message="Нечего делать", logger=logging.getLogger("test"), **kwargs)


@pytest.fixture(autouse=True)
def sqlite_sessions(monkeypatch: pytest.MonkeyPatch, session_maker: async_sessionmaker, tmp_path: Path) -> None:
    monkeypatch.setattr(sql_tasks, "get_session_maker", lambda: session_maker)
    monkeypatch.setattr(sql_tasks, "LOG_DIR", tmp_path)


async def test_generate_wraps_the_builder_in_a_transaction():
    op = operation(validator([], [{"n": 1}, {"n": 2}]))

    state = await settled(sql_tasks.start_generate(rows_form([{}, {}]), op))

    assert state["status"] == "done"
    assert state["sql"] == "BEGIN;\n-- row 1\n-- row 2\nCOMMIT;"
    assert state["count"] == 2


ROW_ERROR = {"row": 2, "field": "ПТОиР", "message": "ПТОиР не найден: 'X'"}


async def test_generate_is_blocked_by_a_row_error():
    op = operation(validator([ROW_ERROR], [{"n": 1}]))

    state = await settled(sql_tasks.start_generate(rows_form([{}, {}]), op))

    assert state["status"] == "error"
    assert state["errors"] == [ROW_ERROR]
    assert "sql" not in state


async def test_partial_generate_comments_out_row_errors_and_returns_them():
    broken = {"row": 3, "field": "ПТОиР", "message": "ПТОиР не найден: 'A\nDROP TABLE x;'"}
    op = operation(validator([ROW_ERROR, broken], [{"n": 1}]), partial=True)

    state = await settled(sql_tasks.start_generate(rows_form([{}, {}, {}]), op))

    assert state["status"] == "done"
    assert state["sql"] == ("-- строка 2 пропущена: ПТОиР не найден: 'X'\n"
                            "-- строка 3 пропущена: ПТОиР не найден: 'A DROP TABLE x;'\n"
                            "BEGIN;\n-- row 1\nCOMMIT;")
    assert state["errors"] == [ROW_ERROR, broken]


@pytest.mark.parametrize("start", [sql_tasks.start_generate, sql_tasks.start_execute])
@pytest.mark.parametrize("partial, errors", [(False, []), (True, []), (True, [ROW_ERROR])])
async def test_nothing_valid_reports_the_empty_message(start, partial, errors):
    op = operation(validator(errors, []), partial=partial)

    state = await settled(start(rows_form([{}]), op))

    assert state["status"] == "error"
    assert state["errors"] == [{"row": 0, "field": "*", "message": "Нечего делать"}, *errors]


def insert_ptoir(valid_rows: list[dict]) -> list[str]:
    # one statement: the SQLite driver refuses a multi-statement batch
    values = ", ".join(f"('{vr['number']}')" for vr in valid_rows)
    return [f"INSERT INTO public.ptoir (number_ptoir) VALUES {values};"]


async def ptoir_numbers(session_maker: async_sessionmaker) -> list[str]:
    async with session_maker() as session:
        return (await session.execute(text("SELECT number_ptoir FROM public.ptoir ORDER BY id"))).scalars().all()


async def test_execute_commits_the_builder_sql_and_logs_it(session_maker, tmp_path):
    op = operation(validator([], [{"number": "ТО1"}, {"number": "ТО2"}]), build=insert_ptoir)

    state = await settled(sql_tasks.start_execute(rows_form([{}, {}, {}]), op))

    assert state["status"] == "done"
    assert state["message"] == "Готово: 2"
    assert await ptoir_numbers(session_maker) == ["ТО1", "ТО2"]
    [log_file] = tmp_path.glob("test_op_*.log")
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("=== Execute test op: ")
    assert lines[1:] == ["Rows: 3", "", "INSERT INTO public.ptoir (number_ptoir) VALUES ('ТО1'), ('ТО2');"]


async def test_failed_execute_rolls_back_and_writes_no_log(session_maker, tmp_path):
    async def validate_and_write(session: AsyncSession, rows: list[dict],
                                 progress: dict | None) -> tuple[list[dict], list[dict]]:
        await session.execute(text("INSERT INTO public.ptoir (number_ptoir) VALUES ('written by validation')"))
        return [], [{"n": 1}]

    op = operation(validate_and_write, build=lambda valid_rows: ["INSERT INTO public.no_such_table VALUES (1);"])

    state = await settled(sql_tasks.start_execute(rows_form([{}]), op))

    assert state["status"] == "error"
    assert state["errors"][0]["message"].startswith("Ошибка выполнения: ")
    assert await ptoir_numbers(session_maker) == []
    assert list(tmp_path.iterdir()) == []
