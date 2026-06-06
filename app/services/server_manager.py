import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.server import Server, ServerStatus, ServerType
from app.services.log_manager import log_manager
from app.services.notification_service import notification_service

logger = logging.getLogger(__name__)


def _docker_enabled():
    return settings.docker_isolation_enabled


@dataclass
class _CrashState:
    count: int = 0
    last_crash_time: float = 0.0
    restart_task: asyncio.Task | None = None
    stability_task: asyncio.Task | None = None


class ServerProcess:
    def __init__(self, server_id: int, process: asyncio.subprocess.Process):
        self.server_id = server_id
        self.process = process
        self.log_lines: list[str] = []
        self.max_log_lines = 500
        self.subscribers: list[Callable] = []
        self._read_task: asyncio.Task | None = None
        self.server_name: str | None = None

    async def start_reading(self):
        self._read_task = asyncio.create_task(self._read_output())

    async def _read_output(self):
        try:
            while True:
                if self.process.stdout is None:
                    break
                line = await self.process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                self.log_lines.append(decoded)
                if len(self.log_lines) > self.max_log_lines:
                    self.log_lines.pop(0)
                if self.server_name:
                    await log_manager.write_line(self.server_name, decoded)
                for callback in self.subscribers:
                    try:
                        await callback(decoded)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass

    async def send_command(self, command: str):
        if self.process.stdin:
            self.process.stdin.write((command + "\n").encode("utf-8"))
            await self.process.stdin.drain()

    def subscribe(self, callback: Callable):
        self.subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        if callback in self.subscribers:
            self.subscribers.remove(callback)


class ServerManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._processes = {}
            cls._instance._docker_processes: dict[int, str] = {}
            cls._instance._readiness_generations: dict[int, int] = {}
            cls._instance._crash_states: dict[int, _CrashState] = {}
            cls._instance._start_locks: dict[int, asyncio.Lock] = defaultdict(
                asyncio.Lock
            )
        return cls._instance

    @property
    def processes(self) -> dict[int, ServerProcess]:
        return self._processes

    @staticmethod
    def _write_pid_file(server_path: str, pid: int):
        try:
            with open(os.path.join(server_path, "gsm.pid"), "w") as f:
                f.write(str(pid))
        except OSError as e:
            logger.warning(f"Failed to write PID file: {e}")

    @staticmethod
    def _read_pid_file(server_path: str) -> int | None:
        pid_path = os.path.join(server_path, "gsm.pid")
        try:
            with open(pid_path, "r") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _remove_pid_file(server_path: str):
        pid_path = os.path.join(server_path, "gsm.pid")
        try:
            if os.path.exists(pid_path):
                os.remove(pid_path)
        except OSError as e:
            logger.warning(f"Failed to remove PID file: {e}")

    def _get_crash_state(self, server_id: int) -> _CrashState:
        if server_id not in self._crash_states:
            self._crash_states[server_id] = _CrashState()
        return self._crash_states[server_id]

    def _reset_crash_state(self, server_id: int):
        state = self._crash_states.get(server_id)
        if state:
            if state.restart_task and not state.restart_task.done():
                state.restart_task.cancel()
            if state.stability_task and not state.stability_task.done():
                state.stability_task.cancel()
            state.count = 0
            state.last_crash_time = 0.0
            state.restart_task = None
            state.stability_task = None

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    async def start_server(
        self, server_id: int, skip_steam_update: bool = False
    ) -> dict:
        """Start a server. Returns {ok: bool, error: str|None}."""
        async with self._start_locks[server_id]:
            if server_id in self._processes:
                return {"ok": False, "error": "Server is already running"}

            async with async_session() as session:
                server = await session.get(Server, server_id)
                if not server:
                    return {"ok": False, "error": "Server not found"}

                from app.config import settings as _settings

                if server.node_id and _settings.multi_node_enabled:
                    from app.models.node import Node
                    from app.services.node_manager import node_manager

                    node = await session.get(Node, server.node_id)
                    if node and not node.is_local:
                        return await node_manager.proxy_command(
                            node, f"servers/{server_id}/start"
                        )

                # Pre-flight checks for Minecraft Java
                if server.server_type == ServerType.MINECRAFT_JAVA:
                    jar_path = os.path.join(server.path, server.executable)
                    if not os.path.isfile(jar_path):
                        return {
                            "ok": False,
                            "error": f"server.jar not found: {jar_path}. "
                            f"Please place the JAR file there or specify an MC version when creating the server.",
                        }
                    # Check if Java is available and correct version
                    from app.services.java_manager import (
                        detect_java_version,
                        get_required_java_version,
                    )

                    try:
                        detected = await detect_java_version(server.java_path)
                        if detected is None:
                            return {
                                "ok": False,
                                "error": f"Java not available at '{server.java_path}'. "
                                f"Please install Java or adjust the path.",
                            }
                        if server.mc_version:
                            required = get_required_java_version(server.mc_version)
                            if detected < required:
                                return {
                                    "ok": False,
                                    "error": f"Java {detected} found, but MC {server.mc_version} "
                                    f"requires Java {required}+. "
                                    f"Please install Java {required} or adjust the path.",
                                }
                        logger.info(
                            f"Java check OK: version {detected} at '{server.java_path}'"
                        )
                    except FileNotFoundError:
                        return {
                            "ok": False,
                            "error": f"Java not found: '{server.java_path}'. "
                            f"Please install the appropriate Java version.",
                        }

                # Pre-flight: Steam update-on-start
                if (
                    not skip_steam_update
                    and server.server_type == ServerType.STEAM
                    and server.steam_update_on_start
                    and server.steam_app_id
                ):
                    from app.services.steamcmd import steamcmd

                    if steamcmd.is_available:
                        logger.info(
                            f"Running SteamCMD update before starting {server.name}..."
                        )
                        (
                            steam_kwargs,
                            steam_error,
                        ) = await steamcmd.get_server_install_kwargs(
                            session, server, interactive=False
                        )
                        if steam_error:
                            return {"ok": False, "error": steam_error}
                        result = await steamcmd.update_server(
                            app_id=server.steam_app_id,
                            install_dir=server.path,
                            **steam_kwargs,
                        )
                        if not result["ok"]:
                            return {"ok": False, "error": result["message"]}
                        if result["ok"] and result.get("build_id"):
                            server.steam_build_id = result["build_id"]
                            server.steam_last_update = datetime.now(timezone.utc)
                            await session.commit()

                server.status = ServerStatus.STARTING
                await session.commit()

                try:
                    if _docker_enabled():
                        from app.services.docker_manager import docker_manager

                        container_id = await docker_manager.create_and_start(server)
                        server.container_id = container_id
                        await session.commit()
                        self._docker_processes[server_id] = container_id
                        self._readiness_generations[server_id] = (
                            self._readiness_generations.get(server_id, 0) + 1
                        )
                        asyncio.create_task(
                            self._watch_container(server_id, container_id)
                        )
                        asyncio.create_task(
                            self._watch_readiness(
                                server_id,
                                server.ready_log_pattern,
                                server.crash_stability_window,
                                self._readiness_generations[server_id],
                            )
                        )
                        asyncio.create_task(
                            notification_service.notify(
                                "start",
                                f"Server Started: {server.name}",
                                f"Port: {server.port} (Docker)",
                                color=0x22C55E,
                                server_id=server_id,
                            )
                        )
                        return {"ok": True, "error": None}

                    cmd = self._build_command(server)
                    logger.info(f"Starting server {server.name}: {cmd}")

                    env = os.environ.copy()
                    if server.environment_vars:
                        import json

                        try:
                            custom_env = json.loads(server.environment_vars)
                            if isinstance(custom_env, dict):
                                env.update(
                                    {str(k): str(v) for k, v in custom_env.items()}
                                )
                        except (json.JSONDecodeError, TypeError):
                            pass

                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        cwd=server.path,
                        env=env,
                    )

                    sp = ServerProcess(server_id, process)
                    sp.server_name = server.name
                    self._processes[server_id] = sp
                    await sp.start_reading()

                    self._write_pid_file(server.path, process.pid)

                    server.started_at = datetime.now(timezone.utc)
                    await session.commit()

                    self._readiness_generations[server_id] = (
                        self._readiness_generations.get(server_id, 0) + 1
                    )
                    asyncio.create_task(self._watch_process(server_id, process))
                    asyncio.create_task(
                        self._watch_readiness(
                            server_id,
                            server.ready_log_pattern,
                            server.crash_stability_window,
                            self._readiness_generations[server_id],
                        )
                    )
                    asyncio.create_task(
                        notification_service.notify(
                            "start",
                            f"Server Started: {server.name}",
                            f"Port: {server.port}",
                            color=0x22C55E,
                            server_id=server_id,
                        )
                    )
                    return {"ok": True, "error": None}

                except Exception as e:
                    logger.error(f"Failed to start server {server_id}: {e}")
                    server.status = ServerStatus.CRASHED
                    await session.commit()
                    return {"ok": False, "error": str(e)}

    async def stop_server(self, server_id: int) -> bool:
        async with async_session() as session:
            server = await session.get(Server, server_id)
            if server:
                from app.config import settings as _settings

                if server.node_id and _settings.multi_node_enabled:
                    from app.models.node import Node
                    from app.services.node_manager import node_manager

                    node = await session.get(Node, server.node_id)
                    if node and not node.is_local:
                        result = await node_manager.proxy_command(
                            node, f"servers/{server_id}/stop"
                        )
                        return result.get("ok", False)

        if _docker_enabled():
            return await self._stop_docker_server(server_id)

        self._reset_crash_state(server_id)

        sp = self._processes.get(server_id)
        if not sp:
            return False

        async with async_session() as session:
            server = await session.get(Server, server_id)
            if server:
                server.status = ServerStatus.STOPPING
                await session.commit()

        try:
            server_type_val = ""
            server_path = ""
            async with async_session() as session:
                server = await session.get(Server, server_id)
                if server:
                    server_type_val = server.server_type.value
                    server_path = server.path

            if server_type_val.startswith("minecraft"):
                await sp.send_command("stop")
                try:
                    await asyncio.wait_for(sp.process.wait(), timeout=30)
                except asyncio.TimeoutError:
                    sp.process.terminate()
                    try:
                        await asyncio.wait_for(sp.process.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        sp.process.kill()
                        await sp.process.wait()
            else:
                sp.process.terminate()
                try:
                    await asyncio.wait_for(sp.process.wait(), timeout=15)
                except asyncio.TimeoutError:
                    sp.process.kill()
                    await sp.process.wait()

        except Exception as e:
            logger.error(f"Error stopping server {server_id}: {e}")
            try:
                sp.process.kill()
                await sp.process.wait()
            except Exception:
                pass

        self._processes.pop(server_id, None)

        if server_path:
            self._remove_pid_file(server_path)

        async with async_session() as session:
            server = await session.get(Server, server_id)
            if server:
                server.status = ServerStatus.STOPPED
                server.started_at = None
                await session.commit()
                asyncio.create_task(
                    notification_service.notify(
                        "stop",
                        f"Server Stopped: {server.name}",
                        "",
                        color=0xEF4444,
                        server_id=server_id,
                    )
                )

        return True

    async def restart_server(self, server_id: int) -> bool:
        await self.stop_server(server_id)
        await asyncio.sleep(2)
        return await self.start_server(server_id)

    async def send_command(self, server_id: int, command: str) -> bool:
        async with async_session() as session:
            server = await session.get(Server, server_id)
            if server and server.node_id:
                from app.config import settings as _settings

                if _settings.multi_node_enabled:
                    from app.models.node import Node
                    from app.services.node_manager import node_manager

                    node = await session.get(Node, server.node_id)
                    if node and not node.is_local:
                        result = await node_manager.proxy_command(
                            node,
                            f"servers/{server_id}/command",
                            data={"command": command},
                        )
                        return result.get("ok", False)
            if _docker_enabled() and server and server.container_id:
                from app.services.docker_manager import docker_manager

                await docker_manager.send_command(server.container_id, command)
                return True
        sp = self._processes.get(server_id)
        if not sp:
            return False
        await sp.send_command(command)
        return True

    def get_logs(self, server_id: int) -> list[str]:
        sp = self._processes.get(server_id)
        if not sp:
            return []
        return list(sp.log_lines)

    def is_running(self, server_id: int) -> bool:
        return server_id in self._processes or server_id in self._docker_processes

    async def _stop_docker_server(self, server_id: int) -> bool:
        """Stop a server running in Docker."""
        self._reset_crash_state(server_id)
        from app.services.docker_manager import docker_manager

        async with async_session() as session:
            server = await session.get(Server, server_id)
            if not server or not server.container_id:
                return False
            container_id = server.container_id
            server.status = ServerStatus.STOPPING
            await session.commit()

        try:
            await docker_manager.stop(container_id)
            await docker_manager.remove(container_id)
        except Exception as e:
            logger.error(f"Error stopping Docker container for server {server_id}: {e}")

        self._docker_processes.pop(server_id, None)

        async with async_session() as session:
            server = await session.get(Server, server_id)
            if server:
                server.status = ServerStatus.STOPPED
                server.container_id = None
                await session.commit()
                asyncio.create_task(
                    notification_service.notify(
                        "stop",
                        f"Server Stopped: {server.name}",
                        "",
                        color=0xEF4444,
                        server_id=server_id,
                    )
                )
        return True

    async def _watch_container(self, server_id: int, container_id: str):
        """Watch a Docker container and update status when it exits."""
        from app.services.docker_manager import docker_manager
        import asyncio as _asyncio

        while True:
            await _asyncio.sleep(5)
            running = await docker_manager.is_running(container_id)
            if not running:
                self._docker_processes.pop(server_id, None)
                async with async_session() as session:
                    server = await session.get(Server, server_id)
                    if server and server.status != ServerStatus.STOPPED:
                        server.status = ServerStatus.CRASHED
                        server.container_id = None
                        await session.commit()

                        if server.auto_restart_on_crash:
                            await self._handle_crash_restart(
                                server_id,
                                server.name,
                                server.max_crash_restarts,
                                server.crash_restart_delay,
                                "Docker container exited",
                            )
                        else:
                            asyncio.create_task(
                                notification_service.notify(
                                    "crash",
                                    f"Server Crashed: {server.name}",
                                    "Docker container exited",
                                    color=0xEF4444,
                                    server_id=server_id,
                                )
                            )
                break

    async def _watch_process(self, server_id: int, process: asyncio.subprocess.Process):
        await process.wait()
        existing = self._processes.get(server_id)
        if existing is not None and existing.process is process:
            self._processes.pop(server_id, None)
        from app.services.resource_monitor import resource_monitor

        resource_monitor.clear_process_cache(process.pid)
        async with async_session() as session:
            server = await session.get(Server, server_id)
            if server:
                self._remove_pid_file(server.path)
                if server.status != ServerStatus.STOPPED:
                    server.status = ServerStatus.CRASHED
                    await session.commit()
                    logger.warning(
                        f"Server {server.name} exited unexpectedly (code {process.returncode})"
                    )

                    if server.auto_restart_on_crash:
                        await self._handle_crash_restart(
                            server_id,
                            server.name,
                            server.max_crash_restarts,
                            server.crash_restart_delay,
                            f"Exit code: {process.returncode}",
                        )
                    else:
                        asyncio.create_task(
                            notification_service.notify(
                                "crash",
                                f"Server Crashed: {server.name}",
                                f"Exit code: {process.returncode}",
                                color=0xEF4444,
                                server_id=server_id,
                            )
                        )

    async def _handle_crash_restart(
        self,
        server_id: int,
        server_name: str,
        max_restarts: int,
        delay: int,
        crash_detail: str,
    ):
        state = self._get_crash_state(server_id)
        state.count += 1
        state.last_crash_time = time.monotonic()

        if state.stability_task and not state.stability_task.done():
            state.stability_task.cancel()
            state.stability_task = None

        if state.count > max_restarts:
            logger.error(
                f"Server {server_name} crashed {state.count} times, "
                f"exceeding max_crash_restarts={max_restarts}. Giving up."
            )
            asyncio.create_task(
                notification_service.notify(
                    "crash",
                    f"Server Crashed: {server_name} (auto-restart exhausted)",
                    f"{crash_detail}. Restarted {max_restarts} time(s) but keeps crashing. "
                    f"Manual intervention required.",
                    color=0xEF4444,
                    server_id=server_id,
                )
            )
            return

        logger.info(
            f"Server {server_name} crashed (attempt {state.count}/{max_restarts}). "
            f"Auto-restarting in {delay}s..."
        )
        asyncio.create_task(
            notification_service.notify(
                "crash",
                f"Server Crashed: {server_name} (auto-restarting {state.count}/{max_restarts})",
                f"{crash_detail}. Restarting in {delay} seconds...",
                color=0xF59E0B,
                server_id=server_id,
            )
        )

        state.restart_task = asyncio.create_task(
            self._delayed_crash_restart(server_id, server_name, delay)
        )

    async def _delayed_crash_restart(
        self, server_id: int, server_name: str, delay: int
    ):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            logger.info(
                f"Crash restart for {server_name} was cancelled (manual stop or new action)."
            )
            return

        async with async_session() as session:
            server = await session.get(Server, server_id)
            if not server:
                return
            if server.status == ServerStatus.STOPPED:
                logger.info(
                    f"Server {server_name} was stopped during restart delay. Aborting auto-restart."
                )
                return
            if server.status == ServerStatus.RUNNING:
                logger.info(
                    f"Server {server_name} is already running. Aborting auto-restart."
                )
                return

        logger.info(f"Executing crash auto-restart for server {server_name}...")
        result = await self.start_server(server_id)
        if not result["ok"]:
            logger.error(
                f"Crash auto-restart failed for {server_name}: {result['error']}"
            )
            asyncio.create_task(
                notification_service.notify(
                    "crash",
                    f"Auto-Restart Failed: {server_name}",
                    f"Could not restart: {result['error']}",
                    color=0xEF4444,
                    server_id=server_id,
                )
            )

    def _start_stability_window(self, server_id: int, window_seconds: int):
        state = self._get_crash_state(server_id)
        if state.stability_task and not state.stability_task.done():
            state.stability_task.cancel()
        state.stability_task = asyncio.create_task(
            self._stability_window_timer(server_id, window_seconds)
        )

    async def _stability_window_timer(self, server_id: int, window_seconds: int):
        try:
            await asyncio.sleep(window_seconds)
        except asyncio.CancelledError:
            return
        state = self._crash_states.get(server_id)
        if state and state.count > 0:
            logger.info(
                f"Server {server_id} has been stable for {window_seconds}s. "
                f"Resetting crash counter (was {state.count})."
            )
            state.count = 0
            state.last_crash_time = 0.0

    async def _watch_readiness(
        self,
        server_id: int,
        pattern: str | None,
        stability_window: int,
        generation: int,
    ):
        """Watch server output for ready pattern, then transition from STARTING to RUNNING."""
        import re

        def _stale() -> bool:
            return self._readiness_generations.get(server_id) != generation

        if not pattern:
            async with async_session() as session:
                server = await session.get(Server, server_id)
                if not server:
                    return
                if server.server_type == ServerType.MINECRAFT_JAVA:
                    pattern = r"Done \(\d+\.\d+s\)! For help"
                elif server.server_type == ServerType.MINECRAFT_BEDROCK:
                    pattern = r"Server started"
                else:
                    if _stale():
                        return
                    server.status = ServerStatus.RUNNING
                    await session.commit()
                    self._start_stability_window(server_id, stability_window)
                    return

        sp = self._processes.get(server_id)
        if not sp:
            # Docker path: no process object, transition immediately
            if _stale():
                return
            async with async_session() as session:
                server = await session.get(Server, server_id)
                if server and server.status == ServerStatus.STARTING:
                    server.status = ServerStatus.RUNNING
                    await session.commit()
            self._start_stability_window(server_id, stability_window)
            return

        timeout = 300
        start = asyncio.get_event_loop().time()
        regex = re.compile(pattern)
        seen_hashes: set[int] = set()

        while asyncio.get_event_loop().time() - start < timeout:
            if _stale():
                return
            lines = sp.log_lines
            for line in lines:
                line_hash = hash(line)
                if line_hash in seen_hashes:
                    continue
                seen_hashes.add(line_hash)
                if regex.search(line):
                    async with async_session() as session:
                        server = await session.get(Server, server_id)
                        if server and server.status == ServerStatus.STARTING:
                            if _stale():
                                return
                            server.status = ServerStatus.RUNNING
                            await session.commit()
                            logger.info(
                                f"Server {server_id} is now RUNNING (readiness pattern matched)"
                            )
                    self._start_stability_window(server_id, stability_window)
                    return
            await asyncio.sleep(1)

        # Timeout: set RUNNING anyway
        if _stale():
            return
        async with async_session() as session:
            server = await session.get(Server, server_id)
            if server and server.status == ServerStatus.STARTING:
                server.status = ServerStatus.RUNNING
                await session.commit()
                logger.warning(
                    f"Server {server_id} readiness timed out after {timeout}s, setting RUNNING anyway"
                )
        self._start_stability_window(server_id, stability_window)

    async def recover_on_startup(self):
        async with async_session() as session:
            result = await session.execute(
                select(Server).where(
                    Server.status.in_(
                        [
                            ServerStatus.RUNNING,
                            ServerStatus.STARTING,
                            ServerStatus.STOPPING,
                        ]
                    )
                )
            )
            stale_servers = result.scalars().all()
            for server in stale_servers:
                if server.node_id:
                    from app.config import settings as _settings

                    if _settings.multi_node_enabled:
                        from app.models.node import Node

                        node = await session.get(Node, server.node_id)
                        if node and not node.is_local:
                            continue
                pid = self._read_pid_file(server.path)
                if pid is not None:
                    if self._is_pid_alive(pid):
                        logger.warning(
                            f"Killing orphaned process {pid} for server {server.name}"
                        )
                        try:
                            if sys.platform == "win32":
                                os.kill(pid, signal.SIGTERM)
                                # On Windows, SIGTERM calls TerminateProcess.
                                # Use subprocess for graceful shutdown if needed.
                                subprocess.call(
                                    ["taskkill", "/PID", str(pid), "/F"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                )
                            else:
                                os.kill(pid, signal.SIGTERM)
                        except OSError:
                            pass
                    self._remove_pid_file(server.path)
                server.status = ServerStatus.STOPPED
                logger.info(f"Reset stale status for server {server.name}")
            await session.commit()

    async def stop_all_servers(self):
        server_ids = list(self._processes.keys())
        if not server_ids:
            return
        tasks = [self.stop_server(sid) for sid in server_ids]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"Stopped {len(server_ids)} server(s)")

    def _build_command(self, server: Server) -> list[str]:
        if server.server_type == ServerType.MINECRAFT_BEDROCK:
            binary = (
                "bedrock_server.exe" if sys.platform == "win32" else "./bedrock_server"
            )
            exe_path = os.path.join(server.path, binary)
            cmd = [exe_path]
            if server.server_args:
                for arg in server.server_args.strip().splitlines():
                    arg = arg.strip()
                    if arg:
                        cmd.append(arg)
            return cmd
        if server.server_type.value.startswith("minecraft"):
            java_path = server.java_path
            # Auto-detect managed Java if using default "java" and MC version is set
            if java_path == "java" and server.mc_version:
                from app.services.java_manager import (
                    get_required_java_version,
                    get_managed_java_path,
                )

                required = get_required_java_version(server.mc_version)
                managed = get_managed_java_path(required)
                if managed:
                    java_path = managed
            cmd = [
                java_path,
                f"-Xms{server.min_memory}M",
                f"-Xmx{server.max_memory}M",
            ]
            if server.jvm_flags:
                for flag in server.jvm_flags.strip().splitlines():
                    flag = flag.strip()
                    if flag:
                        cmd.append(flag)
            cmd.extend(["-jar", server.executable])
            if server.server_args:
                for arg in server.server_args.strip().splitlines():
                    arg = arg.strip()
                    if arg:
                        cmd.append(arg)
            else:
                cmd.append("nogui")
            return cmd
        from app.services.steamcmd import build_runtime_command, _validate_server_name

        _validate_server_name(server.name)
        return build_runtime_command(server)


server_manager = ServerManager()
