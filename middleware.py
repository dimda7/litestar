import time

from litestar.types import ASGIApp, Scope, Receive, Send
from starlette.responses import RedirectResponse

import db_manager


EXCLUDE_PATHS = {"/auth/login", "/auth/logout"}
EXCLUDE_PREFIXES = ("/static/",)
DB_SELECT_PATH = "/auth/db-select"

SESSION_TIMEOUT = 3600  # 1 hour in seconds


class AuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_path = scope.get("raw_path", b"")
        path = raw_path.decode() if isinstance(raw_path, bytes) else scope.get("path", "")
        # ASGI-транспорт в тестах (и потенциально другие) кладёт в raw_path
        # путь вместе со строкой запроса, хотя по спеке её там быть не должно —
        # без явного среза сравнения с EXCLUDE_PATHS/DB_SELECT_PATH ломаются
        # для любого пути с query-параметрами (например, /jira/attachments?issue=...).
        path = path.split("?", 1)[0]
        session = scope.get("session", {})

        if any(path.startswith(p) for p in EXCLUDE_PREFIXES):
            await self.app(scope, receive, send)
            return

        if path == DB_SELECT_PATH:
            await self.app(scope, receive, send)
            return

        # У каждой БД свои пользователи/пароли — логин физически не по чему
        # проверять, пока не выбрано конкретное подключение.
        if not db_manager.has_active_connection():
            response = RedirectResponse(DB_SELECT_PATH, status_code=303)
            await response(scope, receive, send)
            return

        if path in EXCLUDE_PATHS:
            await self.app(scope, receive, send)
            return

        if not session.get("user_id"):
            response = RedirectResponse("/auth/login", status_code=303)
            await response(scope, receive, send)
            return

        last_activity = session.get("last_activity")
        now = time.time()
        if last_activity and (now - last_activity) > SESSION_TIMEOUT:
            session.clear()
            response = RedirectResponse("/auth/login", status_code=303)
            await response(scope, receive, send)
            return

        session["last_activity"] = now
        await self.app(scope, receive, send)
