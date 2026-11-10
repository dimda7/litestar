import json
from datetime import datetime

from litestar import Controller, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    DesignNumber,
)
from parser_storage import LOG_DIR
from sql_builders import design_number as design_number_sql

from .common import PATH, logger


async def validate_is_serial_1c_rows(
    db_session: AsyncSession, rows: list[dict]
) -> tuple[list[dict], list[tuple[int, str, bool]]]:
    """Validate rows for is_serial_1c update.
    Returns (errors, valid_rows) where valid_rows is [(design_number_id, number, is_serial_1c), ...]
    """
    errors: list[dict] = []
    valid_rows: list[tuple[int, str, bool]] = []

    for idx, row in enumerate(rows):
        row_num = idx + 1
        number = str(row.get("number", "") or "").strip()
        is_serial_1c_raw = str(row.get("is_serial_1c", "") or "").strip().lower()

        if not number:
            errors.append({"row": row_num, "field": "number", "message": "Поле 'number' пустое"})
            continue

        dn_result = await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == number))
        dn_id = dn_result.scalar_one_or_none()
        if dn_id is None:
            errors.append({"row": row_num, "field": "number", "message": f"design_number не найден: '{number}'"})
            continue

        if is_serial_1c_raw not in ("true", "false", "1", "0", "да", "нет"):
            errors.append(
                {
                    "row": row_num,
                    "field": "is_serial_1c",
                    "message": f"Неверное значение is_serial_1c: '{is_serial_1c_raw}' (ожидается true/false)",
                }
            )
            continue

        is_serial_1c = is_serial_1c_raw in ("true", "1", "да")
        valid_rows.append((dn_id, number, is_serial_1c))

    return errors, valid_rows


class IsSerial1cController(Controller):
    path = PATH

    @post("/update-is-serial-1c")
    async def update_is_serial_1c(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_is_serial_1c_rows(db_session, rows)

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

        try:
            for _dn_id, number, is_serial_1c in valid_rows:
                await db_session.execute(
                    text("UPDATE public.design_number SET is_serial_1c = :val WHERE number = :number"),
                    {"val": is_serial_1c, "number": number},
                )
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

        now = datetime.now()
        log_lines = [
            f"=== Update is_serial_1c: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        log_lines.extend(design_number_sql.update_is_serial_1c(valid_rows))
        log_lines.append("")

        log_file = LOG_DIR / f"update_is_serial_1c_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated is_serial_1c for %d rows, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps(
                {
                    "status": "ok",
                    "count": len(valid_rows),
                    "message": f"Успешно обновлено is_serial_1c для {len(valid_rows)} записей",
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-is-serial-1c")
    async def generate_sql_is_serial_1c(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_is_serial_1c_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.update_is_serial_1c(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )
