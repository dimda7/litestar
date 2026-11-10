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
    CounterGroup,
    DesignNumber,
)
from parser_storage import LOG_DIR
from sql_builders import design_number as design_number_sql

from .common import PATH, logger


async def validate_counter_group_rows(
    db_session: AsyncSession, rows: list[dict]
) -> tuple[list[dict], list[tuple[int, str, int]]]:
    """Validate rows for counter_group update.
    Returns (errors, valid_rows) where valid_rows is [(design_number_id, number, counter_group_id), ...]
    """
    errors: list[dict] = []
    valid_rows: list[tuple[int, str, int]] = []

    cg_result = await db_session.execute(select(CounterGroup.id, CounterGroup.name))
    cg_map: dict[str, int] = {}
    for cg in cg_result.all():
        if cg[1]:
            cg_map[cg[1].strip().lower()] = cg[0]

    for idx, row in enumerate(rows):
        row_num = idx + 1
        number = str(row.get("number", "") or "").strip()
        cg_name = str(row.get("counter_group", "") or "").strip()

        if not number:
            errors.append({"row": row_num, "field": "number", "message": "Поле 'number' пустое"})
            continue

        dn_result = await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == number))
        dn_id = dn_result.scalar_one_or_none()
        if dn_id is None:
            errors.append({"row": row_num, "field": "number", "message": f"design_number не найден: '{number}'"})
            continue

        if not cg_name:
            errors.append({"row": row_num, "field": "counter_group", "message": "Поле 'counter_group' пустое"})
            continue

        cg_id = cg_map.get(cg_name.lower())
        if cg_id is None:
            errors.append(
                {"row": row_num, "field": "counter_group", "message": f"counter_group не найден: '{cg_name}'"}
            )
            continue

        valid_rows.append((dn_id, number, cg_id))

    return errors, valid_rows


class CounterGroupController(Controller):
    path = PATH

    @post("/update-counter-group")
    async def update_counter_group(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_counter_group_rows(db_session, rows)

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
            for _dn_id, number, cg_id in valid_rows:
                await db_session.execute(
                    text("UPDATE public.design_number SET id_counter_group = :cg_id WHERE number = :number"),
                    {"cg_id": cg_id, "number": number},
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
            f"=== Update id_counter_group: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        log_lines.extend(design_number_sql.update_counter_group(valid_rows))
        log_lines.append("")

        log_file = LOG_DIR / f"update_counter_group_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Updated counter_group for %d rows, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps(
                {
                    "status": "ok",
                    "count": len(valid_rows),
                    "message": f"Успешно обновлено id_counter_group для {len(valid_rows)} записей",
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-counter-group")
    async def generate_sql_counter_group(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_counter_group_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.update_counter_group(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )
