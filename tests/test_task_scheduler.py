import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.models.scheduled_task import ScheduledTask, TaskType
from app.services.task_scheduler import TaskSchedulerService


class FakeResult:
    def __init__(self, items=None):
        self._items = items or []

    def scalars(self):
        return FakeScalars(self._items)


class FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class FakeSession:
    def __init__(self, task=None, server=None, query_results=None):
        self._task = task
        self._server = server
        self._query_results = query_results or []
        self.committed = False

    async def get(self, model, obj_id):
        if model == ScheduledTask:
            return self._task
        return self._server

    async def execute(self, stmt):
        return FakeResult(self._query_results)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass


class TaskSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scheduler = TaskSchedulerService()
        self.scheduler.scheduler = None

    async def test_execute_task_skips_when_only_running_and_server_stopped(self):
        task = ScheduledTask(
            id=1,
            name="Test Task",
            task_type=TaskType.START,
            cron_expression="0 0 * * *",
            enabled=True,
            condition="only_running",
            server_id=42,
        )
        server = MagicMock()
        server.id = 42

        fake_session = FakeSession(task=task, server=server)
        mock_sm = MagicMock()
        mock_sm.is_running.return_value = False

        with patch(
            "app.services.task_scheduler.async_session",
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=fake_session),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            with patch("app.services.server_manager.server_manager", mock_sm):
                await self.scheduler._execute_task(1)

        self.assertEqual(task.last_result, "Skipped: server not running")
        self.assertIsNotNone(task.last_run)

    async def test_execute_task_skips_when_only_stopped_and_server_running(self):
        task = ScheduledTask(
            id=2,
            name="Test Task",
            task_type=TaskType.STOP,
            cron_expression="0 0 * * *",
            enabled=True,
            condition="only_stopped",
            server_id=43,
        )
        server = MagicMock()
        server.id = 43

        fake_session = FakeSession(task=task, server=server)
        mock_sm = MagicMock()
        mock_sm.is_running.return_value = True

        with patch(
            "app.services.task_scheduler.async_session",
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=fake_session),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            with patch("app.services.server_manager.server_manager", mock_sm):
                await self.scheduler._execute_task(2)

        self.assertEqual(task.last_result, "Skipped: server not stopped")
        self.assertIsNotNone(task.last_run)

    async def test_execute_task_runs_when_only_running_and_docker_running(self):
        task = ScheduledTask(
            id=3,
            name="Test Task",
            task_type=TaskType.STOP,
            cron_expression="0 0 * * *",
            enabled=True,
            condition="only_running",
            server_id=44,
        )
        server = MagicMock()
        server.id = 44

        fake_session = FakeSession(task=task, server=server)
        mock_sm = MagicMock()
        mock_sm.is_running.return_value = True
        mock_sm.stop_server = AsyncMock()

        with patch(
            "app.services.task_scheduler.async_session",
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=fake_session),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            with patch("app.services.server_manager.server_manager", mock_sm):
                await self.scheduler._execute_task(3)

        mock_sm.stop_server.assert_awaited_once_with(44)
        self.assertEqual(task.last_result, "Success")

    async def test_command_task_rejects_injection(self):
        task = ScheduledTask(
            id=4,
            name="Bad Command",
            task_type=TaskType.COMMAND,
            cron_expression="0 0 * * *",
            enabled=True,
            condition="always",
            server_id=45,
            command="say hello; rm -rf /",
        )
        server = MagicMock()
        server.id = 45

        fake_session = FakeSession(task=task, server=server)

        with patch(
            "app.services.task_scheduler.async_session",
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=fake_session),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            await self.scheduler._execute_task(4)

        self.assertIn("forbidden characters", task.last_result)

    async def test_sync_uptime_schedule_skips_malformed_times(self):
        from app.models.server import Server

        server = Server(
            id=1,
            name="Test",
            path="/tmp/test",
            uptime_schedule='{"start_time": "not-a-time", "stop_time": "22:00"}',
        )

        fake_session = FakeSession(server=server)
        removed_ids = []

        async def fake_remove_task(task_id):
            removed_ids.append(task_id)

        with patch(
            "app.services.task_scheduler.async_session",
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=fake_session),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            with patch.object(
                self.scheduler, "remove_task", side_effect=fake_remove_task
            ):
                with patch.object(
                    self.scheduler, "add_task", new_callable=AsyncMock
                ) as add_mock:
                    await self.scheduler.sync_uptime_schedule(1)

        add_mock.assert_not_awaited()

    async def test_sync_uptime_schedule_collects_ids_before_removing(self):
        from app.models.server import Server

        server = Server(
            id=2,
            name="Test",
            path="/tmp/test",
            uptime_schedule='{"start_time": "08:00", "stop_time": "22:00"}',
        )

        fake_session = FakeSession(server=server)
        removed_ids = []

        async def fake_remove_task(task_id):
            removed_ids.append(task_id)

        with patch(
            "app.services.task_scheduler.async_session",
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=fake_session),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            with patch.object(
                self.scheduler, "remove_task", side_effect=fake_remove_task
            ):
                with patch.object(self.scheduler, "add_task", new_callable=AsyncMock):
                    await self.scheduler.sync_uptime_schedule(2)

        # Should not raise and should complete removal of any old tasks
        # Since no old tasks exist, removed_ids should be empty
        self.assertEqual(removed_ids, [])
