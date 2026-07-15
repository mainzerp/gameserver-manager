import os
import unittest

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("GSM_SECRET_KEY", "test-secret-key-not-for-production")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.config import settings
from app.database import Base
from app.services import settings_service


class SettingsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._settings_snapshot = self._snapshot_smtp_settings()
        settings.encryption_key = "test-encryption-key-not-for-production"

    async def asyncTearDown(self):
        self._restore_smtp_settings(self._settings_snapshot)
        await self.engine.dispose()

    def _snapshot_smtp_settings(self) -> dict:
        return {
            "smtp_enabled": settings.smtp_enabled,
            "smtp_host": settings.smtp_host,
            "smtp_port": settings.smtp_port,
            "smtp_user": settings.smtp_user,
            "smtp_password": settings.smtp_password,
            "smtp_use_tls": settings.smtp_use_tls,
            "smtp_from_address": settings.smtp_from_address,
            "smtp_to_addresses": settings.smtp_to_addresses,
            "smtp_notify_events": settings.smtp_notify_events,
        }

    def _restore_smtp_settings(self, snapshot: dict) -> None:
        for key, value in snapshot.items():
            setattr(settings, key, value)

    async def test_get_or_create_creates_row_with_id_one(self):
        async with self.session_maker() as session:
            row = await settings_service._get_or_create(session)
            self.assertEqual(row.id, 1)

    async def test_get_or_create_returns_existing_row(self):
        async with self.session_maker() as session:
            first = await settings_service._get_or_create(session)
            first.smtp_host = "smtp.example.com"
            await session.commit()

        async with self.session_maker() as session:
            second = await settings_service._get_or_create(session)
            self.assertEqual(second.id, 1)
            self.assertEqual(second.smtp_host, "smtp.example.com")

    async def test_save_to_db_persists_smtp_and_hydrates_settings(self):
        form_data = {
            "smtp_enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_user": "user",
            "smtp_password": "secret",
            "smtp_use_tls": False,
            "smtp_from_address": "from@example.com",
            "smtp_to_addresses": "to@example.com",
            "smtp_notify_events": "crash,start",
            "totp_global_enabled": False,
            "multi_node_enabled": False,
            "webauthn_enabled": False,
            "webauthn_rp_id": "localhost",
            "webauthn_origin": "https://localhost:8443",
            "discord_webhook_url": "",
            "discord_notify_events": "start,stop",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "telegram_notify_events": "crash",
            "backup_external_path": "",
            "steam_api_key": "",
        }

        async with self.session_maker() as session:
            await settings_service.save_to_db(session, form_data)

        self.assertTrue(settings.smtp_enabled)
        self.assertEqual(settings.smtp_host, "smtp.example.com")
        self.assertEqual(settings.smtp_port, 465)
        self.assertEqual(settings.smtp_user, "user")
        self.assertEqual(settings.smtp_from_address, "from@example.com")
        self.assertEqual(settings.smtp_to_addresses, "to@example.com")
        self.assertEqual(settings.smtp_notify_events, "crash,start")

        async with self.session_maker() as session:
            row = await settings_service._get_or_create(session)
            self.assertTrue(row.smtp_enabled)
            self.assertEqual(row.smtp_host, "smtp.example.com")
            self.assertEqual(row.smtp_port, 465)
            self.assertEqual(row.smtp_user, "user")
            self.assertTrue(row.smtp_password_enc)
            self.assertEqual(row.smtp_from_address, "from@example.com")
            self.assertEqual(row.smtp_to_addresses, "to@example.com")
            self.assertEqual(row.smtp_notify_events, "crash,start")


if __name__ == "__main__":
    unittest.main()
