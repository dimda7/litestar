from pathlib import Path

from litestar import Controller, Litestar, get
from litestar.connection.request import Request
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.middleware.session.client_side import CookieBackendConfig
from litestar.openapi.config import OpenAPIConfig
from litestar.response import Template
from litestar.static_files import StaticFilesConfig
from litestar.template.config import TemplateConfig

from advanced_alchemy.extensions.litestar.plugins import SQLAlchemyPlugin
from advanced_alchemy.extensions.litestar.plugins.init.config.asyncio import SQLAlchemyAsyncConfig

from config import settings
from controllers.auth import AuthController, SESSION_SECRET
from controllers.parser import ParserController
from controllers.train_parser import TrainParserController
from controllers.design_number_parser import DesignNumberParserController
from controllers.users import UsersController
from middleware import AuthMiddleware
from models import Base

db_config = SQLAlchemyAsyncConfig(
    connection_string=settings.db_url,
    metadata=Base.metadata,
)

session_config = CookieBackendConfig(secret=SESSION_SECRET)


class HomeController(Controller):
    path = "/"

    @get("/")
    async def home(self, request: Request) -> Template:
        """Главная страница."""
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

app = Litestar(
    route_handlers=[HomeController, UsersController, AuthController, ParserController, TrainParserController, DesignNumberParserController],
    template_config=TemplateConfig(
        engine=JinjaTemplateEngine(directory=base_dir / "templates"),
    ),
    static_files_config=[
        StaticFilesConfig(
            path="/static",
            directories=[base_dir / "static"],
        ),
    ],
    plugins=[SQLAlchemyPlugin(config=db_config)],
    middleware=[session_config.middleware, AuthMiddleware],
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
        "app:app",  # формат "имя_файла:имя_переменной_приложения"
        host="0.0.0.0",
        port=settings.server_port,
        # reload=False  # <--- ВАЖНО: оставьте выключенным при отладке в PyCharm!
    )

