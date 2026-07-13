"""SteamCMD integration for Steam-based game servers."""

import asyncio
import logging
import os
import re
import secrets
import shlex
import sys
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models.server import ServerType

logger = logging.getLogger(__name__)


STEAM_APPS = {
    "cs2": {
        "app_id": "730",
        "name": "Counter-Strike 2",
        "executable": "game/bin/linuxsteamrt64/cs2",
        "default_port": 27015,
        "start_args": "-dedicated -port {port} +map de_dust2",
        "login_required": False,
        "workshop_supported": True,
        "config_files": ["game/csgo/cfg/server.cfg"],
    },
    "tf2": {
        "app_id": "232250",
        "name": "Team Fortress 2",
        "executable": "srcds_run",
        "default_port": 27015,
        "start_args": "-game tf -port {port} +maxplayers 24 +map ctf_2fort",
        "login_required": False,
        "workshop_supported": True,
        "config_files": ["tf/cfg/server.cfg"],
    },
    "rust": {
        "app_id": "258550",
        "name": "Rust",
        "executable": "RustDedicated",
        "default_port": 28015,
        "start_args": (
            '-batchmode +server.port {port} '
            '+server.hostname "{name}" '
            '+server.maxplayers 50 +server.worldsize 3000'
        ),
        "login_required": False,
        "workshop_supported": True,
        "config_files": ["server/rustserver/cfg/serverauto.cfg"],
    },
    "ark": {
        "app_id": "376030",
        "name": "ARK: Survival Evolved",
        "executable": "ShooterGame/Binaries/Linux/ShooterGameServer",
        "default_port": 7777,
        "start_args": (
            "TheIsland?listen?SessionName={name}"
            "?ServerPassword=?Port={port}"
            "?QueryPort={query_port} -server -log"
        ),
        "login_required": False,
        "workshop_supported": True,
        "config_files": ["ShooterGame/Saved/Config/LinuxServer/GameUserSettings.ini"],
    },
    "valheim": {
        "app_id": "896660",
        "name": "Valheim",
        "executable": "valheim_server.x86_64",
        "default_port": 2456,
        "start_args": '-name "{name}" -port {port} -world Dedicated -password secret -public 1',
        "login_required": False,
        "workshop_supported": False,
        "config_files": [],
    },
    "palworld": {
        "app_id": "2394010",
        "name": "Palworld",
        "executable": "PalServer.sh",
        "default_port": 8211,
        "start_args": "-port={port} -players=32 -queryport={query_port} EpicApp=PalServer -log",
        "login_required": False,
        "workshop_supported": False,
        "config_files": ["Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"],
    },
    "satisfactory": {
        "app_id": "1690800",
        "name": "Satisfactory",
        "executable": "FactoryServer.sh",
        "default_port": 7777,
        "start_args": "-Port={port} -log",
        "login_required": False,
        "workshop_supported": False,
        "config_files": [],
    },
    "enshrouded": {
        "app_id": "2278520",
        "name": "Enshrouded",
        "executable": "enshrouded_server.exe",
        "default_port": 15636,
        "start_args": "",
        "login_required": False,
        "workshop_supported": False,
        "config_files": ["enshrouded_server.json"],
    },
    "7dtd": {
        "app_id": "294420",
        "name": "7 Days to Die",
        "executable": "7DaysToDieServer.x86_64",
        "default_port": 26900,
        "start_args": "-configfile=serverconfig.xml -quit -batchmode -nographics -dedicated",
        "login_required": False,
        "workshop_supported": True,
        "config_files": ["serverconfig.xml"],
    },
    "gmod": {
        "app_id": "4020",
        "name": "Garry's Mod",
        "executable": "srcds_run",
        "default_port": 27015,
        "start_args": "-game garrysmod -port {port} +maxplayers 16 +map gm_construct",
        "login_required": False,
        "workshop_supported": True,
        "config_files": ["garrysmod/cfg/server.cfg"],
    },
    "left4dead2": {
        "app_id": "222860",
        "name": "Left 4 Dead 2",
        "executable": "srcds_run",
        "default_port": 27015,
        "start_args": "-game left4dead2 -port {port} +maxplayers 8 +map c1m1_hotel",
        "login_required": False,
        "workshop_supported": True,
        "config_files": ["left4dead2/cfg/server.cfg"],
    },
    "csgo": {
        "app_id": "740",
        "name": "Counter-Strike: Global Offensive",
        "executable": "srcds_run",
        "default_port": 27015,
        "start_args": "-game csgo -port {port} +game_type 0 +game_mode 0 +map de_dust2",
        "login_required": False,
        "workshop_supported": True,
        "config_files": ["csgo/cfg/server.cfg"],
    },
}

