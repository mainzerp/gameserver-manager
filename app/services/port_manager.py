import asyncio
import logging
import socket

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server

logger = logging.getLogger(__name__)


class PortManager:
    DEFAULT_RANGES = {
        "minecraft_java": {"game": 25565, "rcon": 25575, "query": 0},
        "minecraft_bedrock": {"game": 19132, "rcon": 0, "query": 19133},
        "steam": {"game": 27015, "rcon": 27015, "query": 27016},
    }

    async def get_used_ports(
        self, db: AsyncSession, exclude_server_id: int | None = None
    ) -> set[int]:
        query = select(Server)
        if exclude_server_id is not None:
            query = query.where(Server.id != exclude_server_id)
        result = await db.execute(query)
        servers = result.scalars().all()
        ports = set()
        for s in servers:
            if s.port:
                ports.add(s.port)
            if s.rcon_port:
                ports.add(s.rcon_port)
            if s.query_port:
                ports.add(s.query_port)
        return ports

    async def suggest_ports(self, db: AsyncSession, server_type: str) -> dict[str, int]:
        defaults = self.DEFAULT_RANGES.get(server_type, {"game": 25565, "rcon": 25575, "query": 0})
        used = await self.get_used_ports(db)

        game_port = defaults["game"]
        while game_port in used:
            game_port += 1
            if game_port > 65535:
                break

        rcon_port = defaults["rcon"]
        if rcon_port > 0:
            while rcon_port in used or rcon_port == game_port:
                rcon_port += 1
                if rcon_port > 65535:
                    break

        query_port = defaults["query"]
        if query_port > 0:
            while query_port in used or query_port == game_port or query_port == rcon_port:
                query_port += 1
                if query_port > 65535:
                    break
        elif server_type == "steam":
            # Default Steam query port is game port + 1 if no specific default is defined.
            query_port = game_port + 1
            while query_port in used or query_port == rcon_port:
                query_port += 1
                if query_port > 65535:
                    break

        return {"game_port": game_port, "rcon_port": rcon_port, "query_port": query_port}

    async def check_conflicts(
        self,
        db: AsyncSession,
        game_port: int,
        rcon_port: int | None,
        query_port: int | None = None,
        exclude_server_id: int | None = None,
    ) -> list[str]:
        used = await self.get_used_ports(db, exclude_server_id)
        conflicts = []
        if game_port in used:
            conflicts.append(
                f"Game port {game_port} is already in use by another managed server."
            )
        if rcon_port and rcon_port in used:
            conflicts.append(
                f"RCON port {rcon_port} is already in use by another managed server."
            )
        if rcon_port and rcon_port == game_port:
            conflicts.append("Game port and RCON port cannot be the same.")
        if query_port and query_port in used:
            conflicts.append(
                f"Query port {query_port} is already in use by another managed server."
            )
        if query_port and query_port == game_port:
            conflicts.append("Game port and query port cannot be the same.")
        if query_port and rcon_port and query_port == rcon_port:
            conflicts.append("RCON port and query port cannot be the same.")
        return conflicts

    def check_os_port(self, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("0.0.0.0", port))
                return True
        except OSError:
            return False

    async def check_port_reachable(
        self, port: int, host: str = "127.0.0.1", timeout: float = 3.0
    ) -> dict:
        """Check if a port is reachable by attempting a TCP connection."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return {"reachable": True, "error": None}
        except (ConnectionRefusedError, OSError):
            return {"reachable": False, "error": "Connection refused"}
        except asyncio.TimeoutError:
            return {"reachable": False, "error": "Connection timed out"}
        except Exception as e:
            return {"reachable": False, "error": str(e)}


port_manager = PortManager()
