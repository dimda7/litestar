import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable

from litestar.response import Response

# Validation issues several database queries per row, and on large files
# (thousands of rows) that takes seconds to minutes — progress is served through
# a separate poll rather than holding one HTTP request open all that time.
PROGRESS_TTL_SECONDS = 15 * 60

_progress: dict[str, dict] = {}
# asyncio only keeps a weak reference to fire-and-forget tasks — without
# storing it explicitly the task can be garbage collected before it finishes.
_tasks: dict[str, asyncio.Task] = {}


def _json_response(payload: dict) -> Response:
    return Response(
        content=json.dumps(payload),
        status_code=200,
        media_type="application/json",
    )


def _cleanup() -> None:
    cutoff = time.time() - PROGRESS_TTL_SECONDS
    stale = [tid for tid, state in _progress.items() if state["created_at"] < cutoff]
    for tid in stale:
        _progress.pop(tid, None)


def error_response(message: str) -> Response:
    """The shape the parser pages expect for an error that has no particular row."""
    return _json_response({"status": "error", "errors": [{"row": 0, "field": "*", "message": message}]})


def start_task(total: int, runner: Callable[[dict], Awaitable[None]]) -> Response:
    """Run `runner` against a fresh progress state in the background; answer with its task_id."""
    _cleanup()
    task_id = uuid.uuid4().hex
    progress: dict = {"processed": 0, "total": total, "phase": "validating",
                      "status": "running", "created_at": time.time()}
    _progress[task_id] = progress
    task = asyncio.ensure_future(runner(progress))
    task.add_done_callback(lambda t: _tasks.pop(task_id, None))
    _tasks[task_id] = task
    return _json_response({"task_id": task_id})


def progress_response(task_id: str) -> Response:
    state = _progress.get(task_id)
    if state is None:
        return _json_response({"status": "error", "errors": [{"row": 0, "field": "*", "message": "Задача не найдена или устарела"}]})
    return _json_response({k: v for k, v in state.items() if k != "created_at"})
