import asyncio
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi import WebSocketDisconnect

from app.routers import ws


class FakeWebSocket:
    def __init__(self):
        self.session = {"user_id": 1}
        self.sent = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def close(self, code=None, reason=None):
        self.sent.append({"type": "closed", "code": code, "reason": reason})

    async def send_json(self, payload):
        self.sent.append(payload)


class FakeSteamCmd:
    def __init__(self):
        self.subscribed = []
        self.unsubscribed = []

    def subscribe_progress(self, server_id, queue):
        self.subscribed.append((server_id, queue))

    def unsubscribe_progress(self, server_id, queue):
        self.unsubscribed.append((server_id, queue))

    def get_operation_snapshot(self, server_id):
        return {
            "type": "snapshot",
            "server_id": server_id,
            "operation_id": "op-1",
            "operation": "update_start",
            "status": "running",
            "message": "Running",
            "percent": 10.0,
        }


class FakeQueue:
    async def get(self):
        return None


class SteamWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_ws_sends_snapshot_progress_and_heartbeat(self):
        fake_steam = FakeSteamCmd()
        fake_ws = FakeWebSocket()
        fake_queue = FakeQueue()
        steps = iter(
            [
                {
                    "type": "progress",
                    "server_id": 5,
                    "operation_id": "op-1",
                    "operation": "update_start",
                    "status": "running",
                    "message": "Downloading",
                    "percent": 25.0,
                },
                asyncio.TimeoutError(),
                WebSocketDisconnect(),
            ]
        )

        async def fake_wait_for(_awaitable, timeout):
            if hasattr(_awaitable, "close"):
                _awaitable.close()
            step = next(steps)
            if isinstance(step, Exception):
                raise step
            return step

        with (
            patch("app.services.steamcmd.steamcmd", fake_steam),
            patch("asyncio.Queue", return_value=fake_queue),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
        ):
            await ws.steamcmd_ws(fake_ws, 5)

        self.assertTrue(fake_ws.accepted)
        self.assertEqual(fake_ws.sent[0]["type"], "snapshot")
        self.assertEqual(fake_ws.sent[0]["operation"], "update_start")
        self.assertEqual(fake_ws.sent[1]["type"], "progress")
        self.assertEqual(fake_ws.sent[1]["operation"], "update_start")
        self.assertEqual(fake_ws.sent[2]["type"], "heartbeat")
        self.assertEqual(fake_steam.subscribed[0][0], 5)
        self.assertEqual(fake_steam.unsubscribed[0][0], 5)
