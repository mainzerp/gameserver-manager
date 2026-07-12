import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.site_settings import SiteSettings
from app.models.steam_account import _derive_key_legacy, _derive_key_v2

logger = logging.getLogger(__name__)


def _encryption_secret() -> str:
    return settings.encryption_key or settings.secret_key


def _fernet() -> Fernet:
    """Derive a Fernet key using the strong PBKDF2 method (v2)."""
    return Fernet(_derive_key_v2(_encryption_secret()))


def _fernet_legacy() -> Fernet:
    """Derive a Fernet key using the legacy SHA-256 method for backward compat."""
    return Fernet(_derive_key_legacy(_encryption_secret()))


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        pass
    try:
        return _fernet_legacy().decrypt(value.encode()).decode()
    except InvalidToken:
        pass
    logger.error(
        "Failed to decrypt settings value with both v2 and legacy keys; "
        "returning empty string"
    )
    return ""


async def _get_or_create(db: AsyncSession) -> SiteSettings:
    result = await db.execute(select(SiteSettings).where(SiteSettings.id == 1))
    row = result.scalars().first()
    if row is None:
        row = SiteSettings(id=1)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def load_from_db(db: AsyncSession) -> None:
    """Load DB settings and overlay them onto the global settings singleton."""
    row = await _get_or_create(db)

    # SMTP
    settings.smtp_enabled = row.smtp_enabled
    settings.smtp_host = row.smtp_host or ""
    settings.smtp_port = row.smtp_port
    settings.smtp_user = row.smtp_user or ""
    settings.smtp_password = (
        _decrypt(row.smtp_password_enc) if row.smtp_password_enc else ""
    )
    settings.smtp_use_tls = row.smtp_use_tls
    settings.smtp_from_address = row.smtp_from_address or ""
    settings.smtp_to_addresses = row.smtp_to_addresses or ""
    settings.smtp_notify_events = row.smtp_notify_events

    # Discord
    settings.discord_webhook_url = row.discord_webhook_url or ""
    settings.discord_notify_events = row.discord_notify_events

    # Telegram
    settings.telegram_bot_token = row.telegram_bot_token or ""
    settings.telegram_chat_id = row.telegram_chat_id or ""
    settings.telegram_notify_events = row.telegram_notify_events

    # Flags
    settings.multi_node_enabled = row.multi_node_enabled
    settings.webauthn_enabled = row.webauthn_enabled
    settings.webauthn_rp_id = row.webauthn_rp_id
    settings.webauthn_origin = row.webauthn_origin
    settings.totp_global_enabled = row.totp_global_enabled

    # Backup external storage
    settings.backup_external_path = row.backup_external_path or ""

    # Steam
    settings.steam_api_key = row.steam_api_key or ""

    logger.info("Site settings loaded from database")


async def save_to_db(db: AsyncSession, form_data: dict) -> None:
    """Persist form_data to the site_settings row and re-hydrate the singleton."""
    row = await _get_or_create(db)

    # SMTP
    row.smtp_enabled = form_data.get("smtp_enabled", False)
    row.smtp_host = form_data.get("smtp_host") or None
    try:
        row.smtp_port = int(form_data.get("smtp_port", 587))
    except (ValueError, TypeError):
        row.smtp_port = 587
    row.smtp_user = form_data.get("smtp_user") or None
    new_password = form_data.get("smtp_password", "").strip()
    if new_password:
        row.smtp_password_enc = _encrypt(new_password)
    # If empty, leave existing smtp_password_enc unchanged
    row.smtp_use_tls = form_data.get("smtp_use_tls", False)
    row.smtp_from_address = form_data.get("smtp_from_address") or None
    row.smtp_to_addresses = form_data.get("smtp_to_addresses") or None
    row.smtp_notify_events = form_data.get("smtp_notify_events", "crash,backup_failed")

    # TOTP
    row.totp_global_enabled = form_data.get("totp_global_enabled", False)

    # Multi-node
    row.multi_node_enabled = form_data.get("multi_node_enabled", False)

    # WebAuthn
    row.webauthn_enabled = form_data.get("webauthn_enabled", False)
    row.webauthn_rp_id = form_data.get("webauthn_rp_id") or "localhost"
    row.webauthn_origin = form_data.get("webauthn_origin") or "https://localhost:8443"

    # Discord
    row.discord_webhook_url = form_data.get("discord_webhook_url") or None
    row.discord_notify_events = form_data.get(
        "discord_notify_events", "start,stop,crash,backup"
    )

    # Telegram
    row.telegram_bot_token = form_data.get("telegram_bot_token") or None
    row.telegram_chat_id = form_data.get("telegram_chat_id") or None
    row.telegram_notify_events = form_data.get("telegram_notify_events", "crash")

    # Backup external storage
    row.backup_external_path = form_data.get("backup_external_path") or None

    # Steam
    row.steam_api_key = form_data.get("steam_api_key") or None

    await db.commit()
    await db.refresh(row)
    await load_from_db(db)
