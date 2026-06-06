import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.steam_account import decrypt_password, encrypt_password


class ServerType(str, enum.Enum):
    MINECRAFT_JAVA = "minecraft_java"
    MINECRAFT_BEDROCK = "minecraft_bedrock"
    STEAM = "steam"


class ServerStatus(str, enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    server_type: Mapped[ServerType] = mapped_column(
        Enum(ServerType), nullable=False, index=True
    )
    status: Mapped[ServerStatus] = mapped_column(
        Enum(ServerStatus), default=ServerStatus.STOPPED, index=True
    )
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    executable: Mapped[str] = mapped_column(String(500), nullable=False)
    start_command: Mapped[str] = mapped_column(Text, nullable=False)
    java_path: Mapped[str] = mapped_column(String(500), default="java")
    min_memory: Mapped[int] = mapped_column(Integer, default=1024)
    max_memory: Mapped[int] = mapped_column(Integer, default=2048)
    port: Mapped[int] = mapped_column(Integer, default=25565)
    auto_start: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_update_mods: Mapped[bool] = mapped_column(Boolean, default=True)
    steam_app_id: Mapped[str] = mapped_column(String(20), nullable=True)
    steam_build_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    steam_branch: Mapped[str | None] = mapped_column(
        String(100), nullable=True, server_default="public"
    )
    steam_login_anonymous: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1"
    )
    steam_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("steam_accounts.id", ondelete="SET NULL"), nullable=True
    )
    steam_gslt_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    steam_update_on_start: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    steam_last_update: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    mc_version: Mapped[str] = mapped_column(String(20), nullable=True)
    loader: Mapped[str] = mapped_column(
        String(50), nullable=True
    )  # forge, fabric, etc.
    loader_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    max_backups: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rcon_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    rcon_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rcon_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auto_update_server: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    last_server_update: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auto_restart_on_crash: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    max_crash_restarts: Mapped[int] = mapped_column(
        Integer, default=3, server_default="3"
    )
    crash_restart_delay: Mapped[int] = mapped_column(
        Integer, default=15, server_default="15"
    )
    crash_stability_window: Mapped[int] = mapped_column(
        Integer, default=600, server_default="600"
    )
    jvm_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    server_args: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_log_pattern: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latest_known_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    max_compatible_mc_version: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    max_compatible_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    environment_vars: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default="{}"
    )
    container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cpu_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    uptime_schedule: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    backup_exclude_patterns: Mapped[str | None] = mapped_column(Text, nullable=True)
    notification_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notifications_muted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    notification_events: Mapped[str | None] = mapped_column(String(500), nullable=True)
    saved_commands: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @property
    def steam_gslt(self) -> str | None:
        if not self.steam_gslt_encrypted:
            return None
        return decrypt_password(self.steam_gslt_encrypted)

    @steam_gslt.setter
    def steam_gslt(self, value: str | None) -> None:
        if value:
            self.steam_gslt_encrypted = encrypt_password(value.strip())
        else:
            self.steam_gslt_encrypted = None

    @property
    def has_steam_gslt(self) -> bool:
        return bool(self.steam_gslt_encrypted)
