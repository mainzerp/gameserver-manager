"""
Minecraft whitelist and ban list management.

Reads/writes whitelist.json and banned-players.json in the server directory,
and uses RCON for live server commands when available.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class PlayerManager:
    def _read_json_list(self, server_path: str, filename: str) -> list[dict]:
        file_path = Path(server_path) / filename
        if not file_path.exists():
            return []
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read {file_path}: {e}")
            return []

    def _write_json_list(self, server_path: str, filename: str, data: list[dict]):
        file_path = Path(server_path) / filename
        file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_whitelist(self, server_path: str) -> list[dict]:
        return self._read_json_list(server_path, "whitelist.json")

    def get_banned_players(self, server_path: str) -> list[dict]:
        return self._read_json_list(server_path, "banned-players.json")

    def add_to_whitelist(self, server_path: str, name: str, uuid: str | None = None):
        wl = self.get_whitelist(server_path)
        if any(p.get("name", "").lower() == name.lower() for p in wl):
            return
        entry = {"uuid": uuid or "", "name": name}
        wl.append(entry)
        self._write_json_list(server_path, "whitelist.json", wl)

    def remove_from_whitelist(self, server_path: str, name: str):
        wl = [
            p
            for p in self.get_whitelist(server_path)
            if p.get("name", "").lower() != name.lower()
        ]
        self._write_json_list(server_path, "whitelist.json", wl)

    def ban_player(
        self, server_path: str, name: str, reason: str = "Banned by operator"
    ):
        bl = self.get_banned_players(server_path)
        if any(p.get("name", "").lower() == name.lower() for p in bl):
            return
        entry = {
            "uuid": "",
            "name": name,
            "created": datetime.now(timezone.utc).isoformat(),
            "source": "GameServer Manager",
            "reason": reason,
        }
        bl.append(entry)
        self._write_json_list(server_path, "banned-players.json", bl)

    def pardon_player(self, server_path: str, name: str):
        bl = [
            p
            for p in self.get_banned_players(server_path)
            if p.get("name", "").lower() != name.lower()
        ]
        self._write_json_list(server_path, "banned-players.json", bl)

    async def rcon_whitelist_add(self, rcon_client, server, name: str) -> str:
        return await self._rcon_command(rcon_client, server, f"whitelist add {name}")

    async def rcon_whitelist_remove(self, rcon_client, server, name: str) -> str:
        return await self._rcon_command(rcon_client, server, f"whitelist remove {name}")

    async def rcon_ban(self, rcon_client, server, name: str, reason: str = "") -> str:
        cmd = f"ban {name}" + (f" {reason}" if reason else "")
        return await self._rcon_command(rcon_client, server, cmd)

    async def rcon_pardon(self, rcon_client, server, name: str) -> str:
        return await self._rcon_command(rcon_client, server, f"pardon {name}")

    async def _rcon_command(self, rcon_client, server, command: str) -> str:
        from app.services.rcon_client import RCONClient

        client = RCONClient()
        try:
            authed = await client.connect(
                "127.0.0.1", server.rcon_port, server.rcon_password
            )
            if not authed:
                return "RCON authentication failed"
            return await client.send_command(command)
        except Exception as e:
            logger.warning(f"RCON command failed: {e}")
            return f"RCON error: {e}"
        finally:
            await client.close()


player_manager = PlayerManager()
