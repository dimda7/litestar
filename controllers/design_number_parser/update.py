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
    _cell_text,
    _field_catalogues,
    _missing_headers,
    _parse_fields,
    logger,
    normalize_headers,
    snapshot_design_numbers,
)


async def validate_update_rows(
    db_session: AsyncSession, rows: list[dict]
) -> tuple[list[dict], list[design_number_sql.DesignNumberRow]]:
    rows = normalize_headers(rows)
    errors: list[dict] = _missing_headers(rows)
    if errors:
        return errors, []
    valid_rows: list[design_number_sql.DesignNumberRow] = []

    catalogues = await _field_catalogues(db_session)
    numbers = [_cell_text(row.get("number")) for row in rows]
    dn_ids: dict[str | None, int] = {}
    unit_types: dict[str | None, int | None] = {}
    for number, id_, id_unit_type in (
        await db_session.execute(
            select(DesignNumber.number, DesignNumber.id, DesignNumber.id_unit_type).where(
                DesignNumber.number.in_(numbers)
            )
        )
    ).all():
        dn_ids[number] = id_
        unit_types[number] = id_unit_type

    number_rows: dict[str, int] = {}

    for idx, row in enumerate(rows):
        row_num = idx + 1
        number = numbers[idx]
        if not number:
            errors.append({"row": row_num, "field": "number", "message": "Поле 'number' пустое"})
            continue
        if number in number_rows:
            errors.append(
                {
                    "row": row_num,
                    "field": "number",
                    "message": f"Номер {number!r} уже указан в строке {number_rows[number]}",
                }
            )
            continue
        number_rows[number] = row_num
        if number not in dn_ids:
            errors.append({"row": row_num, "field": "number", "message": f"design_number не найден: {number!r}"})
            continue
        fields = _parse_fields(row, row_num, catalogues, errors, unit_types[number])
        if fields is not None:
            valid_rows.append(design_number_sql.DesignNumberRow(id=dn_ids[number], number=number, **fields))

    return errors, valid_rows


class UpdateDesignNumberController(Controller):
    path = PATH

    @post("/update-design-number")
    async def update_design_number(
        self,
        request: Request,
        db_session: NamedDependency[AsyncSession],
        data: MultipartBody[dict],
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_update_rows(db_session, rows)

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
                        "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для обновления"}],
                    }
                ),
                status_code=200,
                media_type="application/json",
            )

        snapshot = await snapshot_design_numbers(db_session, [row.id for row in valid_rows if row.id is not None])
        sql_lines = design_number_sql.update_full(valid_rows)

        # Written before execution: the overwrite is irreversible, so the
        # rollback SQL has to exist even if the run itself fails half-way.
        now = datetime.now()
        log_lines = [
            f"=== Update design_number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows: {len(valid_rows)}",
            "",
            "-- Rollback (state before the update):",
            *design_number_sql.update_full(snapshot),
            "",
            "-- Executed:",
            *sql_lines,
            "",
        ]
        log_file = LOG_DIR / f"update_design_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
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
        logger.info("Updated %d design numbers, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps(
                {"status": "ok", "count": len(valid_rows), "message": f"Успешно обновлено {len(valid_rows)} ТМЦ"}
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-update-design-number")
    async def generate_sql_update_design_number(
        self,
        request: Request,
        db_session: NamedDependency[AsyncSession],
        data: MultipartBody[dict],
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_update_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.update_full(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(valid_rows)}),
            status_code=200,
            media_type="application/json",
        )
