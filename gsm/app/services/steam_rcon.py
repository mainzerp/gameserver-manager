"""RCON command support for Steam/Source-based game servers."""

import logging

from app.models.server import Server, ServerType
from app.services.rcon_client import RCONClient

logger = logging.getLogger(__name__)


class SteamRCONService:
    """Send RCON commands to running Steam/Source game servers.

    Supports any Source-engine game that exposes the standard Source RCON
    protocol on a configured RCON port (CS2, CS:GO, TF2, GMod, L4D2, Rust).
    """

    async def send_command(
        self, server: Server, command: str, host: str = "127.0.0.1", timeout: float = 5.0
    ) -> dict:
        if server.server_type != ServerType.STEAM:
            return {"ok": False, "error": "RCON is only supported for Steam servers."}
        if not server.rcon_enabled:
            return {"ok": False, "error": "RCON is not enabled for this server."}
        if not server.rcon_port:
            return {"ok": False, "error": "RCON port is not configured."}
        if not server.rcon_password:
            return {"ok": False, "error": "RCON password is not configured."}

        client = RCONClient()
        connected = await client.connect(
            host, server.rcon_port, server.rcon_password, timeout=timeout
        )
        if not connected:
            await client.close()
            return {"ok": False, "error": "RCON connection or authentication failed."}

        try:
            response = await client.send_command(command, timeout=timeout)
            return {"ok": True, "response": response}
        except Exception as exc:
            logger.debug("Steam RCON command failed for server %s: %s", server.id, exc)
            return {"ok": False, "error": str(exc)}
        finally:
            await client.close()


steam_rcon_service = SteamRCONService()
