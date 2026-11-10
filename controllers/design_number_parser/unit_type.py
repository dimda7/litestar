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
    UnitType,
)
from parser_storage import LOG_DIR
from sql_builders import design_number as design_number_sql

from .common import PATH, logger


async def validate_unit_type_rows(
    db_session: AsyncSession, rows: list[dict]
) -> tuple[list[dict], list[tuple[int, str, int]]]:
    """Validate rows for unit_type update.
    Returns (errors, valid_rows) where valid_rows is [(design_number_id, number, unit_type_id), ...]
    """
    errors: list[dict] = []
    valid_rows: list[tuple[int, str, int]] = []

    ut_result = await db_session.execute(select(UnitType.id, UnitType.name))
    ut_map: dict[str, int] = {}
    for ut in ut_result.all():
        if ut[1]:
            ut_map[ut[1].strip().lower()] = ut[0]

    for idx, row in enumerate(rows):
        row_num = idx + 1
        number = str(row.get("number", "") or "").strip()
        ut_name = str(row.get("unit_type", "") or "").strip()

        if not number:
            errors.append({"row": row_num, "field": "number", "message": "Поле 'number' пустое"})
            continue

        dn_result = await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == number))
        dn_id = dn_result.scalar_one_or_none()
        if dn_id is None:
            errors.append({"row": row_num, "field": "number", "message": f"design_number не найден: '{number}'"})
            continue

        if not ut_name:
            errors.append({"row": row_num, "field": "unit_type", "message": "Поле 'unit_type' пустое"})
            continue

        ut_id = ut_map.get(ut_name.lower())
        if ut_id is None:
            errors.append({"row": row_num, "field": "unit_type", "message": f"unit_type не найден: '{ut_name}'"})
            continue

        valid_rows.append((dn_id, number, ut_id))

    return errors, valid_rows


class UnitTypeController(Controller):
    path = PATH

    @post("/update-unit-type")
    async def update_unit_type(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_unit_type_rows(db_session, rows)

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
            for _dn_id, number, ut_id in valid_rows:
                await db_session.execute(
                    text("UPDATE public.design_number SET id_unit_type = :ut_id WHERE number = :number"),
                    {"ut_id": ut_id, "number": number},
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
            f"=== Update id_unit_type: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        log_lines.extend(design_number_sql.update_unit_type(valid_rows))
        log_lines.append("")

        log_file = LOG_DIR / f"update_unit_type_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated unit_type for %d rows, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps(
                {
                    "status": "ok",
                    "count": len(valid_rows),
                    "message": f"Успешно обновлено id_unit_type для {len(valid_rows)} записей",
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-unit-type")
    async def generate_sql_unit_type(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_unit_type_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.update_unit_type(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )
