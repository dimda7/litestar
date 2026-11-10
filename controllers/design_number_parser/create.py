import json
from datetime import datetime

from litestar import Controller, post
from litestar.connection.request import Request
from litestar.di import NamedDependency
from litestar.params import MultipartBody
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    DesignNumber,
)
from parser_storage import LOG_DIR
from sql_builders import design_number as design_number_sql

from .common import (
    PATH,
    _cell,
    _cell_text,
    _field_catalogues,
    _missing_headers,
    _parse_fields,
    logger,
    snapshot_design_numbers,
)

# design_number.number is varchar(32).
DESIGN_NUMBER_MAX_LENGTH = 32


def _similarity_key(number: str) -> str:
    return number.strip().casefold()


async def validate_create_rows(
    db_session: AsyncSession, rows: list[dict]
) -> tuple[list[dict], list[design_number_sql.DesignNumberRow]]:
    errors: list[dict] = _missing_headers(rows)
    if errors:
        return errors, []
    valid_rows: list[design_number_sql.DesignNumberRow] = []

    catalogues = await _field_catalogues(db_session)
    existing: dict[str, int] = {}
    similar: dict[str, tuple[str, int]] = {}
    for number, id_ in (await db_session.execute(select(DesignNumber.number, DesignNumber.id))).all():
        if number:
            existing[number] = id_
            similar.setdefault(_similarity_key(number), (number, id_))
    number_rows: dict[str, int] = {}
    similar_rows: dict[str, int] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        number = _cell_text(row.get("number"))
        if _cell(row, "number") is None:
            errors.append({"row": row_num, "field": "number", "message": "Поле 'number' пустое"})
            continue
        if number != number.strip() or "\xa0" in number:
            errors.append(
                {"row": row_num, "field": "number", "message": f"Номер содержит пробельные символы: {number!r}"}
            )
            continue
        if len(number) > DESIGN_NUMBER_MAX_LENGTH:
            errors.append(
                {
                    "row": row_num,
                    "field": "number",
                    "message": f"Номер длиннее {DESIGN_NUMBER_MAX_LENGTH} символов ({len(number)}): {number!r}",
                }
            )
            continue
        key = _similarity_key(number)
        if number in number_rows:
            errors.append(
                {
                    "row": row_num,
                    "field": "number",
                    "message": f"Номер {number!r} уже указан в строке {number_rows[number]}",
                }
            )
            continue
        if key in similar_rows:
            errors.append(
                {
                    "row": row_num,
                    "field": "number",
                    "message": f"Номер {number!r} похож на номер из строки {similar_rows[key]}",
                }
            )
            continue
        number_rows[number] = row_num
        similar_rows[key] = row_num
        if key in similar and number not in existing:
            similar_number, similar_id = similar[key]
            errors.append(
                {
                    "row": row_num,
                    "field": "number",
                    "message": f"В БД есть похожий номер {similar_number!r} (id={similar_id})",
                }
            )
            continue
        fields = _parse_fields(row, row_num, catalogues, errors)
        if fields is not None:
            valid_rows.append(design_number_sql.DesignNumberRow(id=existing.get(number), number=number, **fields))

    return errors, valid_rows


class CreateDesignNumberController(Controller):
    path = PATH

    @post("/create-design-number")
    async def create_design_number(
        self,
        request: Request,
        db_session: NamedDependency[AsyncSession],
        data: MultipartBody[dict],
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_create_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps(
                    {
                        "status": "error",
                        "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для создания"}],
                    }
                ),
                status_code=200,
                media_type="application/json",
            )

        snapshot = await snapshot_design_numbers(db_session, [row.id for row in valid_rows if row.id is not None])
        sql_lines = design_number_sql.create_or_update(valid_rows)
        created = sum(1 for row in valid_rows if row.id is None)
        updated = len(valid_rows) - created

        now = datetime.now()
        log_lines = [
            f"=== Create design_number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows: {len(valid_rows)} (created {created}, updated {updated})",
            "",
            "-- Rollback (deletes the created design numbers, restores the updated ones):",
            *design_number_sql.undo_create_or_update(valid_rows, snapshot),
            "",
            "-- Executed:",
            *sql_lines,
            "",
        ]
        log_file = LOG_DIR / f"create_design_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))

        try:
            conn = await db_session.connection()
            raw_conn = await conn.get_raw_connection()
            assert raw_conn.driver_connection is not None
            await raw_conn.driver_connection.execute("\n".join(sql_lines))
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            return Response(
                content=json.dumps(
                    {"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]}
                ),
                status_code=200,
                media_type="application/json",
            )
        logger.info("Created %d and updated %d design numbers, log: %s", created, updated, log_file)

        return Response(
            content=json.dumps(
                {
                    "status": "ok",
                    "count": len(valid_rows),
                    "message": f"Успешно создано {created} ТМЦ, обновлено {updated} ТМЦ",
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-create-design-number")
    async def generate_sql_create_design_number(
        self,
        request: Request,
        db_session: NamedDependency[AsyncSession],
        data: MultipartBody[dict],
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_create_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.create_or_update(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(valid_rows)}),
            status_code=200,
            media_type="application/json",
        )
