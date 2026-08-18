import asyncio
import json
import time

import pytest

import progress_tasks


@pytest.fixture(autouse=True)
def clean_registry():
    progress_tasks._progress.clear()
    progress_tasks._tasks.clear()
    yield
    progress_tasks._progress.clear()
    progress_tasks._tasks.clear()


def payload(response) -> dict:
    return json.loads(response.content)


@pytest.mark.asyncio
async def test_start_task_runs_runner_and_reports_progress():
    started = asyncio.Event()

    async def runner(progress: dict) -> None:
        started.set()
        progress.update(processed=1, status="done", count=1)

    task_id = payload(progress_tasks.start_task(1, runner))["task_id"]
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.gather(*progress_tasks._tasks.values())

    state = payload(progress_tasks.progress_response(task_id))
    assert state == {"processed": 1, "total": 1, "phase": "validating", "status": "done", "count": 1}


@pytest.mark.asyncio
async def test_progress_starts_running_before_the_runner_finishes():
    release = asyncio.Event()

    async def runner(progress: dict) -> None:
        await release.wait()

    task_id = payload(progress_tasks.start_task(7, runner))["task_id"]
    assert payload(progress_tasks.progress_response(task_id)) == {
        "processed": 0, "total": 7, "phase": "validating", "status": "running",
    }
    release.set()
    await asyncio.gather(*progress_tasks._tasks.values())


@pytest.mark.asyncio
async def test_finished_task_is_dropped_from_the_task_registry():
    async def runner(progress: dict) -> None:
        return None

    task_id = payload(progress_tasks.start_task(0, runner))["task_id"]
    await asyncio.gather(*progress_tasks._tasks.values())
    await asyncio.sleep(0)

    assert task_id not in progress_tasks._tasks
    assert task_id in progress_tasks._progress


def test_unknown_task_id_reports_an_error():
    state = progress_tasks.progress_response("nope")
    assert payload(state)["status"] == "error"


@pytest.mark.asyncio
async def test_stale_states_are_dropped_on_the_next_start():
    async def runner(progress: dict) -> None:
        return None

    stale_id = payload(progress_tasks.start_task(0, runner))["task_id"]
    progress_tasks._progress[stale_id]["created_at"] = time.time() - progress_tasks.PROGRESS_TTL_SECONDS - 1

    progress_tasks.start_task(0, runner)
    await asyncio.gather(*progress_tasks._tasks.values())

    assert stale_id not in progress_tasks._progress
