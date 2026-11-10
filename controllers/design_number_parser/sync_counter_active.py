import json
from datetime import datetime

from litestar import Controller, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from parser_storage import LOG_DIR
from sql_builders.actives import MILEAGE_COUNTER_TYPE_ID

from .common import PATH, logger
from .counter_group import validate_counter_group_rows

# public.counter_type ids: 1 'Моточасы', 2 'Время', 3 'Пробег'
# (MILEAGE_COUNTER_TYPE_ID, sql_builders/actives.py).
MOTOR_HOURS_COUNTER_TYPE_ID = 1


TIME_COUNTER_TYPE_ID = 2


# public.frequency_type ids of the units a counter is measured in.
KM_FREQUENCY_TYPE_ID = 5


ENGINE_HOURS_FREQUENCY_TYPE_ID = 7


MM_FREQUENCY_TYPE_ID = 8


# The counter_active rows created by the counter_group sync carry the unit their
# counter is measured in: 'Моточасы' counts engine hours, 'Пробег' kilometres,
# and every remaining counter type (4..30 — wheel and brake disc wear) is
# measured in millimetres. 'Время' gets no counter_active row at all.
FREQUENCY_TYPE_BY_COUNTER_TYPE: dict[int, int] = {
    MOTOR_HOURS_COUNTER_TYPE_ID: ENGINE_HOURS_FREQUENCY_TYPE_ID,
    MILEAGE_COUNTER_TYPE_ID: KM_FREQUENCY_TYPE_ID,
}


def _frequency_type_for(counter_type_id: int) -> int:
    return FREQUENCY_TYPE_BY_COUNTER_TYPE.get(counter_type_id, MM_FREQUENCY_TYPE_ID)


