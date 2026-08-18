import logging
from pathlib import Path

from litestar import Controller, Litestar, get
from litestar.config.csrf import CSRFConfig
from litestar.connection.request import Request
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.di import Provide
from litestar.middleware.session.client_side import CookieBackendConfig
from litestar.openapi.config import OpenAPIConfig
from litestar.response import Template
from litestar.static_files import StaticFilesConfig
from litestar.template.config import TemplateConfig

import db_manager
from config import settings
from logging_config import configure_logging
from controllers.auth import AuthController
from controllers import parser as parser_package
from controllers.train_parser import TrainParserController
from controllers.design_number_parser import DesignNumberParserController
from controllers import actives_parser as actives_parser_package
from controllers.active_hierarchy import ActiveHierarchyController
from controllers.ptoir_parser import PtoirParserController
from controllers.order_parser import OrderParserController
from controllers.jira import JiraController
from controllers.sql_console import SqlConsoleController
from controllers.users import UsersController
from controllers.settings import SettingsController
from controllers.about import AboutController
from middleware import AuthMiddleware

configure_logging(level=settings.log_level)

session_config = CookieBackendConfig(secret=settings.session_secret)

# No separate secret: settings.session_secret is already stored in .env as a
# random hex string, and is used here to HMAC-sign the CSRF token (a different
# cryptographic purpose than encrypting the session cookie).
csrf_config = CSRFConfig(secret=settings.session_secret.hex())


def not_found_handler(request: Request, exc: Exception) -> Template:
    """Custom 404 page."""
    return Template(template_name="errors/404.html", status_code=404)


def server_error_handler(request: Request, exc: Exception) -> Template:
    """Custom 500 page. The full error goes to the log, the user gets no details."""
    logging.getLogger("app").error("Unhandled error on %s %s", request.method, request.url, exc_info=exc)
    return Template(template_name="errors/500.html", status_code=500)


class HomeController(Controller):
    path = "/"

    @get("/")
    async def home(self, request: Request) -> Template:
        """Home page."""
        user_id = request.session.get("user_id")
        fullname = request.session.get("fullname", "")
        return Template(
            template_name="index.html",
            context={
                "message": "Добро пожаловать на главную страницу!",
                "user_id": user_id,
                "fullname": fullname,
                "active_page": "home",
            },
        )


base_dir = Path(__file__).parent

jinja_engine = JinjaTemplateEngine(directory=base_dir / "templates")
jinja_engine.register_template_callable("current_db_label", lambda context: db_manager.get_active_label())

app = Litestar(
    route_handlers=[HomeController, UsersController, AuthController, *parser_package.CONTROLLERS, TrainParserController, DesignNumberParserController, *actives_parser_package.CONTROLLERS, ActiveHierarchyController, PtoirParserController, OrderParserController, JiraController, SqlConsoleController, SettingsController, AboutController],
    template_config=TemplateConfig(
        engine=jinja_engine,
    ),
    static_files_config=[
        StaticFilesConfig(
            path="/static",
            directories=[base_dir / "static"],
        ),
    ],
    dependencies={"db_session": Provide(db_manager.provide_db_session)},
    middleware=[session_config.middleware, AuthMiddleware],
    csrf_config=csrf_config,
    exception_handlers={
        404: not_found_handler,
        500: server_error_handler,
    },
    on_shutdown=[db_manager.dispose_all],
    openapi_config=OpenAPIConfig(
        title="Grom API",
        version="1.0.0",
        description="API для управления данными моделей поездов (grom). Включает парсинг Excel, валидацию и атомарные операции с БД.",
    ),

)

if __name__ == "__main__":
    #import subprocess
    #import sys

    #subprocess.run(
    #    [sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(settings.server_port)],
    #    check=True,
    #)

    import uvicorn
    uvicorn.run(
        "app:app",  # format is "module_name:app_variable_name"
        host="0.0.0.0",
        port=settings.server_port,
        # reload=False  # <--- IMPORTANT: keep this off when debugging in PyCharm!
    )

