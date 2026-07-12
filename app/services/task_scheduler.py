import logging
import re
import time
from datetime import datetime, timezone

from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.database import async_session
from app.models.scheduled_task import ScheduledTask, TaskType

logger = logging.getLogger(__name__)


class TaskSchedulerService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.scheduler = None
        return cls._instance

    def set_scheduler(self, scheduler):
        self.scheduler = scheduler

    async def load_tasks(self):
        async with async_session() as session:
            result = await session.execute(
                select(ScheduledTask).where(ScheduledTask.enabled.is_(True))
            )
            tasks = result.scalars().all()
            for task in tasks:
                try:
                    self._register_job(task)
                except Exception as e:
                    logger.error(
                        f"Failed to register task {task.id} ({task.name}): {e}"
                    )
            logger.info(f"Loaded {len(tasks)} scheduled task(s)")

    def _register_job(self, task: ScheduledTask):
        if not self.scheduler:
            return
        trigger = CronTrigger.from_crontab(task.cron_expression)
        self.scheduler.add_job(
            self._execute_task,
            trigger,
            args=[task.id],
            id=f"task_{task.id}",
            replace_existing=True,
            max_instances=1,
        )

    async def _execute_task(self, task_id: int):
        from app.services.backup_manager import backup_manager
        from app.services.server_manager import server_manager

        async with async_session() as session:
            task = await session.get(ScheduledTask, task_id)
            if not task or not task.enabled:
                return

            # Check condition
            if task.condition and task.condition != "always" and task.server_id:
                from app.models.server import Server

                server = await session.get(Server, task.server_id)
                if server:
                    is_running = server_manager.is_running(server.id)
                    if task.condition == "only_running" and not is_running:
                        task.last_result = "Skipped: server not running"
                        task.last_run = datetime.now(timezone.utc)
                        await session.commit()
                        return
                    if task.condition == "only_stopped" and is_running:
                        task.last_result = "Skipped: server not stopped"
                        task.last_run = datetime.now(timezone.utc)
                        await session.commit()
                        return

            start_time = time.monotonic()
            try:
                if task.task_type == TaskType.START:
                    await server_manager.start_server(task.server_id)
                elif task.task_type == TaskType.STOP:
                    await server_manager.stop_server(task.server_id)
                elif task.task_type == TaskType.RESTART:
                    await server_manager.restart_server(task.server_id)
                elif task.task_type == TaskType.BACKUP:
                    await backup_manager.create_backup(task.server_id)
                elif task.task_type == TaskType.COMMAND:
                    if task.command and task.server_id:
                        if re.search(r"[;|&$`<>\n\r]", task.command):
                            raise ValueError("Command contains forbidden characters")
                        await server_manager.send_command(task.server_id, task.command)
                elif task.task_type == TaskType.STEAM_UPDATE:
                    from app.models.server import Server
                    from app.services.server_updater import server_updater

                    server = await session.get(Server, task.server_id)
                    if server and server.steam_app_id:
                        result = await server_updater.update_server(
                            task.server_id,
                            session,
                            create_backup=False,
                            interactive=False,
                        )
                        if not result.get("ok"):
                            raise RuntimeError(
                                result.get("message") or "Steam update failed"
                            )
                elif task.task_type == TaskType.STEAM_VALIDATE:
                    from app.models.server import Server
                    from app.services.steamcmd import steamcmd

                    server = await session.get(Server, task.server_id)
                    if server and server.steam_app_id:
                        kwargs, error = await steamcmd.get_server_install_kwargs(
                            session, server, interactive=False
                        )
                        if error:
                            raise RuntimeError(error)
                        result = await steamcmd.validate_server(
                            app_id=server.steam_app_id,
                            install_dir=server.path,
                            **kwargs,
                        )
                        if not result.get("ok"):
                            raise RuntimeError(
                                result.get("message") or "Steam validation failed"
                            )
                        if result.get("build_id"):
                            server.steam_build_id = result["build_id"]
                        server.steam_last_update = datetime.now(timezone.utc)
                        await session.commit()

                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                task.last_run = datetime.now(timezone.utc)
                task.last_result = "Success"
                task.last_duration_ms = elapsed_ms
                await session.commit()
                logger.info(
                    f"Executed task {task.name} (type={task.task_type.value}) in {elapsed_ms}ms"
                )
            except Exception as e:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                task.last_run = datetime.now(timezone.utc)
                task.last_result = f"Error: {e}"
                task.last_duration_ms = elapsed_ms
                await session.commit()
                logger.error(f"Failed to execute task {task.name}: {e}")

    async def add_task(self, task: ScheduledTask) -> ScheduledTask:
        CronTrigger.from_crontab(task.cron_expression)

        async with async_session() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)

            if task.enabled:
                self._register_job(task)

            return task

    async def run_task_now(self, task_id: int):
        await self._execute_task(task_id)

    async def remove_task(self, task_id: int):
        if self.scheduler:
            try:
                self.scheduler.remove_job(f"task_{task_id}")
            except JobLookupError:
                pass

        async with async_session() as session:
            task = await session.get(ScheduledTask, task_id)
            if task:
                await session.delete(task)
                await session.commit()

    async def toggle_task(self, task_id: int):
        async with async_session() as session:
            task = await session.get(ScheduledTask, task_id)
            if not task:
                return

            task.enabled = not task.enabled
            await session.commit()

            if task.enabled:
                self._register_job(task)
            elif self.scheduler:
                try:
                    self.scheduler.remove_job(f"task_{task_id}")
                except JobLookupError:
                    pass

    def validate_cron(self, expression: str) -> bool:
        try:
            CronTrigger.from_crontab(expression)
            return True
        except (ValueError, KeyError):
            return False

    async def sync_uptime_schedule(self, server_id: int):
        """Create/update start and stop cron tasks from a server's uptime schedule JSON."""
        import json

        async with async_session() as session:
            from app.models.server import Server

            server = await session.get(Server, server_id)
            if not server:
                return

            # Remove existing uptime tasks for this server
            result = await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.server_id == server_id,
                    ScheduledTask.name.like("Uptime:%"),
                )
            )
            old_task_ids = [t.id for t in result.scalars().all()]
            for old_task_id in old_task_ids:
                await self.remove_task(old_task_id)

            if not server.uptime_schedule:
                return

            try:
                schedule = json.loads(server.uptime_schedule)
            except (json.JSONDecodeError, TypeError):
                return

            start_time = schedule.get("start_time", "08:00")
            stop_time = schedule.get("stop_time", "22:00")
            days = schedule.get("days", [0, 1, 2, 3, 4, 5, 6])
            warning_min = schedule.get("warning_minutes", 0)

            # Validate and parse time strings
            try:
                start_dt = datetime.strptime(start_time, "%H:%M")
                stop_dt = datetime.strptime(stop_time, "%H:%M")
            except ValueError:
                logger.warning(
                    f"Malformed uptime schedule times for server {server_id}: "
                    f"start={start_time}, stop={stop_time}"
                )
                return

            # Convert days list to cron day_of_week
            dow = ",".join(str(d) for d in days) if days else "*"
            sh, sm = start_dt.hour, start_dt.minute
            eh, em = stop_dt.hour, stop_dt.minute

            # Create start task
            start_task = ScheduledTask(
                server_id=server_id,
                name="Uptime: Start",
                task_type=TaskType.START,
                cron_expression=f"{sm} {sh} * * {dow}",
                enabled=True,
                condition="only_stopped",
            )
            await self.add_task(start_task)

            # Create warning task if warning_minutes > 0
            if warning_min and warning_min > 0:
                from datetime import datetime as dt
                from datetime import timedelta

                try:
                    stop_dt = dt.strptime(stop_time, "%H:%M")
                    warn_dt = stop_dt - timedelta(minutes=warning_min)
                    warn_task = ScheduledTask(
                        server_id=server_id,
                        name="Uptime: Shutdown Warning",
                        task_type=TaskType.COMMAND,
                        cron_expression=f"{warn_dt.minute} {warn_dt.hour} * * {dow}",
                        command=f"say Server shutting down in {warning_min} minutes!",
                        enabled=True,
                        condition="only_running",
                    )
                    await self.add_task(warn_task)
                except (ValueError, TypeError):
                    pass

            # Create stop task
            stop_task = ScheduledTask(
                server_id=server_id,
                name="Uptime: Stop",
                task_type=TaskType.STOP,
                cron_expression=f"{em} {eh} * * {dow}",
                enabled=True,
                condition="only_running",
            )
            await self.add_task(stop_task)


task_scheduler = TaskSchedulerService()
