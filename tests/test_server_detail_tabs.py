import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import app.models  # noqa: F401
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base
from app.models.server import Server, ServerStatus, ServerType
from app.routers import servers


class ServerDetailTabVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path}")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="test-secret")

        @self.app.middleware("http")
        async def add_test_state(request, call_next):
            request.state.csp_nonce = "test-nonce"
            return await call_next(request)

        self.app.include_router(servers.router)

        async def override_get_db():
            async with self.session_maker() as session:
                yield session

        async def override_current_user():
            return SimpleNamespace(id=1, role="admin")

        self.app.dependency_overrides[servers.get_db] = override_get_db
        self.app.dependency_overrides[servers.get_current_user_dep] = (
            override_current_user
        )

        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(
            transport=self.transport,
            base_url="http://testserver",
            follow_redirects=False,
        )

        self.original_servers_dir = servers.settings.servers_dir
        servers.settings.servers_dir = self.temp_dir.name

        self.access_patch = patch.object(
            servers,
            "require_server_access",
            AsyncMock(return_value=SimpleNamespace(id=1, role="admin")),
        )
        self.conflicts_patch = patch.object(
            servers.mod_updater, "check_conflicts", AsyncMock(return_value=[])
        )
        self.update_patch = patch.object(
            servers.server_updater, "check_update", AsyncMock(return_value=None)
        )
        self.logs_patch = patch.object(
            servers.server_manager, "get_logs", return_value=[]
        )
        self.running_patch = patch.object(
            servers.server_manager, "is_running", return_value=False
        )
        self.apps_patch = patch.object(
            servers.steamcmd, "get_known_apps", return_value={}
        )
        self.whitelist_patch = patch.object(
            servers.player_manager, "get_whitelist", return_value=[]
        )
        self.banned_patch = patch.object(
            servers.player_manager, "get_banned_players", return_value=[]
        )

        self.access_patch.start()
        self.conflicts_patch.start()
        self.check_update_mock = self.update_patch.start()
        self.logs_patch.start()
        self.running_patch.start()
        self.apps_patch.start()
        self.whitelist_patch.start()
        self.banned_patch.start()

    async def asyncTearDown(self):
        self.access_patch.stop()
        self.conflicts_patch.stop()
        self.update_patch.stop()
        self.logs_patch.stop()
        self.running_patch.stop()
        self.apps_patch.stop()
        self.whitelist_patch.stop()
        self.banned_patch.stop()
        servers.settings.servers_dir = self.original_servers_dir
        await self.client.aclose()
        await self.engine.dispose()
        self.temp_dir.cleanup()

    async def _create_server(self, server_type: ServerType, **overrides) -> Server:
        server_path = Path(self.temp_dir.name) / overrides.get(
            "path_name", server_type.value
        )
        server_path.mkdir(parents=True, exist_ok=True)
        values = {
            "name": overrides.get("name", server_type.value),
            "server_type": server_type,
            "status": overrides.get("status", ServerStatus.STOPPED),
            "path": str(server_path),
            "executable": overrides.get(
                "executable",
                "server.jar" if server_type != ServerType.STEAM else "server.sh",
            ),
            "start_command": overrides.get(
                "start_command",
                "java -jar server.jar nogui"
                if server_type != ServerType.STEAM
                else "./server.sh",
            ),
            "java_path": overrides.get("java_path", "java"),
            "min_memory": overrides.get("min_memory", 1024),
            "max_memory": overrides.get("max_memory", 2048),
            "port": overrides.get(
                "port", 25565 if server_type != ServerType.STEAM else 27015
            ),
            "steam_app_id": overrides.get(
                "steam_app_id", "730" if server_type == ServerType.STEAM else None
            ),
            "steam_branch": overrides.get(
                "steam_branch", "public" if server_type == ServerType.STEAM else None
            ),
            "steam_login_anonymous": overrides.get("steam_login_anonymous", True),
            "mc_version": overrides.get(
                "mc_version", "1.21.1" if server_type != ServerType.STEAM else None
            ),
            "loader": overrides.get(
                "loader", "fabric" if server_type == ServerType.MINECRAFT_JAVA else None
            ),
        }
        async with self.session_maker() as session:
            server = Server(**values)
            session.add(server)
            await session.commit()
            await session.refresh(server)
            return server

    async def test_steam_server_hides_mods_and_keeps_workshop(self):
        server = await self._create_server(ServerType.STEAM, name="Steam Tabs")

        response = await self.client.get(f"/servers/{server.id}")

        self.assertEqual(response.status_code, 200)
        self.check_update_mock.assert_not_awaited()
        self.assertNotIn('id="tab-mods"', response.text)
        self.assertNotIn('id="panel-mods"', response.text)
        self.assertIn('id="tab-workshop"', response.text)
        self.assertIn('id="panel-workshop"', response.text)
        self.assertIn('id="check-update-btn"', response.text)
        self.assertNotIn("Update available: Build", response.text)
        self.assertIn("getAvailableTabs", response.text)
        self.assertIn("availableTabs.indexOf(urlTab)", response.text)

    async def test_minecraft_server_hides_workshop_and_keeps_mods(self):
        server = await self._create_server(
            ServerType.MINECRAFT_JAVA, name="Minecraft Tabs"
        )

        response = await self.client.get(f"/servers/{server.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="tab-mods"', response.text)
        self.assertIn('id="panel-mods"', response.text)
        self.assertNotIn('id="tab-workshop"', response.text)
        self.assertNotIn('id="panel-workshop"', response.text)
