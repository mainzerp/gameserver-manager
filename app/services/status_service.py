import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server, ServerType
from app.services.query_protocol import minecraft_query, steam_query
from app.services.server_manager import server_manager

logger = logging.getLogger(__name__)


class StatusService:
    async def get_server_telemetry(
        self, server: Server
    ) -> dict:
        """Return live telemetry for a single server.

        Includes running state, uptime, and player count where supported.
        """
        is_online = server_manager.is_running(server.id)
        uptime_seconds = None
        if is_online and server.started_at:
            uptime_seconds = int(
                (datetime.now(timezone.utc) - server.started_at).total_seconds()
            )

        telemetry = {
            "is_online": is_online,
            "uptime_seconds": uptime_seconds,
            "player_count": None,
            "max_players": None,
            "map_name": None,
            "error": None,
        }

        if not is_online:
            return telemetry

        try:
            if server.server_type == ServerType.STEAM and server.query_port:
                info = await steam_query.query_info("127.0.0.1", server.query_port)
                if info:
                    telemetry["player_count"] = info.get("players")
                    telemetry["max_players"] = info.get("max_players")
                    telemetry["map_name"] = info.get("map")
                else:
                    telemetry["error"] = "Steam query returned no data"
            elif server.server_type == ServerType.MINECRAFT_JAVA:
                info = await minecraft_query.query("127.0.0.1", server.port)
                if info:
                    telemetry["player_count"] = info.get("online")
                    telemetry["max_players"] = info.get("max")
                else:
                    telemetry["error"] = "Minecraft query returned no data"
            elif server.server_type == ServerType.MINECRAFT_BEDROCK:
                telemetry["error"] = "Bedrock player query not yet supported"
        except Exception as e:
            logger.debug(f"Telemetry query failed for server {server.id}: {e}")
            telemetry["error"] = str(e)

        return telemetry

    async def get_public_status(self, db: AsyncSession) -> list[dict]:
        result = await db.execute(select(Server))
        servers = result.scalars().all()
        data = []
        for s in servers:
            telemetry = await self.get_server_telemetry(s)
            data.append(
                {
                    "name": s.name,
                    "server_type": s.server_type.value,
                    "is_online": telemetry["is_online"],
                    "port": s.port,
                    "player_count": telemetry["player_count"],
                    "max_players": telemetry["max_players"],
                    "mc_version": s.mc_version,
                    "uptime_seconds": telemetry["uptime_seconds"],
                }
            )
        return data


status_service = StatusService()
