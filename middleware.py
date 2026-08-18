import time

from litestar.types import ASGIApp, Scope, Receive, Send
from starlette.responses import JSONResponse, RedirectResponse

import db_manager


EXCLUDE_PATHS = {"/auth/login", "/auth/logout"}
EXCLUDE_PREFIXES = ("/static/",)
DB_SELECT_PATH = "/auth/db-select"

SESSION_TIMEOUT = 3600  # 1 hour in seconds


def _is_fetch(scope: Scope) -> bool:
    """The request came from JS (fetch/XHR) rather than a page navigation.

    Browsers mark navigations `Sec-Fetch-Mode: navigate` and fetch() as
    `cors`/`same-origin`. A missing header (old browser, curl) counts as a
    navigation: a redirect is the safer answer there than JSON.
    """
    for name, value in scope.get("headers") or ():
        if name == b"sec-fetch-mode":
            return value.decode() != "navigate"
    return False


def _reject(scope: Scope, location: str, message: str) -> JSONResponse | RedirectResponse:
    """Answer an unauthenticated request.

    Navigations get a redirect, fetch gets a 401 with JSON: otherwise fetch
    silently follows the redirect and receives the login page's HTML, where
    `resp.json()` dies with "Unexpected token '<'" instead of a clear error.
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
        # The ASGI transport used in tests (and potentially others) puts the
        # query string into raw_path, although the spec says it does not belong
        # there — without stripping it explicitly, the EXCLUDE_PATHS and
        # DB_SELECT_PATH comparisons break for any path carrying query params
        # (e.g. /jira/attachments?issue=...).
        path = path.split("?", 1)[0]
        session = scope.get("session", {})

        if any(path.startswith(p) for p in EXCLUDE_PREFIXES):
            await self.app(scope, receive, send)
            return

        if path == DB_SELECT_PATH:
            await self.app(scope, receive, send)
            return

        # Every database has its own users/passwords — there is physically
        # nothing to validate a login against until a connection is chosen.
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

        # The session was issued against the users of whichever database was
        # active at login time. If the app has since been pointed at a different
        # database, that login means nothing there — the same user_id belongs to
        # a different person.
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
