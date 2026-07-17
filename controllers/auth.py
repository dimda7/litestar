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
from config import settings
from models import User
from schemas import DbSelectRequest, LoginRequest


class AuthController(Controller):
    path = "/auth"

    @get("/db-select")
    async def db_select_page(self, request: Request) -> Template:
        """Выбор подключения к БД перед логином — у каждой БД свои пользователи."""
        profiles = [
            {"name": name, "label": profile.label, "host": profile.host,
             "port": profile.port, "dbname": profile.dbname}
            for name, profile in settings.db_profiles.items()
        ]
        return Template(
            template_name="db_select.html",
            context={"error": None, "db_profiles": profiles},
        )

    @post("/db-select")
    async def db_select(
        self,
        request: Request,
        data: DbSelectRequest = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Template | Redirect:
        """Проверяет подключение к выбранной БД и делает её активной."""
        profiles = [
            {"name": name, "label": profile.label, "host": profile.host,
             "port": profile.port, "dbname": profile.dbname}
            for name, profile in settings.db_profiles.items()
        ]

        if data.profile not in settings.db_profiles:
            return Template(
                template_name="db_select.html",
                context={"error": "Неизвестный профиль БД", "db_profiles": profiles},
            )

        ok, message = await db_manager.test_connection(data.profile)
        if not ok:
            return Template(
                template_name="db_select.html",
                context={"error": message, "db_profiles": profiles},
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
