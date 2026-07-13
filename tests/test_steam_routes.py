import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

import app.models  # noqa: F401
from app.database import Base
from app.models.server import Server, ServerStatus, ServerType
from app.models.workshop_item import WorkshopItem
from app.routers import servers


class SteamRouteTests(unittest.IsolatedAsyncioTestCase):
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
        self.app.dependency_overrides[servers.get_current_user_dep] = override_current_user

        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(
            transport=self.transport,
            base_url="http://testserver",
            follow_redirects=False,
        )

        self.original_servers_dir = servers.settings.servers_dir
        self.original_steamcmd_path = servers.steamcmd._steamcmd_path
        self.original_async_session = servers.async_session
        servers.settings.servers_dir = self.temp_dir.name
        servers.steamcmd._steamcmd_path = __file__
        servers.async_session = self.session_maker

        self.spawned = []

        def capture_task(coro):
            self.spawned.append(coro)
            coro.close()

        self.spawn_patch = patch.object(servers, "_spawn_background_task", side_effect=capture_task)
        self.role_patch = patch.object(
            servers,
            "require_role",
            AsyncMock(return_value=SimpleNamespace(id=1, role="admin")),
        )
        self.access_patch = patch.object(
            servers,
            "require_server_access",
            AsyncMock(return_value=SimpleNamespace(id=1, role="admin")),
        )
        self.log_patch = patch.object(servers.audit_service, "log", AsyncMock(return_value=None))
        self.create_task_patch = patch.object(
            servers.audit_service, "create_task", side_effect=lambda coro: coro.close()
        )

        self.spawn_patch.start()
        self.role_patch.start()
        self.access_patch.start()
        self.log_patch.start()
        self.create_task_patch.start()

    async def asyncTearDown(self):
        self.spawn_patch.stop()
        self.role_patch.stop()
        self.access_patch.stop()
        self.log_patch.stop()
        self.create_task_patch.stop()
        servers.settings.servers_dir = self.original_servers_dir
        servers.steamcmd._steamcmd_path = self.original_steamcmd_path
        servers.async_session = self.original_async_session
        await self.client.aclose()
        await self.engine.dispose()
        self.temp_dir.cleanup()

    async def _create_server(self, **overrides) -> Server:
        server_path = Path(self.temp_dir.name) / overrides.get("path_name", f"server-{len(self.spawned)}")
        server_path.mkdir(parents=True, exist_ok=True)
        values = {
            "name": overrides.get("name", "Steam Test"),
            "server_type": overrides.get("server_type", ServerType.STEAM),
            "status": overrides.get("status", ServerStatus.STOPPED),
            "path": str(server_path),
            "executable": overrides.get("executable", "server.sh"),
            "start_command": overrides.get("start_command", "./server.sh"),
            "java_path": "java",
            "min_memory": 1024,
            "max_memory": 2048,
            "port": overrides.get("port", 27015),
            "query_port": overrides.get("query_port"),
            "steam_app_id": overrides.get("steam_app_id", "730"),
            "steam_branch": overrides.get("steam_branch", "public"),
            "steam_login_anonymous": overrides.get("steam_login_anonymous", True),
            "steam_account_id": overrides.get("steam_account_id"),
            "steam_update_on_start": overrides.get("steam_update_on_start", False),
        }
        async with self.session_maker() as session:
            server = Server(**values)
            if "steam_gslt" in overrides:
                server.steam_gslt = overrides.get("steam_gslt")
            session.add(server)
            await session.commit()
            await session.refresh(server)
            return server

    async def test_create_steam_server_requires_account_when_anonymous_disabled(self):
        response = await self.client.post(
            "/servers/create",
            data={
                "name": "Guarded Create",
                "server_type": "steam",
                "port": "27015",
                "min_memory": "1024",
                "max_memory": "2048",
                "steam_app_id": "730",
                "steam_login_anonymous": "false",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Select a Steam account or enable anonymous login for Steam servers.",
            response.text,
        )

    async def test_create_steam_server_queues_background_install(self):
        response = await self.client.post(
            "/servers/create",
            data={
                "name": "Queued Steam Create",
                "server_type": "steam",
                "port": "27015",
                "min_memory": "1024",
                "max_memory": "2048",
                "steam_app_id": "730",
                "steam_login_anonymous": "true",
            },
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(self.spawned), 1)
        async with self.session_maker() as session:
            result = await session.execute(select(Server))
            created = result.scalars().all()
            self.assertEqual(len(created), 1)

        snapshot = servers.steamcmd.get_operation_snapshot(created[0].id)
        self.assertEqual(snapshot["status"], "queued")
        self.assertEqual(snapshot["operation"], "install")

    async def test_create_steam_server_persists_query_port(self):
        response = await self.client.post(
            "/servers/create",
            data={
                "name": "Steam With Query Port",
                "server_type": "steam",
                "port": "8211",
                "query_port": "27025",
                "min_memory": "1024",
                "max_memory": "2048",
                "steam_app_id": "2394010",
                "steam_login_anonymous": "true",
            },
        )

        self.assertEqual(response.status_code, 303)
        async with self.session_maker() as session:
            result = await session.execute(select(Server))
            created = result.scalars().all()
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].query_port, 27025)
            self.assertIn("27025", created[0].start_command)

    async def test_update_steam_settings_persists_query_port(self):
        server = await self._create_server(
            name="Steam Query Update", steam_app_id="2394010", query_port=8212
        )

        response = await self.client.post(
            f"/servers/{server.id}/steam/settings",
            data={
                "steam_app_id": "2394010",
                "steam_branch": "public",
                "steam_login_anonymous": "true",
                "steam_account_id": "",
                "steam_gslt": "",
                "clear_steam_gslt": "false",
                "steam_update_on_start": "false",
                "query_port": "27030",
            },
        )

        self.assertEqual(response.status_code, 303)
        async with self.session_maker() as session:
            refreshed = await session.get(Server, server.id)
            self.assertEqual(refreshed.query_port, 27030)
            self.assertIn("27030", refreshed.start_command)

    async def test_update_steam_settings_rejects_conflicting_query_port(self):
        server = await self._create_server(
            name="Steam Query Update",
            steam_app_id="2394010",
            query_port=8212,
            port=8211,
        )
        await self._create_server(
            name="Other Steam",
            steam_app_id="2394010",
            query_port=27030,
            port=27029,
        )

        response = await self.client.post(
            f"/servers/{server.id}/steam/settings",
            data={
                "steam_app_id": "2394010",
                "steam_branch": "public",
                "steam_login_anonymous": "true",
                "steam_account_id": "",
                "steam_gslt": "",
                "clear_steam_gslt": "false",
                "steam_update_on_start": "false",
                "query_port": "27030",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Query port", response.text)

    async def test_update_steam_settings_allows_unchanged_query_port(self):
        server = await self._create_server(
            name="Steam Query Update", steam_app_id="2394010", query_port=8212, port=8211
        )

        response = await self.client.post(
            f"/servers/{server.id}/steam/settings",
            data={
                "steam_app_id": "2394010",
                "steam_branch": "public",
                "steam_login_anonymous": "true",
                "steam_account_id": "",
                "steam_gslt": "",
                "clear_steam_gslt": "false",
                "steam_update_on_start": "false",
                "query_port": "8212",
            },
        )

        self.assertEqual(response.status_code, 303)


    async def test_manual_update_and_validate_routes_queue_background_work(self):
        server = await self._create_server(name="Queue Ops")

        update_response = await self.client.post(f"/servers/{server.id}/steam/update")
        update_snapshot = servers.steamcmd.get_operation_snapshot(server.id)
        validate_response = await self.client.post(f"/servers/{server.id}/steam/validate")
        validate_snapshot = servers.steamcmd.get_operation_snapshot(server.id)

        self.assertEqual(update_response.status_code, 303)
        self.assertEqual(validate_response.status_code, 303)
        self.assertEqual(len(self.spawned), 2)
        self.assertEqual(update_snapshot["status"], "queued")
        self.assertEqual(update_snapshot["operation"], "update")
        self.assertEqual(validate_snapshot["status"], "queued")
        self.assertEqual(validate_snapshot["operation"], "validate")

    async def test_start_route_queues_background_update_for_steam_update_on_start(self):
        server = await self._create_server(name="Queue Start", steam_update_on_start=True)

        with (
            patch.object(
                servers.server_manager,
                "start_server",
                AsyncMock(return_value={"ok": True, "error": None}),
            ) as start_mock,
            patch.object(servers.server_manager, "is_running", return_value=False),
        ):
            response = await self.client.post(f"/servers/{server.id}/start")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(self.spawned), 1)
        snapshot = servers.steamcmd.get_operation_snapshot(server.id)
        self.assertEqual(snapshot["status"], "queued")
        self.assertEqual(snapshot["operation"], "update_start")
        start_mock.assert_not_awaited()

    async def test_background_update_then_start_runs_update_before_start(self):
        server = await self._create_server(name="Update Then Start", steam_update_on_start=True)
        events = []

        async def capture_event(**payload):
            events.append(payload)

        with (
            patch.object(
                servers.steamcmd,
                "get_server_install_kwargs",
                AsyncMock(
                    return_value=(
                        {
                            "branch": "public",
                            "login_anonymous": True,
                            "server_id": server.id,
                            "interactive": False,
                        },
                        None,
                    )
                ),
            ),
            patch.object(
                servers.steamcmd,
                "update_server",
                AsyncMock(return_value={"ok": True, "build_id": "9002", "message": "updated"}),
            ) as update_mock,
            patch.object(servers.steamcmd, "_publish_event", AsyncMock(side_effect=capture_event)) as publish_mock,
            patch.object(
                servers.server_manager,
                "start_server",
                AsyncMock(return_value={"ok": True, "error": None}),
            ) as start_mock,
            patch.object(servers.server_manager, "_reset_crash_state") as reset_mock,
        ):
            await servers._run_background_steam_update_then_start(server.id, "op-start")

        update_mock.assert_awaited_once()
        start_mock.assert_awaited_once_with(server.id, skip_steam_update=True)
        reset_mock.assert_called_once_with(server.id)
        self.assertGreaterEqual(publish_mock.await_count, 2)
        self.assertEqual(events[0]["message"], "Steam update completed. Starting server...")
        self.assertEqual(events[-1]["message"], "Steam update completed. Server start requested.")

        async with self.session_maker() as session:
            refreshed = await session.get(Server, server.id)
            self.assertEqual(refreshed.steam_build_id, "9002")
            self.assertIsNotNone(refreshed.steam_last_update)

    async def test_background_update_then_start_does_not_start_on_failure(self):
        server = await self._create_server(name="Update Fails", steam_update_on_start=True)

        with (
            patch.object(
                servers.steamcmd,
                "get_server_install_kwargs",
                AsyncMock(
                    return_value=(
                        {
                            "branch": "public",
                            "login_anonymous": True,
                            "server_id": server.id,
                            "interactive": False,
                        },
                        None,
                    )
                ),
            ),
            patch.object(
                servers.steamcmd,
                "update_server",
                AsyncMock(return_value={"ok": False, "message": "boom"}),
            ) as update_mock,
            patch.object(
                servers.server_manager,
                "start_server",
                AsyncMock(return_value={"ok": True, "error": None}),
            ) as start_mock,
        ):
            await servers._run_background_steam_update_then_start(server.id, "op-start")

        update_mock.assert_awaited_once()
        start_mock.assert_not_awaited()

    async def test_background_update_then_start_surfaces_unattended_steam_guard_failure(
        self,
    ):
        server = await self._create_server(
            name="Guard Failure",
            steam_update_on_start=True,
            steam_login_anonymous=False,
        )
        events = []

        async def capture_event(**payload):
            events.append(payload)

        with (
            patch.object(
                servers.steamcmd,
                "get_server_install_kwargs",
                AsyncMock(return_value=({}, "Steam Guard is required.")),
            ),
            patch.object(servers.steamcmd, "_publish_event", AsyncMock(side_effect=capture_event)) as publish_mock,
            patch.object(
                servers.server_manager,
                "start_server",
                AsyncMock(return_value={"ok": True, "error": None}),
            ) as start_mock,
        ):
            await servers._run_background_steam_update_then_start(server.id, "op-start")

        publish_mock.assert_awaited_once()
        start_mock.assert_not_awaited()
        self.assertEqual(events[0]["operation_type"], "update_start")
        self.assertEqual(events[0]["status"], "failed")
        self.assertEqual(events[0]["message"], "Steam Guard is required.")

    async def test_steam_detail_page_exposes_top_level_operation_panel_for_handoff(
        self,
    ):
        server = await self._create_server(name="Steam Detail Handoff")

        response = await self.client.get(f"/servers/{server.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="steam-operation-panel"', response.text)
        self.assertIn("Steam Update Before Start", response.text)

    async def test_steam_starting_detail_page_keeps_cancelable_reload_and_update_start_suppression(
        self,
    ):
        server = await self._create_server(
            name="Steam Starting Detail",
            status=ServerStatus.STARTING,
            steam_update_on_start=True,
        )

        idle_snapshot = {
            "type": "snapshot",
            "server_id": server.id,
            "operation_id": None,
            "operation": None,
            "status": "idle",
            "message": "Idle",
            "percent": 0.0,
            "workshop_item_id": None,
            "build_id": None,
            "timestamp": "2026-04-19T00:00:00+00:00",
        }

        with patch.object(servers.steamcmd, "get_operation_snapshot", return_value=idle_snapshot):
            response = await self.client.get(f"/servers/{server.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "let hasSeenMeaningfulSteamActivity = "
            "isMeaningfulSteamStatus(seededSteamOperationSnapshot && "
            "seededSteamOperationSnapshot.status);",
            response.text,
        )
        self.assertIn('data-seeded-active-update-start="false"', response.text)
        self.assertIn("const activeUpdateStartStatuses = {", response.text)
        self.assertIn("const meaningfulSteamStatuses = {", response.text)
        self.assertIn(
            "let hasSeenMeaningfulSteamActivity = "
            "isMeaningfulSteamStatus(seededSteamOperationSnapshot && "
            "seededSteamOperationSnapshot.status);",
            response.text,
        )
        self.assertIn("waiting_for_steam_guard: true", response.text)
        self.assertIn("function cancelStartingReloadTimer()", response.text)
        self.assertIn("function isActiveUpdateStartOperation(event, currentStatus)", response.text)
        self.assertIn("function isMeaningfulSteamStatus(currentStatus)", response.text)
        self.assertIn("event.operation === 'update_start'", response.text)
        self.assertIn(
            "if (event.type === 'snapshot' && currentStatus === 'idle') {",
            response.text,
        )
        self.assertIn("if (!hasSeenMeaningfulSteamActivity) {", response.text)
        self.assertIn("cancelStartingReloadTimer();", response.text)

    async def test_steam_starting_detail_page_seeds_active_update_start_and_skips_generic_reload(
        self,
    ):
        server = await self._create_server(
            name="Steam Seeded Detail",
            status=ServerStatus.STARTING,
            steam_update_on_start=True,
        )
        active_snapshot = {
            "type": "snapshot",
            "server_id": server.id,
            "operation_id": "op-seeded",
            "operation": "update_start",
            "status": "queued",
            "message": "Queued Steam update before start.",
            "percent": 12.0,
            "workshop_item_id": None,
            "build_id": None,
            "timestamp": "2026-04-19T00:00:00+00:00",
        }

        with patch.object(servers.steamcmd, "get_operation_snapshot", return_value=active_snapshot):
            response = await self.client.get(f"/servers/{server.id}")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "window.serverDetailStartingReloadTimer = window.setTimeout(function(){location.reload();}, 5000);",
            response.text,
        )
        self.assertIn('data-seeded-active-update-start="true"', response.text)
        self.assertIn(
            'id="steam-operation-message" class="text-xs" '
            'style="color: var(--clr-text-secondary);">'
            "Queued Steam update before start.</p>",
            response.text,
        )
        self.assertIn(
            'id="steam-operation-progress-text" class="mt-2 text-xs" style="color: var(--clr-text-muted);">12%</p>',
            response.text,
        )
        self.assertIn("const seededActiveUpdateStart = true;", response.text)
        self.assertIn(
            "let lastState = seededActiveUpdateStart ? seededSteamOperationSnapshot : null;",
            response.text,
        )
        self.assertIn(
            "let hasSeenMeaningfulSteamActivity = "
            "isMeaningfulSteamStatus(seededSteamOperationSnapshot && "
            "seededSteamOperationSnapshot.status);",
            response.text,
        )
        self.assertIn("if (isMeaningfulSteamStatus(currentStatus)) {", response.text)
        self.assertIn("hasSeenMeaningfulSteamActivity = true;", response.text)

    async def test_steam_detail_page_only_reloads_for_live_update_start_completion(
        self,
    ):
        server = await self._create_server(name="Steam Completion Reload Guard")

        response = await self.client.get(f"/servers/{server.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("} else if (currentStatus === 'completed') {", response.text)
        self.assertIn("setStatusBadge('completed', 'Completed');", response.text)
        self.assertIn("updateGuardState(false);", response.text)
        self.assertIn(
            "if (event.operation === 'update_start' && event.type !== 'snapshot') {",
            response.text,
        )
        self.assertIn("window.setTimeout(function() { location.reload(); }, 1200);", response.text)

    async def test_workshop_add_and_update_queue_background_work(self):
        server = await self._create_server(name="Workshop Queue")

        add_response = await self.client.post(
            f"/servers/{server.id}/workshop/add",
            data={"workshop_id": "1234567890", "name": ""},
        )
        self.assertEqual(add_response.status_code, 303)
        add_snapshot = servers.steamcmd.get_operation_snapshot(server.id)
        self.assertEqual(add_snapshot["status"], "queued")
        self.assertEqual(add_snapshot["operation"], "workshop_install")
        self.assertEqual(add_snapshot["workshop_item_id"], "1234567890")

        async with self.session_maker() as session:
            item = (
                (await session.execute(select(WorkshopItem).where(WorkshopItem.server_id == server.id))).scalars().one()
            )
            self.assertFalse(item.installed)
            item_id = item.id

        update_response = await self.client.post(f"/servers/{server.id}/workshop/{item_id}/update")
        self.assertEqual(update_response.status_code, 303)
        self.assertEqual(len(self.spawned), 2)
        update_snapshot = servers.steamcmd.get_operation_snapshot(server.id)
        self.assertEqual(update_snapshot["status"], "queued")
        self.assertEqual(update_snapshot["operation"], "workshop_update")
        self.assertEqual(update_snapshot["workshop_item_id"], "1234567890")

    async def test_submit_steam_guard_route_returns_json_result(self):
        server = await self._create_server(name="Guard Submit")

        with patch.object(
            servers.steamcmd,
            "submit_steam_guard_code",
            AsyncMock(return_value={"ok": True, "message": "accepted"}),
        ):
            response = await self.client.post(
                f"/servers/{server.id}/steam/guard",
                data={"operation_id": "op-1", "steam_guard_code": "123456"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "accepted")

    async def test_submit_steam_guard_route_returns_400_on_error(self):
        server = await self._create_server(name="Guard Submit Error")

        with patch.object(
            servers.steamcmd,
            "submit_steam_guard_code",
            AsyncMock(return_value={"ok": False, "message": "no challenge"}),
        ):
            response = await self.client.post(
                f"/servers/{server.id}/steam/guard",
                data={"operation_id": "op-1", "steam_guard_code": "123456"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], "no challenge")

    async def test_update_check_endpoint_still_calls_server_updater(self):
        server = await self._create_server(name="Async Update Check")

        check_result = {
            "update_available": True,
            "latest": "9002",
            "local_build_id": "9001",
            "remote_build_id": "9002",
        }
        with patch.object(
            servers.server_updater, "check_update", AsyncMock(return_value=check_result)
        ) as check_update_mock:
            response = await self.client.get(f"/servers/{server.id}/update-check")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "data": check_result})
        check_update_mock.assert_awaited_once()

        async with self.session_maker() as session:
            refreshed = await session.get(Server, server.id)
            self.assertEqual(refreshed.latest_known_version, "9002")

    async def test_create_install_helper_persists_build_and_command(self):
        server = await self._create_server(name="Install Helper")

        with (
            patch.object(
                servers.steamcmd,
                "get_server_install_kwargs",
                AsyncMock(
                    return_value=(
                        {
                            "server_id": server.id,
                            "interactive": True,
                            "login_anonymous": True,
                            "branch": "public",
                        },
                        None,
                    )
                ),
            ),
            patch.object(
                servers.steamcmd,
                "install_server",
                AsyncMock(return_value={"ok": True, "build_id": "9001"}),
            ),
        ):
            await servers._run_create_steam_install(server.id, "op-install")

        async with self.session_maker() as session:
            refreshed = await session.get(Server, server.id)
            self.assertEqual(refreshed.steam_build_id, "9001")
            self.assertIsNotNone(refreshed.steam_last_update)
            self.assertTrue(refreshed.start_command)

    async def test_update_steam_settings_persists_encrypted_gslt_and_keeps_command_token_free(
        self,
    ):
        server = await self._create_server(
            name="GMod Settings",
            steam_app_id="4020",
            start_command="./srcds_run -game garrysmod -port 27015",
        )

        response = await self.client.post(
            f"/servers/{server.id}/steam/settings",
            data={
                "steam_app_id": "4020",
                "steam_branch": "public",
                "steam_login_anonymous": "true",
                "steam_account_id": "",
                "steam_gslt": "gslt-secret-token",
                "clear_steam_gslt": "false",
                "steam_update_on_start": "false",
            },
        )

        self.assertEqual(response.status_code, 303)
        async with self.session_maker() as session:
            refreshed = await session.get(Server, server.id)
            self.assertIsNotNone(refreshed.steam_gslt_encrypted)
            self.assertNotEqual(refreshed.steam_gslt_encrypted, "gslt-secret-token")
            self.assertEqual(refreshed.steam_gslt, "gslt-secret-token")
            self.assertNotIn("gslt-secret-token", refreshed.start_command)
            self.assertNotIn("+sv_setsteamaccount", refreshed.start_command)

    async def test_update_steam_settings_can_clear_existing_gslt(self):
        server = await self._create_server(name="GMod Clear", steam_app_id="4020", steam_gslt="existing-token")

        response = await self.client.post(
            f"/servers/{server.id}/steam/settings",
            data={
                "steam_app_id": "4020",
                "steam_branch": "public",
                "steam_login_anonymous": "true",
                "steam_account_id": "",
                "steam_gslt": "",
                "clear_steam_gslt": "true",
                "steam_update_on_start": "false",
            },
        )

        self.assertEqual(response.status_code, 303)
        async with self.session_maker() as session:
            refreshed = await session.get(Server, server.id)
            self.assertIsNone(refreshed.steam_gslt_encrypted)
            self.assertFalse(refreshed.has_steam_gslt)

    async def test_steam_detail_page_renders_gmod_gslt_ui_and_guidance_only_for_gmod(
        self,
    ):
        gmod_server = await self._create_server(name="GMod Detail", steam_app_id="4020", steam_gslt="detail-token")
        other_server = await self._create_server(name="CS2 Detail", steam_app_id="730")

        gmod_response = await self.client.get(f"/servers/{gmod_server.id}")
        other_response = await self.client.get(f"/servers/{other_server.id}")

        self.assertEqual(gmod_response.status_code, 200)
        self.assertIn("GMod GSLT", gmod_response.text)
        self.assertIn("Configured", gmod_response.text)
        self.assertIn("+sv_setsteamaccount", gmod_response.text)
        self.assertIn("base game app id", gmod_response.text)
        self.assertEqual(other_response.status_code, 200)
        self.assertIn("GMod GSLT", other_response.text)
        self.assertNotIn("+sv_setsteamaccount", other_response.text)
        self.assertNotIn("base game app id", other_response.text)

    async def test_steam_detail_page_hides_minecraft_version_widgets(self):
        server = await self._create_server(name="Steam Version UI", steam_app_id="1690800")
        async with self.session_maker() as session:
            refreshed = await session.get(Server, server.id)
            refreshed.mc_version = "26.1.2"
            await session.commit()

        response = await self.client.get(f"/servers/{server.id}")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Max Compatible:", response.text)
        self.assertNotIn("Refresh now", response.text)
        self.assertNotIn("Version:</span>", response.text)
