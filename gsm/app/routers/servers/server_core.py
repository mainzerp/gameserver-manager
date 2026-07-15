import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_log import AuditLog
from app.models.backup import Backup
from app.models.mod import Mod
from app.models.node import Node
from app.models.scheduled_task import ScheduledTask, TaskType
from app.models.server import Server, ServerStatus, ServerType
from app.models.steam_account import SteamAccount
from app.models.workshop_item import WorkshopItem
from app.routers.servers._shared import get_current_user_dep, get_db, require_role, require_server_access
from app.services.auth import (
    get_accessible_server_ids,
    get_current_user,
)
from app.services.config_editor import (
    get_field_schema,
    parse_properties,
)
from app.services.java_manager import (
    detect_java_version,
    find_java_for_mc,
    get_required_java_version,
    list_managed_javas,
)
from app.services.mod_updater import mod_updater
from app.services.player_manager import player_manager
from app.services.port_manager import port_manager
from app.services.resource_monitor import resource_monitor
from app.services.server_manager import server_manager
from app.services.server_templates import get_templates
from app.services.steamcmd import steamcmd
from app.template_utils import templates

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_dep)])

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    accessible_ids = await get_accessible_server_ids(user, db)
    query = select(Server)
    if accessible_ids is not None:
        query = query.where(Server.id.in_(accessible_ids))
    result = await db.execute(query)
    servers = result.scalars().all()

    dirty = False
    for s in servers:
        if server_manager.is_running(s.id, db=db) and s.status not in (
            ServerStatus.RUNNING,
            ServerStatus.STARTING,
        ):
            s.status = ServerStatus.RUNNING
            dirty = True
        elif not server_manager.is_running(s.id, db=db) and s.status in (
            ServerStatus.RUNNING,
            ServerStatus.STARTING,
        ):
            s.status = ServerStatus.STOPPED
            dirty = True
    if dirty:
        await db.commit()

    # Compute status counts for the summary bar
    status_counts = {"total": len(servers), "running": 0, "stopped": 0, "crashed": 0}
    type_counts = {"minecraft_java": 0, "minecraft_bedrock": 0, "steam": 0}
    for s in servers:
        if s.status == ServerStatus.RUNNING:
            status_counts["running"] += 1
        elif s.status == ServerStatus.CRASHED:
            status_counts["crashed"] += 1
        else:
            status_counts["stopped"] += 1
        if s.server_type.value in type_counts:
            type_counts[s.server_type.value] += 1

    # Mod update counts per server
    update_result = await db.execute(
        select(Mod.server_id, func.count(Mod.id))
        .where(Mod.update_available.is_(True))
        .group_by(Mod.server_id)
    )
    update_counts = dict(update_result.all())
    total_mod_updates = sum(update_counts.values())

    # Scheduled tasks count
    task_result = await db.execute(
        select(func.count(ScheduledTask.id))
    )
    scheduled_tasks_count = task_result.scalar() or 0

    return templates.TemplateResponse(request, "dashboard.html", {
            "servers": servers,
            "status_counts": status_counts,
            "type_counts": type_counts,
            "update_counts": update_counts,
            "total_mod_updates": total_mod_updates,
            "scheduled_tasks_count": scheduled_tasks_count,
            "nodes": (await db.execute(select(Node))).scalars().all()
            if settings.multi_node_enabled
            else [],
            "multi_node_enabled": settings.multi_node_enabled,
            "current_user": user,
        })

@router.get("/servers", response_class=HTMLResponse)
async def server_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    accessible_ids = await get_accessible_server_ids(user, db)
    query = select(Server)
    if accessible_ids is not None:
        query = query.where(Server.id.in_(accessible_ids))
    result = await db.execute(query)
    servers = result.scalars().all()

    dirty = False
    for s in servers:
        if server_manager.is_running(s.id, db=db) and s.status not in (
            ServerStatus.RUNNING,
            ServerStatus.STARTING,
        ):
            s.status = ServerStatus.RUNNING
            dirty = True
        elif not server_manager.is_running(s.id, db=db) and s.status in (
            ServerStatus.RUNNING,
            ServerStatus.STARTING,
        ):
            s.status = ServerStatus.STOPPED
            dirty = True
    if dirty:
        await db.commit()

    status_counts = {"total": len(servers), "running": 0, "stopped": 0, "crashed": 0}
    for s in servers:
        if s.status == ServerStatus.RUNNING:
            status_counts["running"] += 1
        elif s.status == ServerStatus.CRASHED:
            status_counts["crashed"] += 1
        else:
            status_counts["stopped"] += 1

    update_result = await db.execute(
        select(Mod.server_id, func.count(Mod.id))
        .where(Mod.update_available.is_(True))
        .group_by(Mod.server_id)
    )
    update_counts = dict(update_result.all())

    monitoring_status = {
        s.id: server_manager.monitoring_status(s.id) for s in servers
    }

    return templates.TemplateResponse(request, "servers.html", {
            "servers": servers,
            "status_counts": status_counts,
            "update_counts": update_counts,
            "monitoring_status": monitoring_status,
            "current_user": user,
        })