class SyncCounterActiveController(Controller):
    path = PATH

    @post("/sync-counter-active")
    async def sync_counter_active(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_counter_group_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        if not valid_rows:
            return Response(
                content=json.dumps(
                    {
                        "status": "error",
                        "errors": [{"row": 0, "field": "*", "message": "Нет валидных строк для синхронизации"}],
                    }
                ),
                status_code=200,
                media_type="application/json",
            )

        deleted_total = 0
        inserted_total = 0
        sync_details: list[str] = []

        try:
            for dn_id, number, cg_id in valid_rows:
                ct_result = await db_session.execute(
                    text(
                        "SELECT ctg.id_counter_type "
                        "FROM public.counter_type_to_group ctg "
                        "WHERE ctg.id_counter_group = :cg_id"
                    ),
                    {"cg_id": cg_id},
                )
                new_types = {row[0] for row in ct_result.all()}

                active_result = await db_session.execute(
                    text("SELECT id FROM public.actives WHERE id_design_number = :dn_id"),
                    {"dn_id": dn_id},
                )
                active_ids = [row[0] for row in active_result.all()]

                if not active_ids:
                    continue

                deleted_dn = 0
                inserted_dn = 0

                for aid in active_ids:
                    existing_result = await db_session.execute(
                        text("SELECT id, id_counter_type FROM public.counter_active WHERE id_active = :aid"),
                        {"aid": aid},
                    )
                    existing_rows = existing_result.all()

                    for ca_id, ca_type in existing_rows:
                        if ca_type not in new_types:
                            await db_session.execute(
                                text("DELETE FROM public.counter_active WHERE id = :id"),
                                {"id": ca_id},
                            )
                            deleted_dn += 1

                    existing_types = {row[1] for row in existing_rows}
                    missing_types = new_types - existing_types

                    for ct_id in missing_types:
                        if ct_id == TIME_COUNTER_TYPE_ID:
                            continue
                        freq = _frequency_type_for(ct_id)
                        now_ts = datetime.now()
                        await db_session.execute(
                            text(
                                "INSERT INTO public.counter_active "
                                "(id_active, date, value, id_counter_type, id_frequency_type, is_train, date_create) "
                                "VALUES (:aid, :dt, 0, :ct_id, :freq, false, :dt)"
                            ),
                            {"aid": aid, "dt": now_ts, "ct_id": ct_id, "freq": freq},
                        )
                        inserted_dn += 1

                deleted_total += deleted_dn
                inserted_total += inserted_dn
                sync_details.append(
                    f"design_number id={dn_id} number='{number}' counter_group={cg_id}: "
                    f"типы={sorted(new_types)}, actives={len(active_ids)}, "
                    f"удалено={deleted_dn}, добавлено={inserted_dn}"
                )

            await db_session.commit()
        except Exception as e:
            await db_session.rollback()
            return Response(
                content=json.dumps(
                    {"status": "error", "errors": [{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}]}
                ),
                status_code=200,
                media_type="application/json",
            )

        now = datetime.now()
        log_lines = [
            f"=== Sync counter_active: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows processed: {len(valid_rows)}",
            f"Counter_active удалено: {deleted_total}, добавлено: {inserted_total}",
            "",
        ]
        for dn_id, number, cg_id in valid_rows:
            log_lines.append(f"design_number id={dn_id} number='{number}' counter_group={cg_id}")
        if sync_details:
            log_lines.append("")
            log_lines.append("Детали синхронизации:")
            for detail in sync_details:
                log_lines.append(f"  {detail}")
        log_lines.append("")

        log_file = LOG_DIR / f"sync_counter_active_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info(
            "Synced counter_active for %d rows, deleted %d, inserted %d, log: %s",
            len(valid_rows),
            deleted_total,
            inserted_total,
            log_file,
        )

        return Response(
            content=json.dumps(
                {
                    "status": "ok",
                    "count": len(valid_rows),
                    "deleted_counter_active": deleted_total,
                    "inserted_counter_active": inserted_total,
                    "message": f"Синхронизация counter_active: {len(valid_rows)} design_number, удалено {deleted_total}, добавлено {inserted_total}",
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    @post("/generate-sql-counter-active")
    async def generate_sql_counter_active(
        self,
        request: Request,
        db_session: AsyncSession,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        errors, valid_rows = await validate_counter_group_rows(db_session, rows)

        if errors:
            return Response(
                content=json.dumps({"status": "error", "errors": errors}),
                status_code=200,
                media_type="application/json",
            )

        sql_lines: list[str] = []

        for dn_id, _number, cg_id in valid_rows:
            ct_result = await db_session.execute(
                text(
                    "SELECT ctg.id_counter_type "
                    "FROM public.counter_type_to_group ctg "
                    "WHERE ctg.id_counter_group = :cg_id"
                ),
                {"cg_id": cg_id},
            )
            new_types = {row[0] for row in ct_result.all()}

            active_result = await db_session.execute(
                text("SELECT id FROM public.actives WHERE id_design_number = :dn_id"),
                {"dn_id": dn_id},
            )
            active_ids = [row[0] for row in active_result.all()]

            if not active_ids:
                continue

            for aid in active_ids:
                existing_result = await db_session.execute(
                    text("SELECT id, id_counter_type FROM public.counter_active WHERE id_active = :aid"),
                    {"aid": aid},
                )
                existing_rows = existing_result.all()

                for ca_id, ca_type in existing_rows:
                    if ca_type not in new_types:
                        sql_lines.append(f"DELETE FROM public.counter_active WHERE id = {ca_id};")

                existing_types = {row[1] for row in existing_rows}
                missing_types = new_types - existing_types

                for ct_id in missing_types:
                    if ct_id == TIME_COUNTER_TYPE_ID:
                        continue
                    freq = _frequency_type_for(ct_id)
                    sql_lines.append(
                        f"INSERT INTO public.counter_active "
                        f"(id_active, date, value, id_counter_type, id_frequency_type, is_train, date_create) "
                        f"VALUES ({aid}, NOW(), 0, {ct_id}, {freq}, false, NOW());"
                    )

        content = "\n".join(sql_lines)
        return Response(
            content=json.dumps({"status": "ok", "sql": content, "count": len(sql_lines)}),
            status_code=200,
            media_type="application/json",
        )
