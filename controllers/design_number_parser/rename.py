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

from .common import PATH, logger

RENAME_OLD_FIELD = "Старый ТМЦ"


RENAME_NEW_FIELD = "Новый ТМЦ"


async def validate_rename_rows(
    db_session: AsyncSession, rows: list[dict]
) -> tuple[list[dict], list[tuple[int, str, str]]]:
    errors: list[dict] = []
    valid_rows: list[tuple[int, str, str]] = []
    old_rows: dict[str, int] = {}
    new_rows: dict[str, int] = {}

    for idx, row in enumerate(rows):
        old = str(row.get(RENAME_OLD_FIELD) or "")
        new = str(row.get(RENAME_NEW_FIELD) or "")
        row_num = idx + 1
        if not old:
            errors.append({"row": row_num, "field": RENAME_OLD_FIELD, "message": f"Поле '{RENAME_OLD_FIELD}' пустое"})
            continue
        if not new:
            errors.append({"row": row_num, "field": RENAME_NEW_FIELD, "message": f"Поле '{RENAME_NEW_FIELD}' пустое"})
            continue
        if old in old_rows:
            errors.append(
                {
                    "row": row_num,
                    "field": RENAME_OLD_FIELD,
                    "message": f"Старый номер {old!r} уже указан в строке {old_rows[old]}",
                }
            )
            continue
        old_rows[old] = row_num
        if new in new_rows:
            errors.append(
                {
                    "row": row_num,
                    "field": RENAME_NEW_FIELD,
                    "message": f"Новый номер {new!r} уже указан в строке {new_rows[new]}",
                }
            )
            continue
        new_rows[new] = row_num
        if new != new.strip() or "\xa0" in new:
            errors.append(
                {
                    "row": row_num,
                    "field": RENAME_NEW_FIELD,
                    "message": f"Новый номер содержит пробельные символы: {new!r}",
                }
            )
            continue
        dn_id = (
            await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == old))
        ).scalar_one_or_none()
        if dn_id is None:
            errors.append({"row": row_num, "field": RENAME_OLD_FIELD, "message": f"design_number не найден: {old!r}"})
            continue
        if new == old:
            errors.append({"row": row_num, "field": RENAME_NEW_FIELD, "message": f"Номер не меняется: {new!r}"})
            continue
        taken_id = (
            await db_session.execute(select(DesignNumber.id).where(DesignNumber.number == new))
        ).scalar_one_or_none()
        if taken_id is not None:
            errors.append(
                {"row": row_num, "field": RENAME_NEW_FIELD, "message": f"ТМЦ {new!r} уже существует (id={taken_id})"}
            )
            continue
        valid_rows.append((dn_id, old, new))

    return errors, valid_rows


class RenameDesignNumberController(Controller):
    path = PATH

    @post("/rename-design-number")
    async def rename_design_number(
        self,
        request: Request,
        db_session: NamedDependency[AsyncSession],
        data: MultipartBody[dict],
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_rename_rows(db_session, rows)

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

        sql_lines = design_number_sql.rename(valid_rows)
        try:
            conn = await db_session.connection()
            for line in sql_lines:
                await conn.exec_driver_sql(line)
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
            f"=== Rename design_number: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows updated: {len(valid_rows)}",
            "",
        ]
        log_lines.extend(f"id={dn_id}: {old!r} -> {new!r}" for dn_id, old, new in valid_rows)
        log_lines.append("")
        log_lines.extend(sql_lines)
        log_lines.append("")

        log_file = LOG_DIR / f"rename_design_number_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("Renamed %d design numbers, log: %s", len(valid_rows), log_file)

        return Response(
            content=json.dumps(
                {"status": "ok", "count": len(valid_rows), "message": f"Успешно переименовано {len(valid_rows)} ТМЦ"}
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-rename-design-number")
    async def generate_sql_rename_design_number(
        self,
        request: Request,
        db_session: NamedDependency[AsyncSession],
        data: MultipartBody[dict],
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_rename_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines = design_number_sql.rename(valid_rows)
        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )
