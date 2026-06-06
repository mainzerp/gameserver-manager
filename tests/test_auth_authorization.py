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
from app.models.server_access import ServerAccess
from app.models.user import User
from app.routers import ws
from app.services.auth import (
    RedirectException,
    get_current_user_dep,
    require_role,
    require_server_access,
)


class FakeWebSocket:
    def __init__(self, session=None):
        self.session = session or {}
        self.sent = []
        self.accepted = False
        self.closed_code = None
        self.closed_reason = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=None, reason=None):
        self.closed_code = code
        self.closed_reason = reason

    async def send_json(self, payload):
        self.sent.append(payload)


class WebSocketAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_console_ws_rejects_unauthenticated(self):
        fake_ws = FakeWebSocket(session={})
        await ws.console_ws(fake_ws, 1)
        self.assertFalse(fake_ws.accepted)
        self.assertEqual(fake_ws.closed_code, 4001)
        self.assertEqual(fake_ws.closed_reason, "Not authenticated")

    async def test_steamcmd_ws_rejects_unauthenticated(self):
        fake_ws = FakeWebSocket(session={})
        await ws.steamcmd_ws(fake_ws, 1)
        self.assertFalse(fake_ws.accepted)
        self.assertEqual(fake_ws.closed_code, 4001)
        self.assertEqual(fake_ws.closed_reason, "Not authenticated")


class RequireRoleUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_require_role_allows_sufficient_role(self):
        request = SimpleNamespace()
        request.session = {"user_id": 1}
        request.headers = {}

        with patch(
            "app.services.auth._get_any_user",
            AsyncMock(return_value=SimpleNamespace(role="admin")),
        ):
            user = await require_role(request, "admin")
            self.assertEqual(user.role, "admin")

    async def test_require_role_rejects_insufficient_role(self):
        request = SimpleNamespace()
        request.session = {"user_id": 1}
        request.headers = {}

        with patch(
            "app.services.auth._get_any_user",
            AsyncMock(return_value=SimpleNamespace(role="viewer")),
        ):
            from fastapi import HTTPException

            with self.assertRaises(HTTPException) as ctx:
                await require_role(request, "admin")
            self.assertEqual(ctx.exception.status_code, 403)


class RequireServerAccessUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_require_server_access_allows_admin(self):
        request = SimpleNamespace()
        request.session = {"user_id": 1}
        request.headers = {}

        async with async_sessionmaker(
            create_async_engine("sqlite+aiosqlite:///:memory:")
        )() as db:
            with patch(
                "app.services.auth._get_any_user",
                AsyncMock(return_value=SimpleNamespace(role="admin", id=1)),
            ):
                user = await require_server_access(request, 1, "manage", db)
                self.assertEqual(user.role, "admin")

    async def test_require_server_access_rejects_unauthorized(self):
        request = SimpleNamespace()
        request.session = {"user_id": 1}
        request.headers = {}

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as db:
            with patch(
                "app.services.auth._get_any_user",
                AsyncMock(return_value=SimpleNamespace(role="viewer", id=1)),
            ):
                from fastapi import HTTPException

                with self.assertRaises(HTTPException) as ctx:
                    await require_server_access(request, 1, "manage", db)
                self.assertEqual(ctx.exception.status_code, 403)


class AuthAuthorizationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path}")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="test-secret")

        @self.app.exception_handler(RedirectException)
        async def redirect_exception_handler(request, exc):
            from fastapi.responses import RedirectResponse

            return RedirectResponse(url=exc.url, status_code=303)

        from app.routers import servers

        self.app.include_router(servers.router)

        async def override_get_db():
            async with self.session_maker() as session:
                yield session

        async def override_current_user():
            return SimpleNamespace(id=1, role="admin")

        self.app.dependency_overrides[servers.get_db] = override_get_db
        self.app.dependency_overrides[get_current_user_dep] = override_current_user

        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(
            transport=self.transport,
            base_url="http://testserver",
            follow_redirects=False,
        )

        # Patch the async_session used by auth helpers so they use the test DB
        import app.database
        import app.services.auth

        self._orig_db_async_session = app.database.async_session
        self._orig_auth_async_session = app.services.auth.async_session
        app.database.async_session = self.session_maker
        app.services.auth.async_session = self.session_maker

        self.original_servers_dir = servers.settings.servers_dir
        servers.settings.servers_dir = self.temp_dir.name

    async def asyncTearDown(self):
        import app.database
        import app.services.auth
        from app.routers import servers

        app.database.async_session = self._orig_db_async_session
        app.services.auth.async_session = self._orig_auth_async_session
        servers.settings.servers_dir = self.original_servers_dir
        self.app.dependency_overrides.clear()
        await self.client.aclose()
        await self.engine.dispose()
        self.temp_dir.cleanup()

    async def _create_user(self, username: str, role: str = "viewer") -> User:
        async with self.session_maker() as session:
            user = User(
                username=username,
                password_hash="$2b$12$dummyhashfortestingpurposesonly",
                role=role,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def _create_server(self) -> Server:
        server_path = Path(self.temp_dir.name) / "test-server"
        server_path.mkdir(parents=True, exist_ok=True)
        async with self.session_maker() as session:
            server = Server(
                name="Auth Test Server",
                server_type=ServerType.MINECRAFT_JAVA,
                status=ServerStatus.STOPPED,
                path=str(server_path),
                executable="server.jar",
                start_command="java -jar server.jar nogui",
                port=25565,
            )
            session.add(server)
            await session.commit()
            await session.refresh(server)
            return server

    async def test_root_route_redirects_to_setup_when_no_users_exist(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/setup")

    async def test_root_route_redirects_to_login_when_not_authenticated(self):
        # Create a user so the app is past setup mode
        await self._create_user("existing_user", role="viewer")
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    async def test_admin_route_returns_403_for_non_admin(self):
        user = await self._create_user("viewer1", role="viewer")

        with patch(
            "app.services.auth._get_any_user",
            AsyncMock(return_value=user),
        ):
            response = await self.client.get("/servers/create")

        self.assertEqual(response.status_code, 403)

    async def test_server_access_returns_403_for_unauthorized_user(self):
        user = await self._create_user("viewer2", role="viewer")
        server = await self._create_server()

        with patch(
            "app.services.auth._get_any_user",
            AsyncMock(return_value=user),
        ):
            response = await self.client.get(f"/servers/{server.id}")

        self.assertEqual(response.status_code, 403)

    async def test_server_access_allowed_for_authorized_user(self):
        user = await self._create_user("operator1", role="viewer")
        server = await self._create_server()

        async with self.session_maker() as session:
            access = ServerAccess(
                user_id=user.id,
                server_id=server.id,
                permission="view",
            )
            session.add(access)
            await session.commit()

        with patch(
            "app.services.auth._get_any_user",
            AsyncMock(return_value=user),
        ):
            response = await self.client.get(f"/servers/{server.id}")

        self.assertEqual(response.status_code, 200)

    async def test_admin_route_allowed_for_admin(self):
        user = await self._create_user("admin1", role="admin")

        with patch(
            "app.services.auth._get_any_user",
            AsyncMock(return_value=user),
        ):
            response = await self.client.get("/servers/create")

        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
