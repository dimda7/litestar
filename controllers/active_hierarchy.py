import json
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.response import Template, Response

from parser_storage import LOG_DIR

logger = logging.getLogger("active_hierarchy")

PREVIEW_LIMIT = 1000

# Верхний актив (id_actves_parent) — родитель на уровень выше по lcn;
# Головной актив (id_actives_root) — вершина дерева (первый уровень lcn).
# Активы на складах (lcn начинается с 'S') не трогаются.
HIERARCHY_KINDS: dict[str, dict[str, str]] = {
    "parent": {
        "column": "id_actves_parent",
        "parent_expr": "subltree(act.lcn, 0, nlevel(act.lcn) - 1)",
        "label": "Верхний актив",
    },
    "root": {
        "column": "id_actives_root",
        "parent_expr": "subltree(act.lcn, 0, 1)",
        "label": "Головной актив",
    },
}


def _preview_sql(cfg: dict[str, str]) -> str:
    return f"""
        SELECT act.id, act.lcn::text AS lcn, act.active_number,
               act.{cfg["column"]} AS current_value,
               act_parent.active_number AS new_value,
               act_parent.lcn::text AS parent_lcn
        FROM public.actives AS act
        JOIN public.actives AS act_parent ON act_parent.lcn = {cfg["parent_expr"]}
        WHERE ltree2text(act.lcn) NOT LIKE 'S%' AND nlevel(act.lcn) > 1
          AND (act.{cfg["column"]} <> act_parent.active_number OR act.{cfg["column"]} IS NULL)
        ORDER BY act.id
        LIMIT {PREVIEW_LIMIT + 1}
    """


def _update_sql(cfg: dict[str, str]) -> str:
    return f"""
        UPDATE public.actives AS act
        SET {cfg["column"]} = act_parent.active_number
        FROM public.actives AS act_parent
        WHERE ltree2text(act.lcn) NOT LIKE 'S%' AND nlevel(act.lcn) > 1
          AND (act.{cfg["column"]} <> act_parent.active_number OR act.{cfg["column"]} IS NULL)
          AND act_parent.lcn = {cfg["parent_expr"]}
    """


class ActiveHierarchyController(Controller):
    path = "/active-hierarchy"

    @get("/")
    async def index(self, request: Request) -> Template:
        return Template(
            template_name="active_hierarchy.html",
            context={
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "active_hierarchy",
            },
        )

    @post("/preview/{kind:str}")
    async def preview(self, kind: str, db_session: AsyncSession) -> Response:
        cfg = HIERARCHY_KINDS.get(kind)
        if cfg is None:
            return Response(
                content=json.dumps({"status": "error", "message": f"Неизвестный тип: {kind}"}),
                status_code=200,
                media_type="application/json",
            )
        try:
            rows = (await db_session.execute(text(_preview_sql(cfg)))).mappings().all()
        except Exception as e:
            logger.exception("Hierarchy preview (%s) failed", kind)
            return Response(
                content=json.dumps({"status": "error", "message": f"Ошибка запроса: {e}"}),
                status_code=200,
                media_type="application/json",
            )

        truncated = len(rows) > PREVIEW_LIMIT
        return Response(
            content=json.dumps({
                "status": "ok",
                "rows": [dict(r) for r in rows[:PREVIEW_LIMIT]],
                "truncated": truncated,
            }),
            status_code=200,
            media_type="application/json",
        )

    @post("/execute/{kind:str}")
    async def execute(self, kind: str, db_session: AsyncSession) -> Response:
        cfg = HIERARCHY_KINDS.get(kind)
        if cfg is None:
            return Response(
                content=json.dumps({"status": "error", "message": f"Неизвестный тип: {kind}"}),
                status_code=200,
                media_type="application/json",
            )
        try:
            result = await db_session.execute(text(_update_sql(cfg)))
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            logger.exception("Hierarchy update (%s) failed", kind)
            return Response(
                content=json.dumps({"status": "error", "message": f"Ошибка выполнения: {e}"}),
                status_code=200,
                media_type="application/json",
            )

        count = result.rowcount
        now = datetime.now()
        log_file = LOG_DIR / f"update_active_hierarchy_{kind}_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"=== Update {cfg['label']} ({cfg['column']}): {now.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
                    f"Rows updated: {count}\n\n{_update_sql(cfg)}\n")
        logger.info("Updated %s (%s) for %d rows, log: %s", cfg["label"], cfg["column"], count, log_file)

        return Response(
            content=json.dumps({
                "status": "ok",
                "count": count,
                "message": f"{cfg['label']}: обновлено записей — {count}",
            }),
            status_code=200,
            media_type="application/json",
        )
