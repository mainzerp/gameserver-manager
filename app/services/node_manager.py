import logging
import secrets
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.node import Node

logger = logging.getLogger(__name__)


class NodeManager:
    async def register_local_node(self, db: AsyncSession) -> None:
        result = await db.execute(select(Node).where(Node.is_local.is_(True)))
        existing = result.scalars().first()
        if existing:
            return
        local = Node(
            name="local",
            hostname="localhost",
            api_url=f"http://localhost:{settings.port}",
            auth_token=secrets.token_urlsafe(32),
            is_local=True,
            status="online",
        )
        db.add(local)
        await db.commit()
        logger.info("Registered local node in database")

    async def check_node_health(self, node: Node, db: AsyncSession) -> bool:
        if node.is_local:
            node.status = "online"
            node.last_heartbeat = datetime.now(timezone.utc)
            await db.commit()
            return True
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{node.api_url}/health",
                    headers={"X-Node-Token": node.auth_token},
                )
            if resp.status_code == 200:
                node.status = "online"
                data = resp.json()
                node.cpu_cores = data.get("cpu_cores")
                node.ram_total_mb = data.get("ram_total_mb")
            else:
                node.status = "offline"
        except Exception as exc:
            logger.warning(f"Node '{node.name}' health check failed: {exc}")
            node.status = "offline"
        node.last_heartbeat = datetime.now(timezone.utc)
        await db.commit()
        return node.status == "online"

    async def check_all_nodes(self) -> None:
        if not settings.multi_node_enabled:
            return
        async with async_session() as db:
            result = await db.execute(select(Node))
            for node in result.scalars().all():
                await self.check_node_health(node, db)

    async def proxy_command(
        self,
        node: Node,
        endpoint: str,
        method: str = "POST",
        data: dict | None = None,
    ) -> dict:
        from urllib.parse import urlparse

        from app.utils.security import is_internal_url, validate_endpoint_path

        parsed = urlparse(node.api_url)
        if parsed.scheme not in ("http", "https"):
            logger.error(
                f"Blocked proxy command to node '{node.name}': "
                f"invalid api_url scheme '{parsed.scheme}'"
            )
            return {"ok": False, "error": f"Node '{node.name}' has an invalid api_url"}

        if is_internal_url(node.api_url):
            logger.error(
                f"Blocked proxy command to node '{node.name}': "
                f"api_url points to an internal address"
            )
            return {"ok": False, "error": f"Node '{node.name}' has an invalid api_url"}

        ok, error = validate_endpoint_path(endpoint)
        if not ok:
            logger.error(
                f"Blocked proxy command to node '{node.name}': "
                f"invalid endpoint '{endpoint}'"
            )
            return {"ok": False, "error": error}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{node.api_url}/api/v1/{endpoint}"
                if method.upper() == "GET":
                    resp = await client.get(
                        url, headers={"X-Node-Token": node.auth_token}
                    )
                else:
                    resp = await client.post(
                        url,
                        json=data or {},
                        headers={"X-Node-Token": node.auth_token},
                    )
            return resp.json()
        except Exception as exc:
            logger.error(
                f"Node proxy command failed ({node.name} -> {endpoint}): {exc}"
            )
            return {"ok": False, "error": f"Node '{node.name}' is unreachable: {exc}"}

    async def get_node_stats(self, node: Node) -> dict:
        if node.is_local:
            from app.services.resource_monitor import resource_monitor

            return resource_monitor.get_system_stats()
        result = await self.proxy_command(node, "system/stats", method="GET")
        return result.get("data", {})


node_manager = NodeManager()
