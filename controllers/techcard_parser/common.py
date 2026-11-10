import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation

from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from sqlalchemy import ColumnElement

from models import (
    Classifier,
    ClassifierType,
    GroupType,
    Priorities,
    Specification,
    Techcard,
    TechcardStatusTypes,
    TechcardWorkOperation,
    WorkType,
)
from sql_tasks import SqlOperation, Validator

logger = logging.getLogger("techcard_parser")

PREFIX = "techcard_parser"
PATH = "/techcard-parser"


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
    return [
        {"row": 0, "field": c, "message": f"В файле не найдена колонка '{c}'"}
        for c in expected
        if rows and c.lower() not in columns
    ]


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
    rows: list[dict],
    resolve: Callable[[dict], Awaitable[dict | None]],
    progress: dict | None,
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


def operation(
    validate: Validator, build: Callable[[list[dict]], list[str]], log_name: str, message: str
) -> SqlOperation:
    """A techcards operation for sql_tasks; message is formatted with {count}, the number of rows written."""
    return SqlOperation(
        validate=validate,
        build=build,
        log_name=log_name,
        log_title=log_name,
        summary=lambda total, valid_rows, errors: (
            message.format(count=len(valid_rows)),
            [f"Rows written: {len(valid_rows)}"],
        ),
        empty_message="Нет строк для записи в базу",
        logger=logger,
    )
