import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app import __version__


class Settings(BaseSettings):
    app_name: str = "GameServer Manager"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8443
    # SQLite alternative: "sqlite+aiosqlite:///./data/gameserver.db"
    database_url: str = "postgresql+asyncpg://gsm:gsm@db:5432/gameserver"
    servers_dir: str = str(Path(__file__).resolve().parent.parent / "servers")
    steamcmd_path: str = ""
    steamcmd_auto_install: bool = True
    steamcmd_install_dir: str = str(
        Path(__file__).resolve().parent.parent / "data" / "steamcmd"
    )
    steam_api_key: str = ""
    modrinth_api_url: str = "https://api.modrinth.com/v2"
    mod_check_interval_minutes: int = 60
    secret_key: str = "change-me-in-production"
    encryption_key: str = ""
    ssl_enabled: bool = False
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    resource_cache_ttl: int = 3
    log_max_size_mb: int = 5
    log_max_files: int = 5
    backup_dir: str = str(Path(__file__).resolve().parent.parent / "data" / "backups")
    max_backups_per_server: int = 10
    discord_webhook_url: str = ""
    discord_notify_events: str = "start,stop,crash,backup"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_notify_events: str = "crash"
    metric_interval_seconds: int = 30
    metric_retention_days: int = 7
    public_status_enabled: bool = False
    update_check_enabled: bool = True
    update_repo: str = ""
    update_check_interval_hours: int = 24
    sftp_enabled: bool = False
    sftp_port: int = 2222
    sftp_host_key_path: str = str(
        Path(__file__).resolve().parent.parent / "data" / "sftp_host_key"
    )
    docker_isolation_enabled: bool = False
    docker_default_image: str = "eclipse-temurin:21-jre"
    docker_network_mode: str = "host"
    prometheus_enabled: bool = False
    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from_address: str = ""
    smtp_to_addresses: str = ""
    smtp_notify_events: str = "crash,backup_failed"
    webauthn_enabled: bool = False
    webauthn_rp_id: str = "localhost"
    webauthn_origin: str = "https://localhost:8443"
    multi_node_enabled: bool = False
    totp_global_enabled: bool = False
    backup_external_path: str = ""
    version: str = __version__
    # Docker Compose env vars (documented to satisfy extra="forbid")
    postgres_db: str = ""
    postgres_user: str = ""
    postgres_password: str = ""
    host_port: int = 8443

    model_config = SettingsConfigDict(env_file=".env", env_prefix="GSM_", extra="forbid")


settings = Settings()

# Ensure directories exist
os.makedirs(settings.servers_dir, exist_ok=True)
os.makedirs("data", exist_ok=True)
os.makedirs(settings.backup_dir, exist_ok=True)
