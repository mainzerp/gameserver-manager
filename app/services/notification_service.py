import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.config import settings

logger = logging.getLogger(__name__)


class NotificationService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _is_event_enabled(self, event: str) -> bool:
        if not settings.discord_webhook_url:
            return False
        enabled = [e.strip() for e in settings.discord_notify_events.split(",")]
        return event in enabled

    async def _get_server_notification_config(
        self, server_id: int | None
    ) -> dict | None:
        if not server_id:
            return None
        from app.database import async_session
        from app.models.server import Server

        async with async_session() as session:
            server = await session.get(Server, server_id)
            if not server:
                return None
            return {
                "muted": server.notifications_muted,
                "webhook_url": server.notification_webhook_url,
                "events": server.notification_events,
            }

    async def notify(
        self,
        event: str,
        title: str,
        description: str,
        color: int = 0x6366F1,
        fields: list[dict] | None = None,
        server_id: int | None = None,
    ):
        # Check per-server muting
        server_config = await self._get_server_notification_config(server_id)
        if server_config and server_config["muted"]:
            return
        # Per-server event filter
        if server_config and server_config["events"]:
            server_events = [e.strip() for e in server_config["events"].split(",")]
            if event not in server_events:
                return

        if self._is_event_enabled(event):
            try:
                await self._send_discord(title, description, color, fields)
            except Exception as e:
                logger.warning(f"Failed to send Discord notification: {e}")

        # Per-server webhook override
        if server_config and server_config.get("webhook_url"):
            try:
                embed = {
                    "title": title,
                    "description": description,
                    "color": color,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                if fields:
                    embed["fields"] = fields
                payload = {"embeds": [embed]}
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(server_config["webhook_url"], json=payload)
                    resp.raise_for_status()
            except Exception as e:
                logger.warning(f"Failed to send per-server webhook notification: {e}")

        # Deliver to custom webhooks
        try:
            await self._send_webhooks(
                event,
                {
                    "title": title,
                    "description": description,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to send webhooks: {e}")

        # Deliver via email
        if settings.smtp_enabled:
            smtp_events = [e.strip() for e in settings.smtp_notify_events.split(",")]
            if event in smtp_events:
                try:
                    from app.services.email_service import email_service

                    await email_service.send_email(
                        subject=f"[{settings.app_name}] {title}",
                        body=description,
                    )
                except Exception as e:
                    logger.warning(f"Failed to send email notification: {e}")

        # Deliver via Telegram
        if settings.telegram_bot_token and settings.telegram_chat_id:
            telegram_events = [
                e.strip() for e in settings.telegram_notify_events.split(",")
            ]
            if event in telegram_events:
                try:
                    await self._send_telegram(title, description)
                except Exception as e:
                    logger.warning(f"Failed to send Telegram notification: {e}")

    async def _send_discord(
        self, title: str, description: str, color: int, fields: list[dict] | None = None
    ):
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if fields:
            embed["fields"] = fields
        payload = {"embeds": [embed]}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.discord_webhook_url, json=payload)
            resp.raise_for_status()

    async def _send_telegram(self, title: str, description: str):
        text = f"*{title}*\n{description}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
            )
            resp.raise_for_status()

    async def _send_webhooks(self, event: str, payload: dict):
        from app.database import async_session
        from app.models.webhook import Webhook
        from app.utils.security import validate_webhook_url

        async with async_session() as db:
            result = await db.execute(select(Webhook).where(Webhook.enabled.is_(True)))
            webhooks = result.scalars().all()

        for wh in webhooks:
            wh_events = [e.strip() for e in wh.events.split(",")]
            if event not in wh_events:
                continue
            ok, error = validate_webhook_url(wh.url)
            if not ok:
                logger.warning(f"Webhook delivery to {wh.name} blocked: {error}")
                continue
            try:
                body = json.dumps({"event": event, **payload})
                req_headers = {"Content-Type": "application/json"}
                if wh.headers:
                    try:
                        req_headers.update(json.loads(wh.headers))
                    except (json.JSONDecodeError, TypeError):
                        pass
                if wh.secret:
                    sig = hmac.new(
                        wh.secret.encode(), body.encode(), hashlib.sha256
                    ).hexdigest()
                    req_headers["X-Webhook-Signature"] = f"sha256={sig}"
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(wh.url, content=body, headers=req_headers)
            except Exception as e:
                logger.warning(f"Webhook delivery to {wh.name} failed: {e}")


notification_service = NotificationService()
