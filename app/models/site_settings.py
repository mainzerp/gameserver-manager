from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SiteSettings(Base):
    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # SMTP
    smtp_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int] = mapped_column(
        Integer, server_default="587", nullable=False
    )
    smtp_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False
    )
    smtp_from_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_to_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_notify_events: Mapped[str] = mapped_column(
        String(500), server_default="crash,backup_failed", nullable=False
    )

    # TOTP
    totp_global_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )

    # Multi-node
    multi_node_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )

    # WebAuthn
    webauthn_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    webauthn_rp_id: Mapped[str] = mapped_column(
        String(255), server_default="localhost", nullable=False
    )
    webauthn_origin: Mapped[str] = mapped_column(
        String(500), server_default="https://localhost:8443", nullable=False
    )

    # Discord
    discord_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    discord_notify_events: Mapped[str] = mapped_column(
        String(500), server_default="start,stop,crash,backup", nullable=False
    )

    # Telegram
    telegram_bot_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_notify_events: Mapped[str] = mapped_column(
        String(500), server_default="crash", nullable=False
    )

    # Backup storage
    backup_external_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Steam
    steam_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
