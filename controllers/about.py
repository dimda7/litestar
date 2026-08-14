import logging

from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get
from litestar.connection.request import Request
from litestar.response import Template

from models import Actives, Orders, Relocate, Techcard, Train, User

logger = logging.getLogger("app")


class TrainRepository(SQLAlchemyAsyncRepository[Train]):
    model_type = Train


class UserRepository(SQLAlchemyAsyncRepository[User]):
    model_type = User


class TechcardRepository(SQLAlchemyAsyncRepository[Techcard]):
    model_type = Techcard


class ActivesRepository(SQLAlchemyAsyncRepository[Actives]):
    model_type = Actives


class OrdersRepository(SQLAlchemyAsyncRepository[Orders]):
    model_type = Orders


class RelocateRepository(SQLAlchemyAsyncRepository[Relocate]):
    model_type = Relocate


def _fmt(value: int) -> str:
    return f"{value:,}".replace(",", " ")


class AboutController(Controller):
    path = "/about"

    @get("/")
    async def index(self, request: Request, db_session: AsyncSession) -> Template:
        try:
            counts: dict[str, int] = {
                "trains": await TrainRepository(session=db_session).count(),
                "users": await UserRepository(session=db_session).count(
                    User.active.is_(True), User.is_user.is_(True)
                ),
                "techcards": await TechcardRepository(session=db_session).count(),
                "actives": await ActivesRepository(session=db_session).count(),
                "orders": await OrdersRepository(session=db_session).count(),
                "relocates": await RelocateRepository(session=db_session).count(),
            }
            stats: dict[str, str] = {key: _fmt(value) for key, value in counts.items()}
            error = ""
        except Exception as e:
            logger.exception("About stats query failed")
            stats = {}
            error = f"Не удалось получить статистику: {e}"

        return Template(
            template_name="about.html",
            context={
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "about",
                "stats": stats,
                "error": error,
            },
        )
