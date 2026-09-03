import json
import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from litestar import Controller, get, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Template, Response

from models import CounterActive, MileageTrain, Train
from parser_storage import LOG_DIR
from sql_builders import mileage as mileage_sql
from sql_builders.actives import MILEAGE_COUNTER_TYPE_ID
from sql_builders.mileage import Correction

logger = logging.getLogger("mileage_correction")

RECENT_LIMIT = 10


def _json(payload: dict) -> Response:
    return Response(content=json.dumps(payload, ensure_ascii=False), status_code=200,
                    media_type="application/json")


async def resolve_correction(
    db_session: AsyncSession, train_id: int, mileage_row_id: int,
) -> tuple[str, Correction | None]:
    """Everything needed to roll a train's mileage back to before the selected row.

    Returns ("", correction) or (message, None) — resolution stops at the first
    problem, so there is never more than one message to report.
    """
    train = (await db_session.execute(select(Train).where(Train.id == train_id))).scalar_one_or_none()
    if train is None:
        return "Поезд не найден", None

    selected = (await db_session.execute(
        select(MileageTrain).where(MileageTrain.id == mileage_row_id, MileageTrain.id_train == train_id)
    )).scalar_one_or_none()
    if selected is None:
        return "Выбранная запись о пробеге не найдена у этого поезда", None
    if selected.date is None:
        # A null date means this row was never a manual reading — the trigger's
        # own fill for a later one — and isn't something the page's list would
        # ever offer to pick.
        return f"У записи mileage_train.id={mileage_row_id} не заполнено время (date)", None

    if train.active is None:
        return "У поезда не задан актив (train.active)", None

    counters = (await db_session.execute(
        select(CounterActive.id).where(
            CounterActive.id_active == train.active,
            CounterActive.id_counter_type == MILEAGE_COUNTER_TYPE_ID,
            CounterActive.is_train.is_(True),
        )
    )).scalars().all()
    if not counters:
        return f"Счётчик пробега не найден для актива {train.active}", None
    if len(counters) > 1:
        ids = ", ".join(str(i) for i in counters)
        return f"Для актива {train.active} найдено несколько счётчиков пробега: {ids}", None

    # date IS NOT NULL, same as the page's list: a null-date row only exists as
    # part of the trigger's fill for a *later* manual entry, so an interior one
    # between two manual readings belongs to the entry being undone, not to a
    # surviving prior state — "previous" must be the last manual reading, not
    # merely the closest earlier row.
    previous = (await db_session.execute(
        select(MileageTrain.id, MileageTrain.milage, MileageTrain.date, MileageTrain.date_average)
        .where(MileageTrain.id_train == train_id, MileageTrain.date_average < selected.date_average,
               MileageTrain.date.is_not(None))
        .order_by(MileageTrain.date_average.desc(), MileageTrain.id.desc())
        .limit(1)
    )).first()
    if previous is None:
        return "Нет более ранней записи о пробеге — счётчик не к чему откатывать", None
    if previous.milage is None:
        return f"У записи mileage_train.id={previous.id} не заполнен пробег (milage)", None

    # The same predicate as the DELETE: date_average strictly after the last
    # surviving row, not >= the selected row's own date_average. A gap of more
    # than a day between two manual readings gets filled by the trigger with
    # intermediate rows carrying a null date, which the page's list — and a
    # boundary of the selected row's own date — would both miss.
    delete_count = await db_session.scalar(
        select(func.count()).select_from(MileageTrain).where(
            MileageTrain.id_train == train_id,
            MileageTrain.date_average > previous.date_average,
        )
    )

    return "", Correction(
        id_train=train_id,
        id_active=train.active,
        counter_date=previous.date,
        counter_value=previous.milage,
        source_row_id=previous.id,
        source_date_average=previous.date_average,
        delete_count=delete_count,
    )


async def _resolve_from_form(
    db_session: AsyncSession, data: dict,
) -> tuple[Response | None, Correction | None]:
    """The prologue the three action routes share: resolve, or answer the error."""
    error, correction = await resolve_correction(
        db_session, int(data["train_id"]), int(data["mileage_row_id"]))
    if correction is None:
        return _json({"status": "error", "message": error}), None
    return None, correction


