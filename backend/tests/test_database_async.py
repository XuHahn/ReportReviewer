import asyncio
import threading

import pytest

import database


@pytest.mark.asyncio
async def test_run_async_does_not_detach_worker_on_cancellation():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def database_operation() -> None:
        started.set()
        release.wait(timeout=1)
        finished.set()

    task = asyncio.create_task(database._run_async(database_operation))
    assert await asyncio.to_thread(started.wait, 1)

    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
