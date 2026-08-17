import time

import bcrypt
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Redirect
from litestar.response import Template
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import db_manager
import db_profiles
from models import User
from schemas import DbSelectRequest, LoginRequest


def _visible_profiles() -> list[dict[str, str | int]]:
    """Подключения для экрана до логина — без паролей."""
    return [
        {"id": p.id, "name": p.name, "host": p.host, "port": p.port, "dbname": p.dbname}
        for p in db_profiles.load()
    ]


class AuthController(Controller):
    path = "/auth"

    @get("/db-select")
    async def db_select_page(self, request: Request) -> Template:
        """Выбор подключения к БД перед логином — у каждой БД свои пользователи."""
        return Template(
            template_name="db_select.html",
            context={"error": None, "db_profiles": _visible_profiles()},
        )

    @post("/db-select")
    async def db_select(
        self,
        request: Request,
        data: DbSelectRequest = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Template | Redirect:
        """Проверяет подключение к выбранной БД и делает её активной."""
        if db_profiles.get(data.profile) is None:
            return Template(
                template_name="db_select.html",
                context={"error": "Неизвестное подключение к БД", "db_profiles": _visible_profiles()},
            )

        ok, message = await db_manager.test_connection(data.profile)
        if not ok:
            return Template(
                template_name="db_select.html",
                context={"error": message, "db_profiles": _visible_profiles()},
            )

        db_manager.set_active_profile(data.profile)
        # Логин проверяется по fdw_users в только что выбранной БД — старая
        # сессия (если была от другой БД) для неё не годится.
        request.clear_session()
        return Redirect("/auth/login")

    @get("/login")
    async def login_page(self, request: Request) -> Template | Redirect:
        """Отображение страницы входа."""
        if request.session.get("user_id"):
            return Redirect("/")
        return Template(template_name="login.html", context={"error": None})

    @post("/login")
    async def login(
        self,
        request: Request,
        db_session: AsyncSession,
        data: LoginRequest = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Template | Redirect:
        """Аутентификация пользователя.

        Проверяет логин и пароль, устанавливает сессию при успехе.
        """
        username = data.username
        password = data.password

        if not username or not password:
            return Template(
                template_name="login.html",
                context={"error": "Введите логин и пароль"},
            )

        result = await db_session.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()

        if not user or not user.active:
            return Template(
                template_name="login.html",
                context={"error": "Неверный логин или пароль"},
            )
        if not user.password or not bcrypt.checkpw(
            password.encode(), user.password.encode()
        ):
            return Template(
                template_name="login.html",
                context={"error": "Неверный логин или пароль"},
            )

        request.session["user_id"] = user.id
        request.session["db_epoch"] = db_manager.get_target_epoch()
        request.session["username"] = user.username or ""
        request.session["fullname"] = " ".join(
            filter(None, [user.lastname, user.firstname, user.middlename])
        ) or user.username or ""
        request.session["last_activity"] = time.time()

        return Redirect("/")

    @get("/logout")
    async def logout(self, request: Request) -> Redirect:
        """Выход из системы. Очищает сессию."""
        request.clear_session()
        return Redirect("/auth/login")