@router.get("/servers/create", response_class=HTMLResponse)
async def create_server_form(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")
    suggested = await port_manager.suggest_ports(db, "minecraft_java")
    nodes = (
        (await db.execute(select(Node))).scalars().all()
        if settings.multi_node_enabled
        else []
    )
    steam_accounts = (await db.execute(select(SteamAccount))).scalars().all()
    return templates.TemplateResponse(request, "server_create.html", {
            "server_types": [t.value for t in ServerType],
            "steam_apps": steamcmd.get_known_apps(),
            "steamcmd_available": steamcmd.is_available,
            "errors": [],
            "form_values": {},
            "suggested_game_port": suggested["game_port"],
            "suggested_rcon_port": suggested["rcon_port"],
            "suggested_query_port": suggested["query_port"],
            "presets": get_templates(),
            "nodes": nodes,
            "steam_accounts": steam_accounts,
        })

@router.get("/servers/{server_id}", response_class=HTMLResponse)
async def server_detail(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    result = await db.execute(
        select(Mod).where(Mod.server_id == server_id).order_by(Mod.name.asc())
    )
    mods = result.scalars().all()

    conflicts = await mod_updater.check_conflicts(server_id, db)

    backup_result = await db.execute(
        select(Backup)
        .where(Backup.server_id == server_id)
        .order_by(Backup.created_at.desc())
    )
    backups = backup_result.scalars().all()

    task_result = await db.execute(
        select(ScheduledTask)
        .where(ScheduledTask.server_id == server_id)
        .order_by(ScheduledTask.created_at.desc())
    )
    scheduled_tasks = task_result.scalars().all()

    logs = server_manager.get_logs(server_id, db=db)

    # Compute uptime
    uptime_seconds = None
    if server.started_at and server.status == ServerStatus.RUNNING:
        uptime_seconds = int(
            (datetime.now(timezone.utc) - server.started_at).total_seconds()
        )

    # Recent audit entries
    audit_result = await db.execute(
        select(AuditLog)
        .where(AuditLog.resource_id == str(server_id))
        .order_by(AuditLog.timestamp.desc())
        .limit(5)
    )
    recent_events = audit_result.scalars().all()

    # Parse backup exclude patterns for display
    backup_exclude_display = ""
    if server.backup_exclude_patterns:
        try:
            patterns = json.loads(server.backup_exclude_patterns)
            backup_exclude_display = (
                "\n".join(patterns) if isinstance(patterns, list) else ""
            )
        except (ValueError, TypeError):
            pass

    # Java compatibility info
    java_compatible = True
    required_java_version = None
    managed_javas = []
    if server.mc_version and server.server_type == ServerType.MINECRAFT_JAVA:
        required_java_version = get_required_java_version(server.mc_version)

        managed_javas = list_managed_javas()
        detected = await detect_java_version(server.java_path)
        if detected is not None:
            java_compatible = detected >= required_java_version
        elif server.java_path == "java":
            java_info = await find_java_for_mc(server.mc_version)
            java_compatible = java_info.get("compatible", False)
        else:
            java_compatible = False

    # Mod update counts
    update_result = await db.execute(
        select(Mod).where(Mod.server_id == server_id, Mod.update_available.is_(True))
    )
    mod_updates_count = len(update_result.scalars().all())

    # Config editor + player management data (Minecraft only)
    config_props = {}
    config_schema = {}
    config_raw_content = ""
    whitelist = []
    banned = []
    if server.server_type in (ServerType.MINECRAFT_JAVA, ServerType.MINECRAFT_BEDROCK):
        props_path = Path(server.path) / "server.properties"
        if props_path.exists():
            config_raw_content = props_path.read_text(
                encoding="utf-8", errors="replace"
            )
            config_props = parse_properties(config_raw_content)
        config_schema = get_field_schema(server.server_type.value)
        whitelist = player_manager.get_whitelist(server.path)
        banned = player_manager.get_banned_players(server.path)

    # Workshop items for Steam servers
    workshop_items = []
    steam_operation_snapshot = None
    steam_has_active_update_start = False
    steam_operation_active = False
    if server.server_type == ServerType.STEAM:
        workshop_result = await db.execute(
            select(WorkshopItem).where(WorkshopItem.server_id == server_id)
        )
        workshop_items = workshop_result.scalars().all()
        steam_operation_snapshot = steamcmd.get_operation_snapshot(server_id)
        steam_snapshot_status = steam_operation_snapshot.get("status", "idle")
        steam_has_active_update_start = steam_operation_snapshot.get(
            "operation"
        ) == "update_start" and steam_snapshot_status in {
            "queued",
            "running",
            "waiting_for_steam_guard",
        }
        steam_operation_active = (
            steam_operation_snapshot.get("operation") is not None
            and steam_snapshot_status in {"queued", "running", "waiting_for_steam_guard"}
        )

    steam_accounts = (await db.execute(select(SteamAccount))).scalars().all()

    monitoring_status = server_manager.monitoring_status(server_id)

    return templates.TemplateResponse(request, "server_detail.html", {
            "server": server,
            "mods": mods,
            "conflicts": conflicts,
            "backups": backups,
            "scheduled_tasks": scheduled_tasks,
            "task_types": [t.value for t in TaskType],
            "logs": logs,
            "is_running": server_manager.is_running(server_id, db=db),
            "node": await db.get(Node, server.node_id) if server.node_id else None,
            "uptime_seconds": uptime_seconds,
            "recent_events": recent_events,
            "backup_exclude_display": backup_exclude_display,
            "java_compatible": java_compatible,
            "required_java_version": required_java_version,
            "managed_javas": managed_javas,
            "mod_updates_count": mod_updates_count,
            "config_props": config_props,
            "config_schema": config_schema,
            "config_raw_content": config_raw_content,
            "whitelist": whitelist,
            "banned": banned,
            "steam_apps": steamcmd.get_known_apps(),
            "steam_accounts": steam_accounts,
            "steam_operation_snapshot": steam_operation_snapshot,
            "steam_has_active_update_start": steam_has_active_update_start,
            "steam_operation_active": steam_operation_active,
            "workshop_items": workshop_items,
            "monitoring_status": monitoring_status,
        })

@router.get("/system/stats")
async def system_stats():
    return JSONResponse(resource_monitor.get_system_stats())

