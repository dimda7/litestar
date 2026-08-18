import time

from litestar.types import ASGIApp, Scope, Receive, Send
from starlette.responses import JSONResponse, RedirectResponse

import db_manager


EXCLUDE_PATHS = {"/auth/login", "/auth/logout"}
EXCLUDE_PREFIXES = ("/static/",)
DB_SELECT_PATH = "/auth/db-select"

SESSION_TIMEOUT = 3600  # 1 hour in seconds


def _is_fetch(scope: Scope) -> bool:
    """Запрос сделан из JS (fetch/XHR), а не переходом по странице.

    Браузер помечает навигацию `Sec-Fetch-Mode: navigate`, а fetch() —
    `cors`/`same-origin`. Отсутствие заголовка (старый браузер, curl) считаем
    навигацией: редирект для неё безопаснее, чем JSON.
    """
    for name, value in scope.get("headers") or ():
        if name == b"sec-fetch-mode":
            return value.decode() != "navigate"
    return False


def _reject(scope: Scope, location: str, message: str):
    """Ответ неаутентифицированному запросу.

    Навигацию отправляем редиректом, а fetch — 401 с JSON: иначе fetch
    молча идёт по редиректу и получает HTML страницы логина, на котором
    `resp.json()` падает с «Unexpected token '<'» вместо внятной ошибки.
    """
    if _is_fetch(scope):
        return JSONResponse(
            {"status": "error", "message": message, "relogin": True, "location": location},
            status_code=401,
        )
    return RedirectResponse(location, status_code=303)


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
            response = _reject(scope, DB_SELECT_PATH, "Не выбрано подключение к базе данных")
            await response(scope, receive, send)
            return

        if path in EXCLUDE_PATHS:
            await self.app(scope, receive, send)
            return

        if not session.get("user_id"):
            response = _reject(scope, "/auth/login", "Вы не авторизованы — войдите заново")
            await response(scope, receive, send)
            return

        # Сессия выдана по пользователям той БД, которая была активна на момент
        # входа. Если с тех пор приложение перевели на другую базу, логин в ней
        # ничего не значит — тот же user_id там принадлежит другому человеку.
        if session.get("db_epoch") != db_manager.get_target_epoch():
            session.clear()
            response = _reject(scope, "/auth/login", "База данных сменилась — войдите заново")
            await response(scope, receive, send)
            return

        last_activity = session.get("last_activity")
        now = time.time()
        if last_activity and (now - last_activity) > SESSION_TIMEOUT:
            session.clear()
            response = _reject(scope, "/auth/login", "Сессия истекла — войдите заново")
            await response(scope, receive, send)
            return

        session["last_activity"] = now
        await self.app(scope, receive, send)
