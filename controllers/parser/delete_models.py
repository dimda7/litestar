import json
import logging
from datetime import datetime

from sqlalchemy import text
from litestar import Controller, post
from litestar.connection.request import Request
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Response

from db_manager import get_session_maker
from parser_storage import LOG_DIR
from progress_tasks import start_task
from sql_builders import models as models_sql


logger = logging.getLogger("parser")


def parse_delete_ids(rows: list[dict], progress: dict | None = None) -> tuple[list[dict], list[int]]:
    errors: list[dict] = []
    valid_ids: list[int] = []
    if progress is not None:
        progress.update(processed=0, total=len(rows), phase="validating")
    for idx, row in enumerate(rows):
        row_num = idx + 1
        if progress is not None:
            progress["processed"] = row_num
        row_id = row.get("id")
        if not row_id:
            errors.append({"row": row_num, "field": "id",
                           "message": "Поле 'id' отсутствует или пустое"})
            continue
        valid_ids.append(int(row_id))
    return errors, valid_ids


class DeleteModelsController(Controller):
    path = "/parser"

    @post("/delete-rows/start")
    async def delete_rows_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start background generation of the SQL file deleting rows from models; returns a task_id to poll."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_delete_generate(progress, rows))

    async def _run_delete_generate(self, progress: dict, rows: list[dict]) -> None:
        errors, valid_ids = parse_delete_ids(rows, progress=progress)
        if errors:
            progress.update(status="error", errors=errors)
            return

        sql_lines = models_sql.delete_models(valid_ids)
        progress.update(status="done", sql="\n".join(sql_lines), count=len(sql_lines))

    @post("/execute-delete/start")
    async def execute_delete_start(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> Response:
        """Start the background atomic delete of rows from public.models; returns a task_id to poll."""
        rows: list[dict] = json.loads(data.get("rows", "[]"))
        return start_task(len(rows), lambda progress: self._run_delete_execute(progress, rows))

    async def _run_delete_execute(self, progress: dict, rows: list[dict]) -> None:
        errors, valid_ids = parse_delete_ids(rows, progress=progress)
        if errors:
            progress.update(status="error", errors=errors)
            return

        if not valid_ids:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": "Нет валидных строк для удаления"}])
            return

        progress.update(processed=0, total=len(valid_ids), phase="executing")
        try:
            session_maker = get_session_maker()
            async with session_maker() as session:
                try:
                    for i, rid in enumerate(valid_ids, start=1):
                        await session.execute(text("DELETE FROM public.models WHERE id = :id"), {"id": rid})
                        progress["processed"] = i
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
                    return
        except Exception as e:
            progress.update(status="error", errors=[{"row": 0, "field": "*", "message": f"Ошибка выполнения: {e}"}])
            return

        now = datetime.now()
        log_lines = [
            f"=== Execute Delete: {now.strftime('%Y-%m-%d %H:%M:%S')} ===",
            f"Rows deleted: {len(valid_ids)}",
            "",
            *models_sql.delete_models(valid_ids),
            "",
        ]
        log_file = LOG_DIR / f"delete_models_{now.strftime('%Y-%m-%d_%H-%M-%S')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        logger.info("SQL executed: %d rows deleted, log saved to %s", len(valid_ids), log_file)

        progress.update(status="done", count=len(valid_ids), message=f"Успешно удалено {len(valid_ids)} строк")
