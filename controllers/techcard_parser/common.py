import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation

from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from litestar.response import Response
from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

from db_manager import get_session_maker
from models import (
    Classifier, ClassifierType, GroupType, Priorities, Specification, Techcard, TechcardStatusTypes,
    TechcardWorkOperation, WorkType,
)
from parser_storage import LOG_DIR
from progress_tasks import start_task

logger = logging.getLogger("techcard_parser")

PREFIX = "techcard_parser"
PATH = "/techcard-parser"

Validator = Callable[[AsyncSession, list[dict], dict | None], Awaitable[tuple[list[dict], list[dict]]]]


class WorkTypeRepository(SQLAlchemyAsyncRepository[WorkType]):
    model_type = WorkType


class GroupTypeRepository(SQLAlchemyAsyncRepository[GroupType]):
    model_type = GroupType


class PrioritiesRepository(SQLAlchemyAsyncRepository[Priorities]):
    model_type = Priorities


class ClassifierTypeRepository(SQLAlchemyAsyncRepository[ClassifierType]):
    model_type = ClassifierType


class ClassifierRepository(SQLAlchemyAsyncRepository[Classifier]):
    model_type = Classifier


class SpecificationRepository(SQLAlchemyAsyncRepository[Specification]):
    model_type = Specification


class TechcardStatusTypesRepository(SQLAlchemyAsyncRepository[TechcardStatusTypes]):
    model_type = TechcardStatusTypes


class TechcardRepository(SQLAlchemyAsyncRepository[Techcard]):
    model_type = Techcard


class TechcardWorkOperationRepository(SQLAlchemyAsyncRepository[TechcardWorkOperation]):
    model_type = TechcardWorkOperation


class RowError(Exception):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def header_columns(rows: list[dict]) -> dict[str, str]:
    return {str(h).strip().lower(): h for h in (rows[0] if rows else {})}


def missing_columns(rows: list[dict], columns: dict[str, str], expected: tuple[str, ...]) -> list[dict]:
    return [{"row": 0, "field": c, "message": f"В файле не найдена колонка '{c}'"}
            for c in expected if rows and c.lower() not in columns]


def cell(row: dict, columns: dict[str, str], name: str) -> str:
    value = row.get(columns.get(name.lower(), ""))
    return "" if value is None else str(value).strip()


def label(columns: dict[str, str], name: str) -> str:
    return str(columns.get(name.lower(), name)).strip()


def labelled_cell(row: dict, columns: dict[str, str], name: str) -> tuple[str, str]:
    return label(columns, name), cell(row, columns, name)


def require(row: dict, columns: dict[str, str], names: tuple[str, ...]) -> None:
    for name in names:
        if not cell(row, columns, name):
            raise RowError(label(columns, name), f"Поле '{label(columns, name)}' пустое")


async def resolve_rows(
    rows: list[dict], resolve: Callable[[dict], Awaitable[dict | None]], progress: dict | None,
) -> tuple[list[dict], list[dict]]:
    """Run resolve over every row: a RowError becomes an error, None skips the row."""
    errors: list[dict] = []
    valid_rows: list[dict] = []
    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")

    for idx, row in enumerate(rows):
        if progress is not None and (idx % 20 == 0 or idx + 1 == len(rows)):
            progress["processed"] = idx + 1
        try:
            valid_row = await resolve(row)
        except RowError as e:
            errors.append({"row": idx + 1, "field": e.field, "message": e.message})
            continue
        if valid_row is not None:
            valid_rows.append(valid_row)

    return errors, valid_rows


def parse_int(field: str, value: str) -> int:
    try:
        number = Decimal(value.replace(",", "."))
    except InvalidOperation:
        raise RowError(field, f"{field}: некорректное значение: '{value}'") from None
    if not number.is_finite() or number != number.to_integral_value():
        raise RowError(field, f"{field}: некорректное значение: '{value}'")
    return int(number)


async def one_id(repo: SQLAlchemyAsyncRepository, field: str, value: str, *filters: ColumnElement[bool]) -> int:
    found = await repo.get_many(*filters)
    if not found:
        raise RowError(field, f"{field}: значение не найдено: '{value}'")
    if len(found) > 1:
        raise RowError(field, f"{field}: значение '{value}' найдено в базе {len(found)} раз")
    return found[0].id


def _error(message: str) -> list[dict]:
    return [{"row": 0, "field": "*", "message": message}]


async def _validated(
    session: AsyncSession, rows: list[dict], validate: Validator, progress: dict,
) -> list[dict] | None:
    """The valid rows, or None once the errors are reported to progress."""
    try:
        errors, valid_rows = await validate(session, rows, progress)
    except Exception as e:
        progress.update(status="error", errors=_error(f"Ошибка валидации: {e}"))
        return None
    if errors:
        progress.update(status="error", errors=errors)
        return None
    if not valid_rows:
        progress.update(status="error", errors=_error("Нет строк для записи в базу"))
        return None
    return valid_rows


def start_generate(data: dict, validate: Validator, build: Callable[[list[dict]], list[str]]) -> Response:
    rows: list[dict] = json.loads(data.get("rows", "[]"))

    async def run(progress: dict) -> None:
        try:
            async with get_session_maker()() as session:
                valid_rows = await _validated(session, rows, validate, progress)
        except Exception as e:
            progress.update(status="error", errors=_error(f"Ошибка валидации: {e}"))
            return
        if valid_rows is None:
            return

        full_sql = "\n".join(["BEGIN;", *build(valid_rows), "COMMIT;"])
        progress.update(status="done", sql=full_sql, count=len(valid_rows))

    return start_task(len(rows), run)


def start_execute(
    data: dict, validate: Validator, build: Callable[[list[dict]], list[str]], log_name: str, message: str,
) -> Response:
    """Validate, then run the builder's SQL in one transaction and echo it to log/<log_name>_<time>.log.

    message is formatted with {count} — the number of valid rows written.
    """
    rows: list[dict] = json.loads(data.get("rows", "[]"))

    async def run(progress: dict) -> None:
        try:
            async with get_session_maker()() as session:
                valid_rows = await _validated(session, rows, validate, progress)
                if valid_rows is None:
                    return

                progress.update(processed=0, total=1, phase="executing")
                sql_lines = build(valid_rows)
                try:
                    # The raw asyncpg connection, as the tests run it: Excel text is
                    # inlined, and a ':1' in it must not be read as a bind parameter.
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

        now = datetime.now()
        log_file = LOG_DIR / f"{log_name}_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join([
                f"=== Execute {log_name}: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
                f"Rows written: {len(valid_rows)}",
                "",
                *sql_lines,
                "",
            ]))
        logger.info("%s: %d rows, log: %s", log_name, len(valid_rows), log_file)

        progress.update(status="done", count=len(valid_rows), message=message.format(count=len(valid_rows)))

    return start_task(len(rows), run)