_PROGRESS_RE = re.compile(r"Update state \(0x\w+\) ([^,]+), progress: ([\d.]+)")
_STEAM_GUARD_REQUIRED_PATTERNS = (
    "steam guard code",
    "steam guard",
    "two-factor code",
    "auth code",
    "one-time password",
    "one time password",
    "email code",
    "enter the current code",
    "account protected by steam guard",
)
_STEAM_GUARD_INVALID_PATTERNS = (
    "invalid login auth code",
    "invalid steam guard code",
    "two-factor code mismatch",
    "two factor code mismatch",
    "incorrect steam guard code",
    "invalid auth code",
    "auth code invalid",
)


_SAFE_NAME_RE = re.compile(r"^[\w\s\-_.]+$")


def _validate_server_name(name: str) -> None:
    """Reject server names with shell-metacharacters to prevent command injection."""
    if not name or not _SAFE_NAME_RE.match(name):
        raise ValueError(
            "Server name contains invalid characters. "
            "Allowed: alphanumeric, space, hyphen, underscore, dot."
        )


def generate_start_command(
    app_id: str, port: int, server_name: str = "GameServer"
) -> str | None:
    """Generate a start command for a known Steam game."""
    _validate_server_name(server_name)
    app_info = None
    for _key, info in STEAM_APPS.items():
        if info["app_id"] == app_id:
            app_info = info
            break
    if not app_info:
        return None
    executable = app_info.get("executable", "")
    start_args = app_info.get("start_args", "").format(
        port=port,
        name=server_name,
        query_port=port + 1,
    )
    return f"./{executable} {start_args}".strip()


def build_runtime_command(server) -> list[str]:
    """Build the final launch argv from the persisted base command."""
    _validate_server_name(server.name)
    try:
        cmd = shlex.split(server.start_command, posix=(sys.platform != "win32"))
    except ValueError as exc:
        raise ValueError(
            f"Invalid start_command for server '{server.name}': {exc}"
        ) from exc

    for token in cmd:
        if "\x00" in token:
            raise ValueError(
                f"Invalid null byte in start_command for server '{server.name}'"
            )

    if (
        server.server_type == ServerType.STEAM
        and server.steam_app_id == "4020"
        and "+sv_setsteamaccount" not in cmd
    ):
        steam_gslt = getattr(server, "steam_gslt", None)
        if steam_gslt:
            cmd.extend(["+sv_setsteamaccount", steam_gslt])

    return cmd


