"""Export/import server configurations as JSON."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server, ServerStatus, ServerType

logger = logging.getLogger(__name__)

EXPORT_FIELDS = [
    "name",
    "server_type",
    "executable",
    "start_command",
    "java_path",
    "min_memory",
    "max_memory",
    "port",
    "auto_start",
    "auto_update_mods",
    "mc_version",
    "loader",
    "loader_version",
    "max_backups",
    "rcon_enabled",
    "rcon_port",
    "auto_update_server",
    "jvm_flags",
    "server_args",
    "ready_log_pattern",
    "uptime_schedule",
    "tags",
    "backup_exclude_patterns",
    "environment_vars",
]


async def export_config(db: AsyncSession, server_id: int) -> dict:
    server = await db.get(Server, server_id)
    if not server:
        raise ValueError("Server not found")
    data = {}
    for field in EXPORT_FIELDS:
        val = getattr(server, field, None)
        if val is not None:
            if hasattr(val, "value"):
                data[field] = val.value
            else:
                data[field] = val
    data["_export_version"] = 1
    return data


async def import_config(
    db: AsyncSession, config_data: dict, server_path: str
) -> Server:
    st = ServerType(config_data.get("server_type", "minecraft_java"))
    server = Server(
        name=config_data.get("name", "Imported Server"),
        server_type=st,
        status=ServerStatus.STOPPED,
        path=server_path,
        executable=config_data.get("executable", "server.jar"),
        start_command=config_data.get("start_command", ""),
        java_path=config_data.get("java_path", "java"),
        min_memory=config_data.get("min_memory", 1024),
        max_memory=config_data.get("max_memory", 2048),
        port=config_data.get("port", 25565),
    )
    for field in EXPORT_FIELDS:
        if field in config_data and hasattr(server, field):
            val = config_data[field]
            if field == "server_type":
                val = ServerType(val)
            setattr(server, field, val)
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return server
