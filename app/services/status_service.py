import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
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
            data.append(
                {
                    "name": s.name,
                    "server_type": s.server_type.value,
                    "is_online": is_online,
                    "port": s.port,
                    "player_count": None,
                    "mc_version": s.mc_version,
                    "uptime_seconds": uptime_seconds,
                }
            )
        return data


status_service = StatusService()
