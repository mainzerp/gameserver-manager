import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

import app.models  # noqa: F401
from app.database import Base
from app.models.server import Server, ServerStatus, ServerType
from app.routers.api_v1 import servers as api_servers


class SteamApiV1Tests(unittest.IsolatedAsyncioTestCase):
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

        self.app.include_router(api_servers.router)

        async def override_get_db():
            async with self.session_maker() as session:
                yield session

        async def override_current_user():
            return SimpleNamespace(id=1, role="admin")

        self.app.dependency_overrides[api_servers.get_db] = override_get_db
        self.app.dependency_overrides[api_servers.get_current_user_flexible] = override_current_user

        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(
            transport=self.transport,
            base_url="http://testserver",
            follow_redirects=False,
        )

        self.access_patch = patch.object(
            api_servers,
            "require_server_access",
            AsyncMock(return_value=SimpleNamespace(id=1, role="admin")),
        )
        self.access_patch.start()

        self.tasks = []

        def capture_task(coro):
            self.tasks.append(coro)
            coro.close()

        self.spawn_patch = patch.object(
            api_servers, "_spawn_background_task", side_effect=capture_task
        )
        self.spawn_patch.start()

    async def asyncTearDown(self):
        self.access_patch.stop()
        self.spawn_patch.stop()
        await self.client.aclose()
        await self.engine.dispose()
        self.temp_dir.cleanup()

    async def _create_server(self, **overrides) -> Server:
        server_path = Path(self.temp_dir.name) / overrides.get("path_name", f"server-{len(self.tasks)}")
        server_path.mkdir(parents=True, exist_ok=True)
        values = {
            "name": overrides.get("name", "Steam Test"),
            "server_type": overrides.get("server_type", ServerType.STEAM),
            "status": overrides.get("status", ServerStatus.STOPPED),
            "path": str(server_path),
            "executable": overrides.get("executable", "srcds_run"),
            "start_command": overrides.get("start_command", "./srcds_run"),
            "port": overrides.get("port", 27015),
            "query_port": overrides.get("query_port", 27016),
            "steam_app_id": overrides.get("steam_app_id", "740"),
        }
        async with self.session_maker() as session:
            server = Server(**values)
            session.add(server)
            await session.commit()
            return server

    async def test_steam_update_endpoint_queues_operation(self):
        server = await self._create_server()
        with patch.object(api_servers.steamcmd, "queue_operation", AsyncMock(return_value="op-123")):
            response = await self.client.post(f"/servers/{server.id}/steam/update")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["data"]["operation_id"] == "op-123"
        assert len(self.tasks) == 1

    async def test_steam_validate_endpoint_queues_operation(self):
        server = await self._create_server()
        with patch.object(api_servers.steamcmd, "queue_operation", AsyncMock(return_value="op-456")):
            response = await self.client.post(f"/servers/{server.id}/steam/validate")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["data"]["operation_id"] == "op-456"

    async def test_steam_guard_endpoint_accepts_code(self):
        server = await self._create_server()
        with patch(
            "app.services.steamcmd.SteamCMD.submit_steam_guard_code",
            AsyncMock(return_value={"ok": True, "message": "accepted"}),
        ) as mock_submit:
            response = await self.client.post(
                f"/servers/{server.id}/steam/guard",
                json={"operation_id": "op-123", "steam_guard_code": "12345"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        mock_submit.assert_awaited_once_with(server.id, "op-123", "12345")
    async def test_steam_status_endpoint_returns_info(self):
        server = await self._create_server()
        with patch.object(
            api_servers.steam_query,
            "query_info",
            AsyncMock(return_value={"name": "Test", "players": 5, "max_players": 32}),
        ):
            response = await self.client.get(f"/servers/{server.id}/steam/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["data"]["players"] == 5

    async def test_workshop_preview_endpoint_returns_metadata(self):
        server = await self._create_server()
        with patch(
            "app.services.steam_workshop.SteamWorkshopService.fetch_metadata",
            AsyncMock(return_value={"name": "Cool Map", "file_size": 1024}),
        ):
            response = await self.client.get(
                f"/servers/{server.id}/workshop/preview/1234567890"
            )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["data"]["name"] == "Cool Map"

    async def test_workshop_preview_endpoint_not_found(self):
        server = await self._create_server()
        with patch(
            "app.services.steam_workshop.SteamWorkshopService.fetch_metadata",
            AsyncMock(return_value=None),
        ):
            response = await self.client.get(
                f"/servers/{server.id}/workshop/preview/1234567890"
            )
        assert response.status_code == 404
        data = response.json()
        assert data["ok"] is False


if __name__ == "__main__":
    unittest.main()
