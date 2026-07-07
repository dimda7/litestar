import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response, Template

import db_manager

logger = logging.getLogger("sql_console")

# execution_id -> (профиль БД, backend pid), пока запрос выполняется
_running_queries: dict[str, tuple[str, int]] = {}


def _split_sql_statements(script: str) -> list[str]:
    """Разбивает скрипт на отдельные операторы по ';' вне строковых литералов.

    Простой посимвольный разбор без поддержки dollar-quoted строк ($$...$$)
    и экранированных кавычек — достаточно для обычных SELECT/INSERT/UPDATE/DELETE.
    """
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False

    for ch in script:
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == ";" and not in_single and not in_double:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


class SqlConsoleController(Controller):
    path = "/sql-console"

    @get("/")
    async def index(self, request: Request) -> Template:
        return Template(
            template_name="sql_console.html",
            context={
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "sql_console",
            },
        )

    @post("/execute")
    async def execute(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        script = str(data.get("sql", "") or "")
        execution_id = str(data.get("execution_id", "") or "") or uuid.uuid4().hex
        statements = _split_sql_statements(script)

        if not statements:
            return Response(
                content=json.dumps({"status": "error", "message": "Пустой SQL-запрос"}),
                status_code=200,
                media_type="application/json",
            )

        results: list[dict] = []
        try:
            pid_result = await db_session.execute(text("SELECT pg_backend_pid()"))
            _running_queries[execution_id] = (db_manager.get_active_profile(), pid_result.scalar())

            for stmt in statements:
                result = await db_session.execute(text(stmt))
                if result.returns_rows:
                    columns = list(result.keys())
                    rows = [
                        [None if v is None else str(v) for v in row]
                        for row in result.fetchall()
                    ]
                    results.append({"statement": stmt, "columns": columns, "rows": rows})
                else:
                    results.append({"statement": stmt, "columns": None, "rows": None, "rowcount": result.rowcount})
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            message = "Запрос прерван пользователем" if "QueryCanceledError" in str(e) else str(e)
            return Response(
                content=json.dumps({
                    "status": "error",
                    "message": message,
                    "results": results,
                    "failed_index": len(results),
                }),
                status_code=200,
                media_type="application/json",
            )
        finally:
            _running_queries.pop(execution_id, None)

        logger.info("SQL console: executed %d statement(s)", len(statements))
        return Response(
            content=json.dumps({"status": "ok", "results": results}),
            status_code=200,
            media_type="application/json",
        )

    @post("/cancel")
    async def cancel(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        execution_id = str(data.get("execution_id", "") or "")
        entry = _running_queries.get(execution_id)

        if not entry:
            return Response(
                content=json.dumps({"status": "error", "message": "Запрос не найден или уже завершён"}),
                status_code=200,
                media_type="application/json",
            )

        profile, pid = entry
        try:
            cancelled = await db_manager.cancel_backend(profile, pid)
        except TimeoutError:
            return Response(
                content=json.dumps({"status": "error", "message": "Таймаут: не удалось получить соединение для отправки сигнала отмены"}),
                status_code=200,
                media_type="application/json",
            )
        except Exception as e:
            return Response(
                content=json.dumps({"status": "error", "message": f"Не удалось отправить сигнал отмены: {e}"}),
                status_code=200,
                media_type="application/json",
            )

        message = "Сигнал отмены отправлен" if cancelled else "Запрос уже завершился"
        logger.info("SQL console: cancel requested for execution_id=%s (pid=%s) -> %s", execution_id, pid, message)
        return Response(
            content=json.dumps({"status": "ok", "message": message}),
            status_code=200,
            media_type="application/json",
        )
