from litestar import Controller, get
from litestar.connection.request import Request
from litestar.response import Template
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User


class UsersController(Controller):
    path = "/users"

    @get("/")
    async def list_users(
        self,
        request: Request,
        db_session: AsyncSession,
        page: int = 1,
        per_page: int = 25,
    ) -> Template:
        """Список пользователей с пагинацией."""
        per_page = min(per_page, 200)
        page = max(page, 1)

        total = (await db_session.execute(select(func.count(User.id)))).scalar() or 0
        total_pages = max((total + per_page - 1) // per_page, 1)

        if page > total_pages:
            page = total_pages

        offset = (page - 1) * per_page
        result = await db_session.execute(
            select(User).order_by(User.id).offset(offset).limit(per_page)
        )
        users = result.scalars().all()

        return Template(
            template_name="users.html",
            context={
                "users": users,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "users",
            },
        )