class SteamCMD:
    def __init__(self):
        self._steamcmd_path = settings.steamcmd_path or self._find_steamcmd()
        self._operation_lock = asyncio.Lock()
        self._server_locks: dict[int, asyncio.Lock] = {}
        self._progress_subscribers: dict[int, list[asyncio.Queue]] = {}
        self._operation_state: dict[int, dict] = {}
        self._guard_waiters: dict[str, asyncio.Future] = {}

    def _find_steamcmd(self) -> str:
        candidates = []
        if sys.platform == "win32":
            candidates.append(
                os.path.join(settings.steamcmd_install_dir, "steamcmd.exe")
            )
            candidates.extend(
                [
                    r"C:\steamcmd\steamcmd.exe",
                    os.path.expanduser(r"~\steamcmd\steamcmd.exe"),
                ]
            )
        else:
            candidates.append(
                os.path.join(settings.steamcmd_install_dir, "steamcmd.sh")
            )
            candidates.extend(
                [
                    "/usr/games/steamcmd",
                    "/usr/local/bin/steamcmd",
                    os.path.expanduser("~/steamcmd/steamcmd.sh"),
                ]
            )

        for path in candidates:
            if os.path.isfile(path):
                return path
        return ""

    def _get_lock(self, server_id: int | None) -> asyncio.Lock:
        if server_id is None:
            return self._operation_lock
        if server_id not in self._server_locks:
            self._server_locks[server_id] = asyncio.Lock()
        return self._server_locks[server_id]

    def _default_snapshot(self, server_id: int) -> dict:
        return {
            "type": "snapshot",
            "server_id": server_id,
            "operation_id": None,
            "operation": None,
            "status": "idle",
            "message": "Idle",
            "percent": 0.0,
            "workshop_item_id": None,
            "build_id": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_operation_snapshot(self, server_id: int) -> dict:
        snapshot = deepcopy(
            self._operation_state.get(server_id) or self._default_snapshot(server_id)
        )
        snapshot["type"] = "snapshot"
        return snapshot

    async def _publish_event(
        self,
        server_id: int | None,
        event_type: str,
        operation_id: str,
        operation_type: str,
        message: str,
        percent: float | None = None,
        workshop_item_id: str | None = None,
        build_id: str | None = None,
        status: str | None = None,
    ) -> None:
        if server_id is None:
            return

        existing = self._operation_state.get(
            server_id, self._default_snapshot(server_id)
        )
        payload = {
            **existing,
            "type": event_type,
            "server_id": server_id,
            "operation_id": operation_id,
            "operation": operation_type,
            "status": status or event_type,
            "message": message,
            "percent": existing.get("percent", 0.0) if percent is None else percent,
            "workshop_item_id": existing.get("workshop_item_id")
            if workshop_item_id is None
            else workshop_item_id,
            "build_id": existing.get("build_id") if build_id is None else build_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._operation_state[server_id] = deepcopy(payload)

        for queue in list(self._progress_subscribers.get(server_id, [])):
            try:
                queue.put_nowait(deepcopy(payload))
            except asyncio.QueueFull:
                logger.debug(
                    "Dropping SteamCMD event for slow subscriber on server %s",
                    server_id,
                )
            except Exception:
                logger.debug(
                    "Failed to enqueue SteamCMD event for server %s",
                    server_id,
                    exc_info=True,
                )

    async def queue_operation(
        self,
        server_id: int,
        operation_type: str,
        message: str,
        workshop_item_id: str | None = None,
    ) -> str:
        operation_id = uuid.uuid4().hex
        await self._publish_event(
            server_id=server_id,
            event_type="started",
            operation_id=operation_id,
            operation_type=operation_type,
            message=message,
            percent=0.0,
            workshop_item_id=workshop_item_id,
            status="queued",
        )
        return operation_id

    async def record_operation_failure(
        self,
        server_id: int,
        operation_type: str,
        message: str,
        workshop_item_id: str | None = None,
    ) -> dict:
        operation_id = uuid.uuid4().hex
        await self._publish_event(
            server_id=server_id,
            event_type="failed",
            operation_id=operation_id,
            operation_type=operation_type,
            message=message,
            percent=0.0,
            workshop_item_id=workshop_item_id,
            status="failed",
        )
        return {"ok": False, "message": message, "operation_id": operation_id}

    def _write_login_script(
        self, username: str, password: str, steam_guard_code: str | None
    ) -> str:
        """Write a temporary SteamCMD runscript containing the login command.

        Keeps the password out of the process argv (visible via ``ps`` /
        ``/proc/<pid>/cmdline``).  The caller must delete the returned path.
        """
        auth_dir = Path("data") / "auth"
        auth_dir.mkdir(parents=True, exist_ok=True)
        script_path = auth_dir / f"gsm_login_{secrets.token_hex(8)}.txt"

        parts = ["login", username, password]
        if steam_guard_code:
            parts.append(steam_guard_code)
        script_path.write_text(" ".join(parts) + "\n", encoding="utf-8")

        try:
            os.chmod(script_path, 0o600)
        except OSError:
            pass

        return str(script_path)

    def _build_login_args(
        self,
        login_anonymous: bool,
        username: str | None,
        password: str | None,
        steam_guard_code: str | None,
        _created_scripts: list[str] | None = None,
    ) -> list[str]:
        if login_anonymous or not username:
            return ["+login", "anonymous"]
        script_path = self._write_login_script(
            username, password or "", steam_guard_code
        )
        if _created_scripts is not None:
            _created_scripts.append(script_path)
        return ["+runscript", script_path]

    def _is_guard_required_line(self, line: str) -> bool:
        lowered = line.lower()
        if any(pattern in lowered for pattern in _STEAM_GUARD_REQUIRED_PATTERNS):
            return True
        return "account logon denied" in lowered and "steam guard" in lowered

    def _is_guard_invalid_line(self, line: str) -> bool:
        lowered = line.lower()
        return any(pattern in lowered for pattern in _STEAM_GUARD_INVALID_PATTERNS)

    async def _terminate_process(self, process) -> None:
        try:
            if process.returncode is None:
                process.terminate()
        except ProcessLookupError:
            return
        except Exception:
            logger.debug("Failed to terminate SteamCMD process cleanly", exc_info=True)
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                logger.debug("Failed to kill SteamCMD process", exc_info=True)
            await process.wait()

    async def _run_process(
        self,
        cmd: list[str],
        install_dir: str,
        app_id: str,
        operation_id: str,
        operation_type: str,
        progress_callback,
        server_id: int | None,
        workshop_item_id: str | None,
    ) -> dict:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:

            async def _communicate() -> dict:
                if process.stdout is None:
                    return {
                        "ok": False,
                        "status": "failed",
                        "message": "SteamCMD process stdout is not available.",
                        "percent": 0.0,
                    }

                output_lines: list[str] = []
                last_percent = 0.0
                guard_required_message = "Steam Guard input is required to continue."
                guard_invalid_message = (
                    "The Steam Guard code was rejected. Enter a new code and try again."
                )

                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").rstrip()
                    if not decoded:
                        continue
                    output_lines.append(decoded)

                    if self._is_guard_invalid_line(decoded):
                        guard_invalid_message = decoded
                        await self._terminate_process(process)
                        return {
                            "ok": False,
                            "status": "steam_guard_invalid",
                            "message": guard_invalid_message,
                            "percent": last_percent,
                        }

                    if self._is_guard_required_line(decoded):
                        guard_required_message = decoded
                        await self._terminate_process(process)
                        return {
                            "ok": False,
                            "status": "steam_guard_required",
                            "message": guard_required_message,
                            "percent": last_percent,
                        }

                    match = _PROGRESS_RE.search(decoded)
                    if match:
                        status_text = match.group(1).strip()
                        last_percent = float(match.group(2))
                        if progress_callback:
                            try:
                                await progress_callback(last_percent, status_text)
                            except Exception:
                                logger.debug(
                                    "SteamCMD progress callback failed", exc_info=True
                                )
                        await self._publish_event(
                            server_id=server_id,
                            event_type="progress",
                            operation_id=operation_id,
                            operation_type=operation_type,
                            message=status_text,
                            percent=last_percent,
                            workshop_item_id=workshop_item_id,
                            status="running",
                        )

                await process.wait()
                if process.returncode == 0:
                    return {
                        "ok": True,
                        "status": "completed",
                        "message": "SteamCMD operation completed successfully.",
                        "percent": max(last_percent, 100.0),
                        "build_id": self._read_build_id(app_id, install_dir),
                    }

                message = (
                    "\n".join(output_lines[-10:])
                    or f"SteamCMD exited with code {process.returncode}."
                )
                return {
                    "ok": False,
                    "status": "failed",
                    "message": message[-400:],
                    "percent": last_percent,
                }

            return await asyncio.wait_for(_communicate(), timeout=1800)
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "status": "failed",
                "message": "SteamCMD operation timed out after 30 minutes.",
                "percent": 0.0,
            }
        finally:
            await self._terminate_process(process)

    async def _run_guarded_operation(
        self,
        *,
        cmd_builder,
        install_dir: str,
        app_id: str,
        operation_id: str,
        operation_type: str,
        progress_callback,
        server_id: int | None,
        workshop_item_id: str | None,
        interactive: bool,
    ) -> dict:
        steam_guard_code = None
        guard_waiter: asyncio.Future | None = None

        while True:
            result = await self._run_process(
                cmd=cmd_builder(steam_guard_code),
                install_dir=install_dir,
                app_id=app_id,
                operation_id=operation_id,
                operation_type=operation_type,
                progress_callback=progress_callback,
                server_id=server_id,
                workshop_item_id=workshop_item_id,
            )
            if result.get("status") == "steam_guard_required":
                if not interactive:
                    result["message"] = (
                        "Steam Guard input is required, but this operation is running without interactive user input."
                    )
                    return result
                if server_id is None:
                    result["message"] = (
                        "Steam Guard input is required, but no server operation context exists to resume it."
                    )
                    return result
                guard_waiter = asyncio.get_running_loop().create_future()
                self._guard_waiters[operation_id] = guard_waiter
                await self._publish_event(
                    server_id=server_id,
                    event_type="steam_guard_required",
                    operation_id=operation_id,
                    operation_type=operation_type,
                    message=result["message"],
                    percent=result.get("percent", 0.0),
                    workshop_item_id=workshop_item_id,
                    status="waiting_for_steam_guard",
                )
                try:
                    steam_guard_code = (
                        await asyncio.wait_for(guard_waiter, timeout=300)
                    ).strip()
                except asyncio.TimeoutError:
                    self._guard_waiters.pop(operation_id, None)
                    return {
                        "ok": False,
                        "status": "failed",
                        "message": "Steam Guard input timed out after 5 minutes.",
                        "percent": result.get("percent", 0.0),
                    }
                self._guard_waiters.pop(operation_id, None)
                continue
            if result.get("status") == "steam_guard_invalid":
                if not interactive:
                    result["message"] = (
                        "Steam Guard input was rejected during a non-interactive SteamCMD operation."
                    )
                    return result
                if server_id is None:
                    return result
                guard_waiter = asyncio.get_running_loop().create_future()
                self._guard_waiters[operation_id] = guard_waiter
                await self._publish_event(
                    server_id=server_id,
                    event_type="steam_guard_invalid",
                    operation_id=operation_id,
                    operation_type=operation_type,
                    message=result["message"],
                    percent=result.get("percent", 0.0),
                    workshop_item_id=workshop_item_id,
                    status="waiting_for_steam_guard",
                )
                try:
                    steam_guard_code = (
                        await asyncio.wait_for(guard_waiter, timeout=300)
                    ).strip()
                except asyncio.TimeoutError:
                    self._guard_waiters.pop(operation_id, None)
                    return {
                        "ok": False,
                        "status": "failed",
                        "message": "Steam Guard input timed out after 5 minutes.",
                        "percent": result.get("percent", 0.0),
                    }
                self._guard_waiters.pop(operation_id, None)
                continue
            return result

    @property
    def is_available(self) -> bool:
        return bool(self._steamcmd_path) and os.path.isfile(self._steamcmd_path)

    async def ensure_available(self) -> bool:
        if self.is_available:
            return True
        if settings.steamcmd_auto_install:
            from app.services.steamcmd_installer import steamcmd_installer

            success = await steamcmd_installer.install()
            if success:
                self._steamcmd_path = self._find_steamcmd()
            return self.is_available
        return False

    async def install_server(
        self,
        app_id: str,
        install_dir: str,
        validate: bool = True,
        branch: str | None = None,
        login_anonymous: bool = True,
        username: str | None = None,
        password: str | None = None,
        steam_guard_code: str | None = None,
        progress_callback=None,
        server_id: int | None = None,
        operation_type: str = "install",
        operation_id: str | None = None,
        interactive: bool = False,
    ) -> dict:
        if not self.is_available:
            message = "SteamCMD is not installed or not found."
            if server_id is not None:
                return await self.record_operation_failure(
                    server_id, operation_type, message
                )
            return {
                "ok": False,
                "message": message,
                "build_id": None,
                "operation_id": operation_id,
            }

        install_path = Path(install_dir)
        install_path.mkdir(parents=True, exist_ok=True)
        operation_id = operation_id or uuid.uuid4().hex

        login_scripts: list[str] = []

        def build_cmd(guard_code: str | None) -> list[str]:
            login_args = self._build_login_args(
                login_anonymous, username, password, guard_code, login_scripts
            )
            cmd = [
                self._steamcmd_path,
                "+force_install_dir",
                str(install_path),
                *login_args,
                "+app_update",
                app_id,
            ]
            if branch and branch.strip() and branch.strip().lower() != "public":
                cmd.extend(["-beta", branch.strip()])
            if validate:
                cmd.append("validate")
            cmd.append("+quit")
            return cmd

        async with self._get_lock(server_id):
            await self._publish_event(
                server_id=server_id,
                event_type="started",
                operation_id=operation_id,
                operation_type=operation_type,
                message=f"SteamCMD {operation_type} started.",
                percent=0.0,
                status="running",
            )

            try:
                result = await self._run_guarded_operation(
                    cmd_builder=lambda guard_code: build_cmd(
                        guard_code or steam_guard_code
                    ),
                    install_dir=str(install_path),
                    app_id=app_id,
                    operation_id=operation_id,
                    operation_type=operation_type,
                    progress_callback=progress_callback,
                    server_id=server_id,
                    workshop_item_id=None,
                    interactive=interactive,
                )
            finally:
                self._guard_waiters.pop(operation_id, None)
                for _script in login_scripts:
                    try:
                        os.remove(_script)
                    except OSError:
                        pass

        result["operation_id"] = operation_id
        if result.get("ok"):
            await self._publish_event(
                server_id=server_id,
                event_type="completed",
                operation_id=operation_id,
                operation_type=operation_type,
                message=result.get("message")
                or "SteamCMD operation completed successfully.",
                percent=100.0,
                build_id=result.get("build_id"),
                status="completed",
            )
            return result

        terminal_event = result.get("status", "failed")
        if terminal_event not in {"steam_guard_required", "steam_guard_invalid"}:
            await self._publish_event(
                server_id=server_id,
                event_type="failed",
                operation_id=operation_id,
                operation_type=operation_type,
                message=result.get("message") or "SteamCMD operation failed.",
                percent=result.get("percent", 0.0),
                status="failed",
            )
        return result

    async def update_server(self, app_id: str, install_dir: str, **kwargs) -> dict:
        kwargs.setdefault("operation_type", "update")
        return await self.install_server(app_id, install_dir, validate=False, **kwargs)

    async def validate_server(self, app_id: str, install_dir: str, **kwargs) -> dict:
        kwargs.setdefault("operation_type", "validate")
        return await self.install_server(app_id, install_dir, validate=True, **kwargs)

    def _read_build_id(self, app_id: str, install_dir: str) -> str | None:
        manifest = os.path.join(install_dir, "steamapps", f"appmanifest_{app_id}.acf")
        if not os.path.isfile(manifest):
            return None
        try:
            with open(manifest, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped.startswith('"buildid"'):
                        parts = stripped.split('"')
                        if len(parts) >= 4:
                            return parts[3]
        except OSError:
            logger.debug(
                "Failed to read Steam build manifest %s", manifest, exc_info=True
            )
        return None

    async def get_remote_build_id(self, app_id: str) -> str | None:
        return await self.get_remote_build_id_for_branch(app_id, "public")

    async def get_remote_build_id_for_branch(
        self, app_id: str, branch: str | None = None
    ) -> str | None:
        if not self.is_available:
            return None
        cmd = [
            self._steamcmd_path,
            "+login",
            "anonymous",
            "+app_info_update",
            "1",
            "+app_info_print",
            app_id,
            "+quit",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            output = stdout.decode("utf-8", errors="replace")

            target_branch = (branch or "public").strip() or "public"
            in_target_branch = False
            for line in output.splitlines():
                stripped = line.strip()
                if f'"{target_branch}"' in stripped:
                    in_target_branch = True
                elif in_target_branch and '"buildid"' in stripped:
                    parts = stripped.split('"')
                    if len(parts) >= 4:
                        return parts[3]
                elif in_target_branch and stripped == "}":
                    break
        except Exception as exc:
            logger.error("Failed to get remote build ID for %s: %s", app_id, exc)
        return None

    async def get_account_credentials(self, db, account_id: int | None) -> dict:
        if not account_id:
            return {}
        from app.models.steam_account import SteamAccount, decrypt_password

        account = await db.get(SteamAccount, account_id)
        if not account:
            return {}
        return {
            "username": account.username,
            "password": decrypt_password(account.password_encrypted),
            "steam_guard_type": account.steam_guard_type,
        }

    async def get_server_install_kwargs(
        self, db, server, interactive: bool = False
    ) -> tuple[dict, str | None]:
        kwargs = {
            "branch": server.steam_branch or "public",
            "login_anonymous": bool(server.steam_login_anonymous),
            "server_id": getattr(server, "id", None),
            "interactive": interactive,
        }
        if server.steam_login_anonymous:
            return kwargs, None
        if not server.steam_account_id:
            return (
                kwargs,
                "Authenticated Steam install requested, but no Steam account is assigned.",
            )

        creds = await self.get_account_credentials(db, server.steam_account_id)
        if not creds:
            return kwargs, "Assigned Steam account could not be loaded."

        guard_type = (creds.get("steam_guard_type") or "none").strip().lower()
        if guard_type != "none" and not interactive:
            return (
                kwargs,
                f"Steam account '{creds.get('username')}' requires Steam Guard "
                f"input and cannot be used in unattended SteamCMD operations.",
            )

        kwargs.update(
            {
                "login_anonymous": False,
                "username": creds.get("username"),
                "password": creds.get("password"),
            }
        )
        return kwargs, None

    async def install_workshop_item(
        self,
        app_id: str,
        workshop_id: str,
        install_dir: str,
        login_anonymous: bool = True,
        username: str | None = None,
        password: str | None = None,
        steam_guard_code: str | None = None,
        server_id: int | None = None,
        operation_type: str = "workshop_install",
        operation_id: str | None = None,
        interactive: bool = False,
    ) -> dict:
        if not self.is_available:
            message = "SteamCMD is not available."
            if server_id is not None:
                return await self.record_operation_failure(
                    server_id, operation_type, message, workshop_item_id=workshop_id
                )
            return {"ok": False, "message": message, "operation_id": operation_id}

        operation_id = operation_id or uuid.uuid4().hex
        install_path = Path(install_dir)
        install_path.mkdir(parents=True, exist_ok=True)

        login_scripts: list[str] = []

        def build_cmd(guard_code: str | None) -> list[str]:
            login_args = self._build_login_args(
                login_anonymous, username, password, guard_code, login_scripts
            )
            return [
                self._steamcmd_path,
                "+force_install_dir",
                str(install_path),
                *login_args,
                "+workshop_download_item",
                app_id,
                workshop_id,
                "+quit",
            ]

        async with self._get_lock(server_id):
            await self._publish_event(
                server_id=server_id,
                event_type="started",
                operation_id=operation_id,
                operation_type=operation_type,
                message=f"Workshop item {workshop_id} download started.",
                percent=0.0,
                workshop_item_id=workshop_id,
                status="running",
            )
            try:
                result = await self._run_guarded_operation(
                    cmd_builder=lambda guard_code: build_cmd(
                        guard_code or steam_guard_code
                    ),
                    install_dir=str(install_path),
                    app_id=app_id,
                    operation_id=operation_id,
                    operation_type=operation_type,
                    progress_callback=None,
                    server_id=server_id,
                    workshop_item_id=workshop_id,
                    interactive=interactive,
                )
            finally:
                self._guard_waiters.pop(operation_id, None)
                for _script in login_scripts:
                    try:
                        os.remove(_script)
                    except OSError:
                        pass

        result["operation_id"] = operation_id
        if result.get("ok"):
            result["message"] = f"Workshop item {workshop_id} downloaded"
            await self._publish_event(
                server_id=server_id,
                event_type="completed",
                operation_id=operation_id,
                operation_type=operation_type,
                message=result["message"],
                percent=100.0,
                workshop_item_id=workshop_id,
                status="completed",
            )
            return result

        if result.get("status") not in {"steam_guard_required", "steam_guard_invalid"}:
            await self._publish_event(
                server_id=server_id,
                event_type="failed",
                operation_id=operation_id,
                operation_type=operation_type,
                message=result.get("message")
                or f"Workshop item {workshop_id} download failed.",
                percent=result.get("percent", 0.0),
                workshop_item_id=workshop_id,
                status="failed",
            )
        return result

    def subscribe_progress(self, server_id: int, queue: asyncio.Queue):
        self._progress_subscribers.setdefault(server_id, []).append(queue)

    def unsubscribe_progress(self, server_id: int, queue: asyncio.Queue):
        subscribers = self._progress_subscribers.get(server_id, [])
        if queue in subscribers:
            subscribers.remove(queue)
        if not subscribers and server_id in self._progress_subscribers:
            del self._progress_subscribers[server_id]

    async def submit_steam_guard_code(
        self, server_id: int, operation_id: str, code: str
    ) -> dict:
        state = self._operation_state.get(server_id)
        if not state or state.get("operation_id") != operation_id:
            return {
                "ok": False,
                "message": "The Steam operation no longer matches the requested Steam Guard challenge.",
            }
        waiter = self._guard_waiters.get(operation_id)
        if waiter is None or waiter.done():
            return {
                "ok": False,
                "message": "There is no active Steam Guard challenge waiting for input.",
            }
        normalized_code = (code or "").strip()
        if not normalized_code:
            return {"ok": False, "message": "Steam Guard code is required."}
        waiter.set_result(normalized_code)
        await self._publish_event(
            server_id=server_id,
            event_type="progress",
            operation_id=operation_id,
            operation_type=state.get("operation") or "install",
            message="Steam Guard code received. Resuming SteamCMD operation.",
            percent=state.get("percent", 0.0),
            workshop_item_id=state.get("workshop_item_id"),
            build_id=state.get("build_id"),
            status="running",
        )
        return {"ok": True, "message": "Steam Guard code accepted."}

    def get_known_apps(self) -> dict:
        return STEAM_APPS

    def get_app_info(self, app_id: str) -> dict | None:
        for _key, info in STEAM_APPS.items():
            if info["app_id"] == app_id:
                return info
        return None


steamcmd = SteamCMD()
