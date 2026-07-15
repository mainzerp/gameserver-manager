"""Central registry for fire-and-forget asyncio tasks.

Provides a safe way to spawn background coroutines without losing track of
unhandled exceptions. All registered tasks are awaited during application
shutdown so the lifespan context can clean up gracefully.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


class TaskRegistry:
    """Singleton registry that tracks background asyncio tasks.

    Use this instead of bare ``asyncio.create_task`` for any fire-and-forget
    work started by routers or services. Crashed tasks are logged and the
    registry can wait for all pending tasks during shutdown.
    """

    _instance: "TaskRegistry | None" = None

    def __new__(cls) -> "TaskRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tasks: set[asyncio.Task] = set()
        return cls._instance

    def _discard(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)

    def _log_exception(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception(
                "Background task %r failed: %s", task.get_name(), exc, exc_info=exc
            )

    def spawn(self, coro, *, name: str | None = None) -> asyncio.Task:
        """Create a tracked task from *coro* and return it.

        The task is removed from the registry when it finishes. If it raises
        an exception (other than CancelledError), the exception is logged.
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._discard)
        task.add_done_callback(self._log_exception)
        return task

    async def flush(self) -> None:
        """Wait for all currently tracked tasks to finish.

        Exceptions are suppressed because each task is already logged via
        ``_log_exception``; this method is intended for graceful shutdown.
        """
        if not self._tasks:
            return
        pending = list(self._tasks)
        logger.debug("Waiting for %d background task(s) to finish", len(pending))
        await asyncio.gather(*pending, return_exceptions=True)


task_registry = TaskRegistry()