class MileageCorrectionController(Controller):
    path = "/mileage-correction"

    @get("/")
    async def index(self, request: Request, db_session: AsyncSession) -> Template:
        trains = (await db_session.execute(
            select(Train.id, Train.name)
            .where(Train.is_delete.is_not(True), Train.name.is_not(None))
            .order_by(Train.name)
        )).all()

        return Template(
            template_name="mileage_correction.html",
            context={
                "trains": [{"id": t.id, "name": t.name} for t in trains],
                "recent_limit": RECENT_LIMIT,
                "user_id": request.session.get("user_id"),
                "fullname": request.session.get("fullname", ""),
                "active_page": "mileage_correction",
            },
        )

    @get("/mileage/{train_id:int}")
    async def mileage(self, train_id: int, db_session: AsyncSession) -> Response:
        # Only the readings an operator actually entered: every other row of
        # mileage_train was generated by counter_active_trigger with a null date.
        rows = (await db_session.execute(
            select(MileageTrain.id, MileageTrain.date, MileageTrain.date_average,
                   MileageTrain.milage, MileageTrain.mileage_average)
            .where(MileageTrain.id_train == train_id, MileageTrain.date.is_not(None))
            .order_by(MileageTrain.date_average.desc(), MileageTrain.id.desc())
            .limit(RECENT_LIMIT)
        )).all()

        return _json({
            "status": "ok",
            "rows": [
                {
                    "id": r.id,
                    "date": r.date.strftime("%Y-%m-%d %H:%M:%S"),
                    "date_average": r.date_average.strftime("%Y-%m-%d"),
                    "milage": r.milage,
                    "mileage_average": r.mileage_average,
                }
                for r in rows
            ],
        })

    @post("/preview")
    async def preview(
        self,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        failed, correction = await _resolve_from_form(db_session, data)
        if failed is not None:
            return failed

        return _json({
            "status": "ok",
            "delete_count": correction.delete_count,
            "counter_value": correction.counter_value,
            "counter_date": correction.counter_date.strftime("%Y-%m-%d %H:%M:%S"),
            "kept_date": correction.source_date_average.strftime("%Y-%m-%d"),
        })

    @post("/generate-sql")
    async def generate_sql(
        self,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        failed, correction = await _resolve_from_form(db_session, data)
        if failed is not None:
            return failed

        sql = "\n".join(mileage_sql.correct_mileage(correction, wrap_transaction=True))
        return _json({"status": "ok", "sql": sql})

    @post("/execute")
    async def execute(
        self,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        failed, correction = await _resolve_from_form(db_session, data)
        if failed is not None:
            return failed

        sql_body = "\n".join(mileage_sql.correct_mileage(correction))
        try:
            # ALTER TABLE ... DISABLE TRIGGER cannot share a prepared statement
            # with the DELETE and the UPDATE, so the whole body goes to the raw
            # asyncpg connection — the same path move_actives and create_actives
            # use. It runs inside the session's transaction, so the rollback
            # below also puts the trigger back.
            conn = await db_session.connection()
            raw_conn = await conn.get_raw_connection()
            await raw_conn.driver_connection.execute(sql_body)
            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            logger.exception("Mileage correction for train %s failed", correction.id_train)
            return _json({"status": "error", "message": f"Ошибка выполнения: {e}"})

        now = datetime.now()
        log_file = LOG_DIR / f"mileage_correction_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join([
                f"=== Execute mileage correction: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
                f"Train={correction.id_train}, active={correction.id_active}, "
                f"rows deleted={correction.delete_count}, "
                f"counter set to {correction.counter_value} at {correction.counter_date}",
                "",
                sql_body,
                "",
            ]))
        logger.info("Corrected mileage of train %d: %d rows deleted, counter = %d at %s, log: %s",
                    correction.id_train, correction.delete_count, correction.counter_value,
                    correction.counter_date, log_file)

        return _json({
            "status": "ok",
            "message": f"Удалено записей: {correction.delete_count}. "
                       f"Счётчик установлен: {correction.counter_value} "
                       f"на {correction.counter_date.strftime('%Y-%m-%d %H:%M:%S')}",
        })
