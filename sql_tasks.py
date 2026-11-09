import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from litestar.response import Response
from sqlalchemy.ext.asyncio import AsyncSession

from db_manager import get_session_maker
from parser_storage import LOG_DIR
from progress_tasks import start_task

# valid_rows are whatever the validator hands its builder: dicts, or tuples for "Запуск ПТОиР".
Validator = Callable[[AsyncSession, list[dict], dict | None], Awaitable[tuple[list[dict], list]]]


@dataclass(frozen=True)
class SqlOperation:
    """One parser operation run through start_generate / start_execute.

    summary(rows in file, valid_rows, errors) gives the success message and the
    lines the log carries between its header and the executed SQL. With partial,
    row errors do not block the file: they are returned alongside the result.
    """
    validate: Validator
    build: Callable[[list], list[str]]
    log_name: str
    log_title: str
    summary: Callable[[int, list, list[dict]], tuple[str, list[str]]]
    empty_message: str
    logger: logging.Logger
    partial: bool = False


def _error(message: str) -> list[dict]:
    return [{"row": 0, "field": "*", "message": message}]


async def _validated(
    session: AsyncSession, rows: list[dict], op: SqlOperation, progress: dict,
) -> tuple[list[dict], list] | None:
    """(errors, valid_rows), or None once the failure is reported to progress."""
    try:
        errors, valid_rows = await op.validate(session, rows, progress)
    except Exception as e:
        progress.update(status="error", errors=_error(f"Ошибка валидации: {e}"))
        return None
    if errors and not op.partial:
        progress.update(status="error", errors=errors)
        return None
    if not valid_rows:
        progress.update(status="error", errors=[*_error(op.empty_message), *errors])
        return None
    return errors, valid_rows


def start_generate(data: dict, op: SqlOperation) -> Response:
    rows: list[dict] = json.loads(data.get("rows", "[]"))

    async def run(progress: dict) -> None:
        try:
            async with get_session_maker()() as session:
                validated = await _validated(session, rows, op, progress)
        except Exception as e:
            progress.update(status="error", errors=_error(f"Ошибка валидации: {e}"))
            return
        if validated is None:
            return
        errors, valid_rows = validated

        # a cell may hold a line break, which would end the comment and turn the rest into SQL
        skipped = [f"-- строка {e['row']} пропущена: {' '.join(e['message'].splitlines())}" for e in errors]
        full_sql = "\n".join([*skipped, "BEGIN;", *op.build(valid_rows), "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows), errors=errors)

    return start_task(len(rows), run)


def start_execute(data: dict, op: SqlOperation) -> Response:
    """Validate, run the builder's SQL in one transaction, then log it to log/<log_name>_<time>.log."""
    rows: list[dict] = json.loads(data.get("rows", "[]"))

    async def run(progress: dict) -> None:
        try:
            async with get_session_maker()() as session:
                validated = await _validated(session, rows, op, progress)
                if validated is None:
                    return
                errors, valid_rows = validated

                progress.update(processed=0, total=1, phase="executing")
                sql_lines = op.build(valid_rows)
                try:
                    # The raw asyncpg connection, as the tests run it: Excel text is
                    # inlined, and a ':1' in it must not be read as a bind parameter;
                    # a multi-statement body cannot go through a parameterised execute().
                    conn = await session.connection()
                    raw_conn = await conn.get_raw_connection()
                    await raw_conn.driver_connection.execute("\n".join(sql_lines))
                    progress["processed"] = 1
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    progress.update(status="error", errors=_error(f"Ошибка выполнения: {e}"))
                    return
        except Exception as e:
            progress.update(status="error", errors=_error(f"Ошибка выполнения: {e}"))
            return

        message, log_lines = op.summary(len(rows), valid_rows, errors)
        now = datetime.now()
        log_file = LOG_DIR / f"{op.log_name}_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join([
                f"=== Execute {op.log_title}: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
                *log_lines,
                "",
                *sql_lines,
                "",
            ]))
        op.logger.info("%s: %d rows, log: %s", op.log_name, len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows), errors=errors, message=message)

    return start_task(len(rows), run)
