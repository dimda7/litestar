import json
import logging

from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response, Template

import db_manager
import db_profiles
from schemas import DBProfileFormRequest, DBProfileRequest, DBProfileUpdateRequest

logger = logging.getLogger("db_manager")


def _json(status: str, message: str, **extra: object) -> Response:
    return Response(
        content=json.dumps({"status": status, "message": message, **extra}),
        status_code=200,
        media_type="application/json",
    )


def _save_failed(e: OSError) -> Response:
    """The connections file could not be written (permissions, disk space, ro mount).

    Without this branch the error fell through to the generic 500 handler,
    which renders an HTML page, while the Settings page JS expects JSON.
    """
    logger.error("DB profiles file write failed: %s", e, exc_info=e)
    return _json("error", f"Не удалось сохранить файл подключений: {e}")


def _form_url(data: DBProfileFormRequest) -> str:
    return db_profiles.build_url(data.host, data.port, data.user, data.password, data.dbname)


class SettingsController(Controller):
    path = "/settings"

    @get("/")
    async def settings_page(self, request: Request) -> Template:
        """The Settings page."""
        # Passwords stay out of the context: the whole page ends up in view
        # source, the browser cache and proxy logs. The edit modal fetches one
        # connection's password in a separate request.
        profiles = [
            {"id": p.id, "name": p.name, "host": p.host, "port": p.port,
             "user": p.user, "dbname": p.dbname}
            for p in db_profiles.load()
        ]
        return Template(
            template_name="settings.html",
            context={
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "settings",
                "db_profiles": profiles,
                "active_db_profile": db_manager.get_active_profile(),
                "active_db_label": db_manager.get_active_label(),
            },
        )

    @post("/db/test-connection")
    async def test_connection(
        self,
        request: Request,
        data: DBProfileRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        ok, message = await db_manager.test_connection(data.profile)
        return _json("ok" if ok else "error", message)

    @post("/db/password")
    async def password(
        self,
        request: Request,
        data: DBProfileRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """One connection's password — to prefill the edit form."""
        profile = db_profiles.get(data.profile)
        if profile is None:
            return _json("error", "Подключение не найдено")
        return _json("ok", "", password=profile.password)

    @post("/db/test-params")
    async def test_params(
        self,
        request: Request,
        data: DBProfileFormRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Test parameters that are not saved yet — the button inside the form."""
        ok, message = await db_manager.test_url(_form_url(data))
        return _json("ok" if ok else "error", message)

    @post("/db/connect")
    async def connect(
        self,
        request: Request,
        data: DBProfileRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        ok, message = await db_manager.test_connection(data.profile)
        if not ok:
            return _json("error", message)

        changed = db_manager.set_active_profile(data.profile)

        if changed:
            # Another database has its own users — the current login is no good there.
            request.clear_session()

        profile = db_profiles.get(data.profile)
        return _json(
            "ok",
            f"Подключено к «{profile.name if profile else data.profile}»",
            active=data.profile,
            relogin=changed,
        )

    @post("/db/add")
    async def add(
        self,
        request: Request,
        data: DBProfileFormRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        try:
            created = db_profiles.add(
                name=data.name, host=data.host, port=data.port,
                user=data.user, password=data.password, dbname=data.dbname,
            )
        except ValueError as e:
            return _json("error", str(e))
        except OSError as e:
            return _save_failed(e)

        logger.info(
            "DB profile added: %s (%s:%s/%s)", created.name, created.host, created.port, created.dbname,
        )
        return _json("ok", f"Подключение «{created.name}» добавлено")

    @post("/db/update")
    async def update(
        self,
        request: Request,
        data: DBProfileUpdateRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        existing = db_profiles.get(data.profile)
        if existing is None:
            return _json("error", "Подключение не найдено")

        try:
            updated = db_profiles.update(
                data.profile, name=data.name, host=data.host, port=data.port,
                user=data.user, password=data.password, dbname=data.dbname,
            )
        except ValueError as e:
            return _json("error", str(e))
        except OSError as e:
            return _save_failed(e)

        # The cached engine was built from the old URL — drop it, or an edit to
        # the host or password has no effect.
        await db_manager.forget_engine(data.profile)

        # Changing host/port/database means a different database with different
        # users: a session issued by the old one is no good there. Editing the
        # name, user or password does not log anyone out.
        relogin = (
            not db_profiles.targets_same_database(existing, updated)
            and data.profile == db_manager.get_active_profile()
        )
        if relogin:
            # Everyone else's sessions were issued by the old database too and
            # their logins were never checked against the new one, so bump the
            # epoch for all of them.
            db_manager.bump_target_epoch()
            request.clear_session()

        logger.info(
            "DB profile updated: %s (%s:%s/%s)", updated.name, updated.host, updated.port, updated.dbname,
        )
        return _json("ok", f"Подключение «{updated.name}» сохранено", relogin=relogin)

    @post("/db/delete")
    async def delete(
        self,
        request: Request,
        data: DBProfileRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        profile = db_profiles.get(data.profile)
        try:
            db_profiles.delete(data.profile, db_manager.get_active_profile())
        except ValueError as e:
            return _json("error", str(e))
        except OSError as e:
            return _save_failed(e)

        await db_manager.forget_engine(data.profile)

        name = profile.name if profile else data.profile
        logger.info("DB profile deleted: %s", name)
        return _json("ok", f"Подключение «{name}» удалено")
