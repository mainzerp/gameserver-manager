import asyncio

import pytest

from app.services.task_registry import TaskRegistry, task_registry


class TestTaskRegistry:
    def test_singleton(self):
        a = TaskRegistry()
        b = TaskRegistry()
        assert a is b

    @pytest.mark.asyncio
    async def test_spawn_tracks_task_and_logs_exception(self, caplog):
        caplog.set_level("ERROR")

        async def failing_task():
            raise RuntimeError("boom")

        task_registry.spawn(failing_task())
        await task_registry.flush()

        assert "boom" in caplog.text
        assert len(task_registry._tasks) == 0

    @pytest.mark.asyncio
    async def test_flush_waits_for_pending_tasks(self):
        completed = []

        async def slow_task():
            await asyncio.sleep(0.01)
            completed.append(True)

        task_registry.spawn(slow_task())
        assert len(task_registry._tasks) == 1

        await task_registry.flush()

        assert completed == [True]
        assert len(task_registry._tasks) == 0

    @pytest.mark.asyncio
    async def test_cancellation_is_not_logged(self, caplog):
        caplog.set_level("ERROR")

        async def cancelled_task():
            raise asyncio.CancelledError()

        task_registry.spawn(cancelled_task())
        await task_registry.flush()

        assert "boom" not in caplog.text
        assert len(task_registry._tasks) == 0
