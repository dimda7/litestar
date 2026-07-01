from litestar import Controller, get
from litestar.connection.request import Request
from litestar.response import Template


class SettingsController(Controller):
    path = "/settings"

    @get("/")
    async def settings_page(self, request: Request) -> Template:
        """Страница настроек."""
        return Template(
            template_name="settings.html",
            context={
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "settings",
            },
        )
