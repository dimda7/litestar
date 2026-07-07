import json

from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response, Template

import db_manager
from config import settings
from schemas import DBProfileRequest


class SettingsController(Controller):
    path = "/settings"

    @get("/")
    async def settings_page(self, request: Request) -> Template:
        """Страница настроек."""
        profiles = [
            {"name": name, "label": profile.label, "host": profile.host,
             "port": profile.port, "dbname": profile.dbname}
            for name, profile in settings.db_profiles.items()
        ]
        return Template(
            template_name="settings.html",
            context={
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "settings",
                "db_profiles": profiles,
                "active_db_profile": db_manager.get_active_profile(),
            },
        )

    @post("/db/test-connection")
    async def test_connection(
        self,
        request: Request,
        data: DBProfileRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        ok, message = await db_manager.test_connection(data.profile)
        return Response(
            content=json.dumps({"status": "ok" if ok else "error", "message": message}),
            status_code=200,
            media_type="application/json",
        )

    @post("/db/connect")
    async def connect(
        self,
        request: Request,
        data: DBProfileRequest = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        ok, message = await db_manager.test_connection(data.profile)
        if not ok:
            return Response(
                content=json.dumps({"status": "error", "message": message}),
                status_code=200,
                media_type="application/json",
            )

        db_manager.set_active_profile(data.profile)
        return Response(
            content=json.dumps({
                "status": "ok",
                "message": f"Подключено к «{data.profile}»",
                "active": data.profile,
            }),
            status_code=200,
            media_type="application/json",
        )
