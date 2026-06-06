import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.models.steam_account import SteamAccount, encrypt_password
from app.models.server import ServerType
from app.services.steam_workshop import steam_workshop_service
from app.services.steamcmd import (
    build_runtime_command,
    generate_start_command,
    steamcmd,
)


class FakeDb:
    def __init__(self, account):
        self._account = account

    async def get(self, _model, _account_id):
        return self._account


class SteamCmdServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_path = steamcmd._steamcmd_path
        self.original_state = dict(steamcmd._operation_state)
        self.original_waiters = dict(steamcmd._guard_waiters)
        self.original_subscribers = dict(steamcmd._progress_subscribers)
        self.original_locks = dict(steamcmd._server_locks)
        steamcmd._steamcmd_path = __file__
        steamcmd._operation_state.clear()
        steamcmd._guard_waiters.clear()
        steamcmd._progress_subscribers.clear()
        steamcmd._server_locks.clear()

    async def asyncTearDown(self):
        steamcmd._steamcmd_path = self.original_path
        steamcmd._operation_state = self.original_state
        steamcmd._guard_waiters = self.original_waiters
        steamcmd._progress_subscribers = self.original_subscribers
        steamcmd._server_locks = self.original_locks

    async def test_get_server_install_kwargs_blocks_guarded_unattended(self):
        account = SteamAccount(
            display_name="Guarded",
            username="steam-user",
            password_encrypted=encrypt_password("secret"),
            steam_guard_type="email",
        )
        db = FakeDb(account)
        server = SimpleNamespace(
            id=7,
            steam_branch="public",
            steam_login_anonymous=False,
            steam_account_id=1,
        )

        kwargs, error = await steamcmd.get_server_install_kwargs(
            db, server, interactive=False
        )
        self.assertFalse(kwargs["login_anonymous"])
        self.assertIn("Steam Guard", error)

        kwargs, error = await steamcmd.get_server_install_kwargs(
            db, server, interactive=True
        )
        self.assertIsNone(error)
        self.assertEqual(kwargs["username"], "steam-user")
        self.assertEqual(kwargs["password"], "secret")
        self.assertTrue(kwargs["interactive"])

    async def test_install_server_waits_for_steam_guard_and_resumes(self):
        run_results = iter(
            [
                {
                    "ok": False,
                    "status": "steam_guard_required",
                    "message": "Steam Guard code required.",
                    "percent": 12.5,
                },
                {
                    "ok": True,
                    "status": "completed",
                    "message": "Install complete.",
                    "percent": 100.0,
                    "build_id": "4242",
                },
            ]
        )

        async def fake_run_process(**_kwargs):
            return next(run_results)

        with patch.object(steamcmd, "_run_process", side_effect=fake_run_process):
            task = asyncio.create_task(
                steamcmd.install_server(
                    app_id="730",
                    install_dir=".",
                    validate=True,
                    server_id=99,
                    operation_type="install",
                    interactive=True,
                    login_anonymous=False,
                    username="steam-user",
                    password="secret",
                )
            )

            await asyncio.sleep(0.01)
            snapshot = steamcmd.get_operation_snapshot(99)
            self.assertEqual(snapshot["status"], "waiting_for_steam_guard")
            self.assertEqual(snapshot["operation"], "install")

            submit_result = await steamcmd.submit_steam_guard_code(
                99, snapshot["operation_id"], "123456"
            )
            self.assertTrue(submit_result["ok"])

            result = await task
            self.assertTrue(result["ok"])
            self.assertEqual(result["build_id"], "4242")

            final_snapshot = steamcmd.get_operation_snapshot(99)
            self.assertEqual(final_snapshot["status"], "completed")
            self.assertEqual(final_snapshot["build_id"], "4242")

    async def test_install_server_fails_fast_when_guard_is_required_non_interactive(
        self,
    ):
        async def fake_run_process(**_kwargs):
            return {
                "ok": False,
                "status": "steam_guard_required",
                "message": "Steam Guard code required.",
                "percent": 31.0,
            }

        with patch.object(steamcmd, "_run_process", side_effect=fake_run_process):
            result = await steamcmd.install_server(
                app_id="730",
                install_dir=".",
                server_id=55,
                operation_type="update",
                interactive=False,
                login_anonymous=False,
                username="steam-user",
                password="secret",
            )

        self.assertFalse(result["ok"])
        self.assertIn("without interactive user input", result["message"])
        self.assertEqual(steamcmd.get_operation_snapshot(55)["status"], "running")

    async def test_queue_operation_creates_reconnectable_snapshot(self):
        operation_id = await steamcmd.queue_operation(
            12, "validate", "Queued validation."
        )

        snapshot = steamcmd.get_operation_snapshot(12)
        self.assertEqual(snapshot["operation_id"], operation_id)
        self.assertEqual(snapshot["operation"], "validate")
        self.assertEqual(snapshot["status"], "queued")

    async def test_submit_steam_guard_code_rejects_wrong_operation(self):
        await steamcmd.queue_operation(13, "install", "Queued install.")

        result = await steamcmd.submit_steam_guard_code(13, "wrong-op", "123456")

        self.assertFalse(result["ok"])
        self.assertIn("no longer matches", result["message"])

    async def test_generate_start_command_for_gmod_remains_token_free(self):
        command = generate_start_command("4020", 27015, "Token Free")

        self.assertIsNotNone(command)
        self.assertIn("srcds_run", command)
        self.assertNotIn("sv_setsteamaccount", command)

    async def test_build_runtime_command_injects_gmod_gslt_only_for_app_4020(self):
        gmod_server = SimpleNamespace(
            name="GMod",
            server_type=ServerType.STEAM,
            steam_app_id="4020",
            start_command="./srcds_run -game garrysmod -port 27015",
            steam_gslt="gmod-token",
        )
        other_server = SimpleNamespace(
            name="CS2",
            server_type=ServerType.STEAM,
            steam_app_id="730",
            start_command="./game/bin/linuxsteamrt64/cs2 -dedicated -port 27015",
            steam_gslt="ignored-token",
        )

        gmod_cmd = build_runtime_command(gmod_server)
        other_cmd = build_runtime_command(other_server)

        self.assertEqual(gmod_cmd[-2:], ["+sv_setsteamaccount", "gmod-token"])
        self.assertNotIn("+sv_setsteamaccount", other_cmd)


class WorkshopMetadataServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_metadata_normalizes_workshop_payload(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "response": {
                "publishedfiledetails": [
                    {
                        "result": 1,
                        "title": "Sample Mod",
                        "file_description": "<b>Hello</b>   world",
                        "file_size": "2097152",
                        "preview_url": "https://cdn.example.test/mod.png",
                        "subscriptions": "42",
                        "time_created": 1713436800,
                        "time_updated": 1713523200,
                        "tags": [{"tag": "Maps"}, {"tag": "PvP"}],
                    }
                ]
            }
        }
        client = AsyncMock()
        client.get.return_value = response

        class ClientContext:
            async def __aenter__(self_inner):
                return client

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        with patch(
            "app.services.steam_workshop.httpx.AsyncClient",
            return_value=ClientContext(),
        ):
            metadata = await steam_workshop_service.fetch_metadata(
                "123", steam_api_key="api-key"
            )

        self.assertEqual(metadata["name"], "Sample Mod")
        self.assertEqual(metadata["description"], "Hello world")
        self.assertEqual(metadata["file_size"], 2097152)
        self.assertEqual(metadata["preview_url"], "https://cdn.example.test/mod.png")
        self.assertEqual(metadata["subscriptions"], 42)
        self.assertEqual(metadata["tags"], ["Maps", "PvP"])
        self.assertIsNotNone(metadata["created_at"])
        self.assertIsNotNone(metadata["last_updated"])

    async def test_fetch_metadata_returns_none_without_api_key(self):
        metadata = await steam_workshop_service.fetch_metadata("123", steam_api_key="")
        self.assertIsNone(metadata)
