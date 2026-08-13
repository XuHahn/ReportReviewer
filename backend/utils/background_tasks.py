"""Application-owned background task registry with graceful shutdown."""

import asyncio
from collections.abc import Coroutine
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

_tasks: set[asyncio.Task] = set()
_tasks_by_key: dict[str, asyncio.Task] = {}


def background_task_running(key: str) -> bool:
    task = _tasks_by_key.get(key)
    return bool(task and not task.done())


def start_background_task(
    coroutine: Coroutine[Any, Any, Any], *, name: str, key: str = "",
) -> asyncio.Task:
    existing = _tasks_by_key.get(key) if key else None
    if existing and not existing.done():
        coroutine.close()
        logger.info("background_task_deduplicated", task_name=name, task_key=key)
        return existing

    task = asyncio.create_task(coroutine, name=name)
    _tasks.add(task)
    if key:
        _tasks_by_key[key] = task

    def _done(completed: asyncio.Task) -> None:
        _tasks.discard(completed)
        if key and _tasks_by_key.get(key) is completed:
            _tasks_by_key.pop(key, None)
        if completed.cancelled():
            logger.info("background_task_cancelled", task_name=name, task_key=key)
            return
        error = completed.exception()
        if error:
            logger.error(
                "background_task_failed", task_name=name, task_key=key,
                error=str(error)[:500],
            )

    task.add_done_callback(_done)
    return task


async def shutdown_background_tasks() -> None:
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
    _tasks_by_key.clear()


async def cancel_background_task(key: str) -> bool:
    task = _tasks_by_key.get(key)
    if not task or task.done():
        return False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return True
