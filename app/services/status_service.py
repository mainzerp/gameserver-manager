import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server, ServerType
from app.services.query_protocol import steam_query
from app.services.server_manager import server_manager

logger = logging.getLogger(__name__)


class StatusService:
    async def get_public_status(self, db: AsyncSession) -> list[dict]:
        result = await db.execute(select(Server))
        servers = result.scalars().all()
        data = []
        for s in servers:
            is_online = server_manager.is_running(s.id)
            uptime_seconds = None
            if is_online and s.started_at:
                uptime_seconds = int(
                    (datetime.now(timezone.utc) - s.started_at).total_seconds()
                )
            player_count = None
            if is_online and s.server_type == ServerType.STEAM and s.query_port:
                try:
                    info = await steam_query.query_info("127.0.0.1", s.query_port)
                    if info:
                        player_count = info.get("players")
                except Exception:
                    pass
            data.append(
                {
                    "name": s.name,
                    "server_type": s.server_type.value,
                    "is_online": is_online,
                    "port": s.port,
                    "player_count": player_count,
                    "mc_version": s.mc_version,
                    "uptime_seconds": uptime_seconds,
                }
            )
        return data


status_service = StatusService()
