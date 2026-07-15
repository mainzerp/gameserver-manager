import os
import unittest

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.server import Server, ServerStatus, ServerType
from app.services.port_manager import port_manager


class PortManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session = self.session_maker()

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()

    async def _add_server(self, **overrides):
        server = Server(
            name=overrides.get("name", "Test Server"),
            server_type=overrides.get("server_type", ServerType.MINECRAFT_JAVA),
            status=ServerStatus.STOPPED,
            path="/tmp/test",
            executable="server.jar",
            start_command="java -jar server.jar",
            port=overrides.get("port", 25565),
            query_port=overrides.get("query_port"),
            rcon_port=overrides.get("rcon_port"),
            rcon_enabled=overrides.get("rcon_port") is not None,
            rcon_password="secret" if overrides.get("rcon_port") else None,
        )
        self.session.add(server)
        await self.session.commit()
        await self.session.refresh(server)
        return server

    async def test_get_used_ports_includes_query_port(self):
        await self._add_server(server_type=ServerType.STEAM, port=27015, query_port=27016, rcon_port=27017)
        used = await port_manager.get_used_ports(self.session)
        self.assertEqual(used, {27015, 27016, 27017})

    async def test_suggest_ports_avoids_query_port_conflicts(self):
        await self._add_server(server_type=ServerType.STEAM, port=27015, query_port=27016, rcon_port=27017)
        suggested = await port_manager.suggest_ports(self.session, "steam")
        self.assertNotIn(suggested["game_port"], {27015, 27016, 27017})
        self.assertNotIn(suggested["query_port"], {27015, 27016, 27017})
        self.assertNotEqual(suggested["game_port"], suggested["query_port"])

    async def test_check_conflicts_detects_query_port_collision(self):
        await self._add_server(server_type=ServerType.STEAM, port=27015, query_port=27016, rcon_port=27017)
        conflicts = await port_manager.check_conflicts(self.session, 27018, 27019, 27016)
        self.assertIn("Query port 27016 is already in use by another managed server.", conflicts)

    async def test_check_conflicts_rejects_query_port_same_as_game_port(self):
        conflicts = await port_manager.check_conflicts(self.session, 27015, None, 27015)
        self.assertIn("Game port and query port cannot be the same.", conflicts)

    async def test_check_conflicts_rejects_query_port_same_as_rcon_port(self):
        conflicts = await port_manager.check_conflicts(self.session, 27015, 27016, 27016)
        self.assertIn("RCON port and query port cannot be the same.", conflicts)
