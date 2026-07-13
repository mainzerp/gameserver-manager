import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models.backup import Backup
from app.models.metric import MetricSnapshot
from app.models.mod import Mod
from app.models.node import Node
from app.models.scheduled_task import ScheduledTask, TaskType
from app.models.server import Server, ServerStatus, ServerType
from app.routers.files import _extract_archive
from app.services.audit_service import audit_service, get_audit_context
from app.services.auth import (
    get_accessible_server_ids,
    get_current_user,
    get_current_user_dep,
    require_role,
    require_server_access,
)
from app.services.config_editor import (
    generate_default_properties,
    get_field_schema,
    parse_properties,
)
from app.services.jar_downloader import download_server_jar
from app.services.java_manager import (
    find_java_for_mc,
    get_required_java_version,
)
from app.services.log_manager import log_manager
from app.services.mod_updater import mod_updater
from app.services.player_manager import player_manager
from app.services.port_manager import port_manager
from app.services.query_protocol import minecraft_query, steam_query
from app.services.rcon_client import RCONClient
from app.services.resource_monitor import resource_monitor
from app.services.server_detector import detect_server_info
from app.services.server_manager import server_manager
from app.services.server_templates import get_templates
from app.services.server_updater import server_updater
from app.services.steamcmd import generate_start_command, steamcmd
from app.services.world_manager import world_manager
from app.template_utils import templates
from app.validation import (
    validate_command_length,
    validate_mc_version,
    validate_memory,
    validate_port,
    validate_server_name,
    validate_server_type,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_dep)])


def _spawn_background_task(coro):
    asyncio.create_task(coro)


async def _refresh_workshop_item_metadata(item, db: AsyncSession) -> None:
    from app.services.steam_workshop import steam_workshop_service

    metadata = await steam_workshop_service.fetch_metadata(item.workshop_id)
    if not metadata:
        return
    if metadata.get("name"):
        item.name = metadata["name"]
    if metadata.get("description"):
        item.description = metadata["description"]
    if metadata.get("file_size") is not None:
        item.file_size = metadata["file_size"]
    if metadata.get("last_updated"):
        item.last_updated = metadata["last_updated"]
    await db.flush()


async def _run_create_steam_install(
    server_id: int, operation_id: str | None = None
) -> None:
    async with async_session() as db:
        server = await db.get(Server, server_id)
        if (
            not server
            or server.server_type != ServerType.STEAM
            or not server.steam_app_id
        ):
            return

        steam_kwargs, steam_error = await steamcmd.get_server_install_kwargs(
            db, server, interactive=True
        )
        if steam_error:
            server.status = ServerStatus.CRASHED
            await db.commit()
            await steamcmd.record_operation_failure(server.id, "install", steam_error)
            return

        result = await steamcmd.install_server(
            app_id=server.steam_app_id,
            install_dir=server.path,
            validate=True,
            operation_type="install",
            operation_id=operation_id,
            **steam_kwargs,
        )
        if result.get("ok"):
            server.status = ServerStatus.STOPPED
            server.steam_build_id = result.get("build_id")
            server.steam_last_update = datetime.now(timezone.utc)
            app_info = steamcmd.get_app_info(server.steam_app_id)
            if app_info:
                server.executable = app_info.get("executable", server.executable)
                query_port = server.query_port or (server.port + 1)
                start_args = app_info.get("start_args", "").format(
                    port=server.port,
                    name=server.name,
                    query_port=query_port,
                )
                server.start_command = (
                    f"./{app_info['executable']} {start_args}".strip()
                )
        else:
            server.status = ServerStatus.CRASHED
        await db.commit()


async def _run_manual_steam_validate(
    server_id: int, operation_id: str | None = None
) -> None:
    async with async_session() as db:
        server = await db.get(Server, server_id)
        if (
            not server
            or server.server_type != ServerType.STEAM
            or not server.steam_app_id
        ):
            return

        steam_kwargs, steam_error = await steamcmd.get_server_install_kwargs(
            db, server, interactive=True
        )
        if steam_error:
            await steamcmd.record_operation_failure(server_id, "validate", steam_error)
            return

        result = await steamcmd.validate_server(
            app_id=server.steam_app_id,
            install_dir=server.path,
            operation_type="validate",
            operation_id=operation_id,
            **steam_kwargs,
        )
        if result.get("ok") and result.get("build_id"):
            server.steam_build_id = result["build_id"]
            server.steam_last_update = datetime.now(timezone.utc)
            await db.commit()


async def _run_background_steam_update(
    server_id: int, operation_id: str | None = None
) -> None:
    async with async_session() as db:
        await server_updater.update_server(
            server_id, db, interactive=True, operation_id=operation_id
        )


async def _run_background_steam_update_then_start(
    server_id: int, operation_id: str
) -> None:
    async with async_session() as db:
        server = await db.get(Server, server_id)
        if (
            not server
            or server.server_type != ServerType.STEAM
            or not server.steam_app_id
        ):
            return

        steam_kwargs, steam_error = await steamcmd.get_server_install_kwargs(
            db, server, interactive=False
        )
        if steam_error:
            await steamcmd._publish_event(
                server_id=server_id,
                event_type="failed",
                operation_id=operation_id,
                operation_type="update_start",
                message=steam_error,
                percent=0.0,
                status="failed",
            )
            return

        result = await steamcmd.update_server(
            app_id=server.steam_app_id,
            install_dir=server.path,
            operation_type="update_start",
            operation_id=operation_id,
            **steam_kwargs,
        )
        if not result.get("ok"):
            return

        if result.get("build_id"):
            server.steam_build_id = result["build_id"]
            server.steam_last_update = datetime.now(timezone.utc)
            await db.commit()

        await steamcmd._publish_event(
            server_id=server_id,
            event_type="progress",
            operation_id=operation_id,
            operation_type="update_start",
            message="Steam update completed. Starting server...",
            percent=100.0,
            build_id=result.get("build_id"),
            status="running",
        )

    server_manager._reset_crash_state(server_id)
    start_result = await server_manager.start_server(server_id, skip_steam_update=True)
    if not start_result.get("ok"):
        await steamcmd._publish_event(
            server_id=server_id,
            event_type="failed",
            operation_id=operation_id,
            operation_type="update_start",
            message=start_result.get("error")
            or "Steam update completed, but the server failed to start.",
            percent=100.0,
            status="failed",
        )
        return

    await steamcmd._publish_event(
        server_id=server_id,
        event_type="completed",
        operation_id=operation_id,
        operation_type="update_start",
        message="Steam update completed. Server start requested.",
        percent=100.0,
        status="completed",
    )


async def _run_workshop_install(
    server_id: int, item_id: int, operation_type: str, operation_id: str | None = None
) -> None:
    async with async_session() as db:
        server = await db.get(Server, server_id)
        if (
            not server
            or server.server_type != ServerType.STEAM
            or not server.steam_app_id
        ):
            return

        from app.models.workshop_item import WorkshopItem

        item = await db.get(WorkshopItem, item_id)
        if not item or item.server_id != server_id:
            return

        steam_kwargs, steam_error = await steamcmd.get_server_install_kwargs(
            db, server, interactive=True
        )
        if steam_error:
            await steamcmd.record_operation_failure(
                server_id,
                operation_type,
                steam_error,
                workshop_item_id=item.workshop_id,
            )
            return

        result = await steamcmd.install_workshop_item(
            app_id=item.app_id,
            workshop_id=item.workshop_id,
            install_dir=server.path,
            login_anonymous=steam_kwargs.get("login_anonymous", True),
            username=steam_kwargs.get("username"),
            password=steam_kwargs.get("password"),
            server_id=server_id,
            operation_type=operation_type,
            operation_id=operation_id,
            interactive=True,
        )
        if not result.get("ok"):
            await db.commit()
            return

        item.installed = True
        item.last_updated = datetime.now(timezone.utc)
        await _refresh_workshop_item_metadata(item, db)
        await db.commit()


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
        if server_manager.is_running(s.id) and s.status not in (
            ServerStatus.RUNNING,
            ServerStatus.STARTING,
        ):
            s.status = ServerStatus.RUNNING
            dirty = True
        elif not server_manager.is_running(s.id) and s.status in (
            ServerStatus.RUNNING,
            ServerStatus.STARTING,
        ):
            s.status = ServerStatus.STOPPED
            dirty = True
    if dirty:
        await db.commit()

    # Compute status counts for the summary bar
    status_counts = {"total": len(servers), "running": 0, "stopped": 0, "crashed": 0}
    for s in servers:
        if s.status == ServerStatus.RUNNING:
            status_counts["running"] += 1
        elif s.status == ServerStatus.CRASHED:
            status_counts["crashed"] += 1
        else:
            status_counts["stopped"] += 1

    # Mod update counts per server
    from sqlalchemy import func

    update_result = await db.execute(
        select(Mod.server_id, func.count(Mod.id))
        .where(Mod.update_available.is_(True))
        .group_by(Mod.server_id)
    )
    update_counts = dict(update_result.all())

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "servers": servers,
            "status_counts": status_counts,
            "update_counts": update_counts,
            "steamcmd_available": steamcmd.is_available,
            "nodes": (await db.execute(select(Node))).scalars().all()
            if settings.multi_node_enabled
            else [],
            "multi_node_enabled": settings.multi_node_enabled,
            "current_user": user,
        },
    )


@router.get("/servers/create", response_class=HTMLResponse)
async def create_server_form(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")
    suggested = await port_manager.suggest_ports(db, "minecraft_java")
    nodes = (
        (await db.execute(select(Node))).scalars().all()
        if settings.multi_node_enabled
        else []
    )
    from app.models.steam_account import SteamAccount

    steam_accounts = (await db.execute(select(SteamAccount))).scalars().all()
    return templates.TemplateResponse(
        "server_create.html",
        {
            "request": request,
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
        },
    )


@router.post("/servers/create")
async def create_server(
    request: Request,
    name: str = Form(...),
    server_type: str = Form(...),
    port: int = Form(25565),
    min_memory: int = Form(1024),
    max_memory: int = Form(2048),
    java_path: str = Form("java"),
    mc_version: str = Form(""),
    loader: str = Form(""),
    loader_version: str = Form(""),
    steam_app_id: str = Form(""),
    steam_branch: str = Form("public"),
    steam_login_anonymous: bool = Form(True),
    steam_account_id: str = Form(""),
    steam_update_on_start: bool = Form(False),
    auto_start: bool = Form(False),
    rcon_enabled: bool = Form(False),
    rcon_port: int = Form(25575),
    rcon_password: str = Form(""),
    node_id: int = Form(None),
    query_port: int = Form(0),
    # Server Properties (Minecraft)
    motd: str = Form("A Minecraft Server"),
    level_seed: str = Form(""),
    white_list: bool = Form(False),
    difficulty: str = Form("easy"),
    gamemode: str = Form("survival"),
    max_players: int = Form(20),
    pvp: bool = Form(True),
    online_mode: bool = Form(True),
    spawn_protection: int = Form(16),
    view_distance: int = Form(10),
    allow_nether: bool = Form(True),
    level_type: str = Form("minecraft\\:normal"),
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    steam_account_id_value = int(steam_account_id) if steam_account_id else None
    form_values = {
        "name": name,
        "server_type": server_type,
        "port": port,
        "min_memory": min_memory,
        "max_memory": max_memory,
        "java_path": java_path,
        "mc_version": mc_version,
        "loader": loader,
        "loader_version": loader_version,
        "steam_app_id": steam_app_id,
        "steam_branch": steam_branch,
        "steam_login_anonymous": steam_login_anonymous,
        "steam_account_id": steam_account_id,
        "steam_update_on_start": steam_update_on_start,
        "auto_start": auto_start,
        "rcon_enabled": rcon_enabled,
        "rcon_port": rcon_port,
        "rcon_password": rcon_password,
        "query_port": query_port,
        "motd": motd,
        "level_seed": level_seed,
        "white_list": white_list,
        "difficulty": difficulty,
        "gamemode": gamemode,
        "max_players": max_players,
        "pvp": pvp,
        "online_mode": online_mode,
        "spawn_protection": spawn_protection,
        "view_distance": view_distance,
        "allow_nether": allow_nether,
        "level_type": level_type,
    }

    errors = []
    err = validate_server_name(name)
    if err:
        errors.append(err)
    err = validate_server_type(server_type)
    if err:
        errors.append(err)
    err = validate_port(port)
    if err:
        errors.append(err)
    err = validate_memory(min_memory, max_memory)
    if err:
        errors.append(err)
    err = validate_mc_version(mc_version)
    if err:
        errors.append(err)
    if (
        not err
        and server_type == ServerType.STEAM.value
        and steam_app_id
        and not steam_login_anonymous
        and not steam_account_id_value
    ):
        errors.append(
            "Select a Steam account or enable anonymous login for Steam servers."
        )

    if not errors:
        effective_query_port = (query_port or (port + 1)) if server_type == ServerType.STEAM.value else None
        port_conflicts = await port_manager.check_conflicts(
            db, port, rcon_port if rcon_enabled else None, effective_query_port
        )
        errors.extend(port_conflicts)

    if not errors:
        existing = await db.execute(select(Server).where(Server.name == name))
        if existing.scalars().first():
            errors.append(f"A server named '{name}' already exists.")

    if not errors and settings.multi_node_enabled and node_id is not None:
        node = await db.get(Node, node_id)
        if not node:
            errors.append("Selected node does not exist.")

    server_path = os.path.join(settings.servers_dir, uuid.uuid4().hex[:12])

    if not errors and os.path.exists(server_path):
        errors.append(f"Server directory already exists: {server_path}")

    if errors:
        nodes = (
            (await db.execute(select(Node))).scalars().all()
            if settings.multi_node_enabled
            else []
        )
        suggested = await port_manager.suggest_ports(db, server_type)
        from app.models.steam_account import SteamAccount

        steam_accounts_err = (await db.execute(select(SteamAccount))).scalars().all()
        return templates.TemplateResponse(
            "server_create.html",
            {
                "request": request,
                "server_types": [t.value for t in ServerType],
                "steam_apps": steamcmd.get_known_apps(),
                "steamcmd_available": steamcmd.is_available,
                "errors": errors,
                "form_values": form_values,
                "suggested_game_port": suggested["game_port"],
                "suggested_rcon_port": suggested["rcon_port"],
                "suggested_query_port": suggested["query_port"],
                "presets": get_templates(),
                "nodes": nodes,
                "steam_accounts": steam_accounts_err,
            },
        )

    name = name.strip()
    os.makedirs(server_path, exist_ok=True)

    st = ServerType(server_type)
    effective_query_port = query_port if query_port else (port + 1)

    # Auto-detect the correct Java version for this MC version
    if st == ServerType.MINECRAFT_JAVA and mc_version:
        java_info = await find_java_for_mc(mc_version)
        java_path = java_info["java_path"]

    if st == ServerType.MINECRAFT_JAVA:
        executable = "server.jar"
        start_command = (
            f"{java_path} -Xms{min_memory}M -Xmx{max_memory}M -jar {executable} nogui"
        )
    elif st == ServerType.MINECRAFT_BEDROCK:
        executable = "bedrock_server.exe" if os.name == "nt" else "bedrock_server"
        start_command = os.path.join(server_path, executable)
    else:
        app_info = steamcmd.get_app_info(steam_app_id) if steam_app_id else None
        if app_info:
            executable = app_info.get("executable", "")
            start_args = app_info.get("start_args", "").format(
                port=port, name=name, query_port=effective_query_port
            )
            start_command = f"./{executable} {start_args}".strip()
        else:
            executable = ""
            start_command = ""

    server = Server(
        name=name,
        server_type=st,
        status=ServerStatus.STOPPED,
        path=server_path,
        executable=executable,
        start_command=start_command,
        java_path=java_path,
        min_memory=min_memory,
        max_memory=max_memory,
        port=port,
        query_port=effective_query_port if st == ServerType.STEAM else None,
        auto_start=auto_start,
        mc_version=mc_version or None,
        loader=loader or None,
        loader_version=loader_version or None,
        steam_app_id=steam_app_id or None,
        steam_branch=steam_branch or "public",
        steam_login_anonymous=steam_login_anonymous,
        steam_account_id=steam_account_id_value if not steam_login_anonymous else None,
        steam_update_on_start=steam_update_on_start,
        rcon_enabled=rcon_enabled,
        rcon_port=rcon_port if rcon_enabled else None,
        rcon_password=rcon_password if rcon_enabled else None,
        node_id=node_id if settings.multi_node_enabled else None,
    )
    db.add(server)
    await db.commit()

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.create",
            resource_type="server",
            resource_id=str(server.id),
            details=f"name={name}, type={server_type}",
        )
    )

    # Minecraft Java: download server.jar and accept EULA
    if st == ServerType.MINECRAFT_JAVA and mc_version:
        jar_ok = await download_server_jar(
            mc_version=mc_version,
            loader=loader or None,
            dest_dir=Path(server_path),
            loader_version=loader_version or None,
        )
        if not jar_ok:
            server.status = ServerStatus.CRASHED
            await db.commit()

        eula_path = os.path.join(server_path, "eula.txt")
        if not os.path.exists(eula_path):
            with open(eula_path, "w") as f:
                f.write("eula=true\n")

        # Write server.properties with all defaults + user overrides
        props_path = os.path.join(server_path, "server.properties")
        if not os.path.exists(props_path):
            overrides = {
                "server-port": str(port),
                "motd": motd,
                "max-players": str(max_players),
                "difficulty": difficulty,
                "gamemode": gamemode,
                "pvp": "true" if pvp else "false",
                "online-mode": "true" if online_mode else "false",
                "white-list": "true" if white_list else "false",
                "spawn-protection": str(spawn_protection),
                "view-distance": str(view_distance),
                "allow-nether": "true" if allow_nether else "false",
                "level-type": level_type,
            }
            if level_seed:
                overrides["level-seed"] = level_seed
            if rcon_enabled and rcon_password:
                overrides["enable-rcon"] = "true"
                overrides["rcon.port"] = str(rcon_port)
                overrides["rcon.password"] = rcon_password
            with open(props_path, "w") as f:
                f.write(generate_default_properties(overrides))

    # Minecraft Bedrock: download BDS
    if st == ServerType.MINECRAFT_BEDROCK:
        from app.services.jar_downloader import download_bedrock_server

        bds_ok = await download_bedrock_server(mc_version or None, Path(server_path))
        if not bds_ok:
            server.status = ServerStatus.CRASHED
            await db.commit()

    # Steam: install server via SteamCMD
    if st == ServerType.STEAM and steam_app_id:
        operation_id = await steamcmd.queue_operation(
            server.id,
            "install",
            f"Queued Steam install for {server.name}.",
        )
        _spawn_background_task(_run_create_steam_install(server.id, operation_id))

    return RedirectResponse(url=f"/servers/{server.id}", status_code=303)


@router.get("/servers/import", response_class=HTMLResponse)
async def import_server_form(request: Request):
    return templates.TemplateResponse(
        "server_import.html",
        {
            "request": request,
            "errors": [],
            "form_values": {},
        },
    )


@router.post("/servers/detect", response_class=JSONResponse)
async def detect_server(request: Request, path: str = Form(...)):
    await require_role(request, "admin")
    p = Path(path).resolve()
    allowed_base = Path(settings.servers_dir).resolve()
    if not str(p).startswith(str(allowed_base)):
        return JSONResponse(
            {"error": "Path must be within the servers directory"}, status_code=400
        )
    if not p.exists() or not p.is_dir():
        return JSONResponse(
            {"error": "Path does not exist or is not a directory"}, status_code=400
        )
    info = detect_server_info(path)
    return JSONResponse(info)


@router.post("/servers/import")
async def import_server(
    request: Request,
    path: str = Form(...),
    name: str = Form(...),
    server_type: str = Form("minecraft_java"),
    port: int = Form(25565),
    min_memory: int = Form(1024),
    max_memory: int = Form(2048),
    java_path: str = Form("java"),
    loader: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    form_values = {
        "path": path,
        "name": name,
        "server_type": server_type,
        "port": port,
        "min_memory": min_memory,
        "max_memory": max_memory,
        "java_path": java_path,
        "loader": loader,
    }
    await require_role(request, "admin")
    errors = []

    # Restrict import to configured servers directory
    try:
        resolved_import = Path(path).resolve()
        safe_servers_dir = Path(settings.servers_dir).resolve()
        if not str(resolved_import).startswith(str(safe_servers_dir)):
            errors.append(
                "Import path must be within the configured servers directory."
            )
    except Exception:
        errors.append("Invalid path.")

    p = Path(path)
    if not p.exists() or not p.is_dir():
        errors.append("Path does not exist or is not a directory.")

    err = validate_server_name(name)
    if err:
        errors.append(err)

    if not errors:
        existing = await db.execute(select(Server).where(Server.name == name))
        if existing.scalars().first():
            errors.append(f"A server named '{name}' already exists.")

    if not errors:
        resolved = str(p.resolve())
        existing_path = await db.execute(select(Server).where(Server.path == resolved))
        if existing_path.scalars().first():
            errors.append("A server already manages this directory.")

    if errors:
        return templates.TemplateResponse(
            "server_import.html",
            {
                "request": request,
                "errors": errors,
                "form_values": form_values,
            },
        )

    resolved = str(p.resolve())
    st = ServerType(server_type)
    executable = "server.jar"
    info = detect_server_info(path)
    if info.get("executable"):
        executable = info["executable"]

    if st == ServerType.MINECRAFT_JAVA:
        start_command = (
            f"{java_path} -Xms{min_memory}M -Xmx{max_memory}M -jar {executable} nogui"
        )
    elif st == ServerType.MINECRAFT_BEDROCK:
        executable = "bedrock_server.exe" if os.name == "nt" else "bedrock_server"
        start_command = os.path.join(resolved, executable)
    else:
        imported_app_id = info.get("steam_app_id") if info else None
        app_info = (
            steamcmd.get_app_info(imported_app_id) if imported_app_id else None
        )
        if app_info:
            executable = app_info.get("executable", "")
            start_args = app_info.get("start_args", "").format(
                port=port, name=name.strip(), query_port=port + 1
            )
            start_command = f"./{executable} {start_args}".strip()
        else:
            start_command = ""

    server = Server(
        name=name.strip(),
        server_type=st,
        status=ServerStatus.STOPPED,
        path=resolved,
        executable=executable,
        start_command=start_command,
        java_path=java_path,
        min_memory=min_memory,
        max_memory=max_memory,
        port=port,
        query_port=port + 1 if st == ServerType.STEAM else None,
        loader=loader or None,
    )
    db.add(server)
    await db.commit()
    return RedirectResponse(url=f"/servers/{server.id}", status_code=303)


MAX_ZIP_UPLOAD_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB compressed
ZIP_MAGIC = b"PK\x03\x04"  # Local file header signature


@router.get("/servers/upload-zip", response_class=HTMLResponse)
async def upload_zip_form(request: Request):
    await require_role(request, "admin")
    return templates.TemplateResponse(
        "server_upload_zip.html",
        {
            "request": request,
            "errors": [],
            "form_values": {},
        },
    )


@router.post("/servers/upload-zip")
async def upload_zip_server(
    request: Request,
    zip_file: UploadFile = File(...),
    name: str = Form(...),
    server_type: str = Form("minecraft_java"),
    port: int = Form(25565),
    min_memory: int = Form(1024),
    max_memory: int = Form(2048),
    java_path: str = Form("java"),
    loader: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")

    form_values = {
        "name": name,
        "server_type": server_type,
        "port": port,
        "min_memory": min_memory,
        "max_memory": max_memory,
        "java_path": java_path,
        "loader": loader,
    }
    errors = []

    # --- Validate form fields ---
    err = validate_server_name(name)
    if err:
        errors.append(err)

    err = validate_port(port)
    if err:
        errors.append(err)

    err = validate_memory(min_memory, max_memory)
    if err:
        errors.append(err)

    err = validate_server_type(server_type)
    if err:
        errors.append(err)

    # --- Validate file extension ---
    original_filename = zip_file.filename or ""
    if not original_filename.lower().endswith(".zip"):
        errors.append("Uploaded file must have a .zip extension.")

    if errors:
        await zip_file.close()
        return templates.TemplateResponse(
            "server_upload_zip.html",
            {
                "request": request,
                "errors": errors,
                "form_values": form_values,
            },
            status_code=422,
        )

    # --- Check for duplicate server name ---
    existing_name = await db.execute(select(Server).where(Server.name == name))
    if existing_name.scalars().first():
        errors.append(f"A server named '{name}' already exists.")
        await zip_file.close()
        return templates.TemplateResponse(
            "server_upload_zip.html",
            {
                "request": request,
                "errors": errors,
                "form_values": form_values,
            },
            status_code=422,
        )

    # --- Stream ZIP to a temp file with size limit + magic-byte check ---
    tmp_path = None
    server_dir = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name
            total_bytes = 0
            first_chunk = True
            while True:
                chunk = await zip_file.read(1024 * 1024)  # 1 MB chunks
                if not chunk:
                    break
                if first_chunk:
                    if not chunk[:4].startswith(ZIP_MAGIC):
                        errors.append(
                            "Uploaded file is not a valid ZIP archive (magic bytes check failed)."
                        )
                        break
                    first_chunk = False
                total_bytes += len(chunk)
                if total_bytes > MAX_ZIP_UPLOAD_SIZE:
                    errors.append(
                        f"Uploaded ZIP exceeds the maximum allowed size of "
                        f"{MAX_ZIP_UPLOAD_SIZE // (1024**3)} GB."
                    )
                    break
                tmp.write(chunk)

        if errors:
            return templates.TemplateResponse(
                "server_upload_zip.html",
                {
                    "request": request,
                    "errors": errors,
                    "form_values": form_values,
                },
                status_code=422,
            )

        # --- Create destination directory ---
        safe_name = re.sub(r"[^\w\-]", "_", name.strip())[:30]
        server_dir = Path(settings.servers_dir) / f"{safe_name}_{uuid.uuid4().hex[:8]}"
        server_dir.mkdir(parents=True, exist_ok=True)

        # --- Extract ZIP (path traversal + size protection in _extract_archive) ---
        try:
            await asyncio.to_thread(_extract_archive, tmp_path, str(server_dir))
        except ValueError as e:
            errors.append(str(e))
            return templates.TemplateResponse(
                "server_upload_zip.html",
                {
                    "request": request,
                    "errors": errors,
                    "form_values": form_values,
                },
                status_code=422,
            )

        # --- Strip single top-level directory (e.g. myserver.zip/myserver/*) ---
        # Move contents up rather than renaming across parent dirs (avoids
        # PermissionError on Windows-hosted Docker bind mounts).
        children = list(server_dir.iterdir())
        if len(children) == 1 and children[0].is_dir():
            inner = children[0]
            # Ensure inner dir is traversable/writable before moving its contents
            os.chmod(inner, 0o755)
            for item in list(inner.iterdir()):
                shutil.move(str(item), str(server_dir / item.name))
            inner.rmdir()

        # --- Auto-detect server info ---
        info = detect_server_info(str(server_dir))
        executable = info.get("executable") or "server.jar"

        # --- Build start command ---
        st = ServerType(server_type)
        resolved = str(server_dir.resolve())
        if st == ServerType.MINECRAFT_JAVA:
            start_command = f"{java_path} -Xms{min_memory}M -Xmx{max_memory}M -jar {executable} nogui"
        elif st == ServerType.MINECRAFT_BEDROCK:
            executable = "bedrock_server.exe" if os.name == "nt" else "bedrock_server"
            start_command = os.path.join(resolved, executable)
        else:
            start_command = ""

        # --- Insert Server row ---
        server = Server(
            name=name.strip(),
            server_type=st,
            status=ServerStatus.STOPPED,
            path=resolved,
            executable=executable,
            start_command=start_command,
            java_path=java_path,
            min_memory=min_memory,
            max_memory=max_memory,
            port=port,
            loader=loader or None,
        )
        db.add(server)
        await db.commit()

        ctx = get_audit_context(request)
        audit_service.create_task(
            audit_service.log(
                **ctx,
                action="server.upload_zip",
                resource_type="server",
                resource_id=str(server.id),
                details=f"name={name}, type={server_type}, zip={original_filename}",
            )
        )

        return RedirectResponse(url=f"/servers/{server.id}", status_code=303)

    except Exception:
        logger.exception("ZIP upload failed")
        errors.append("An unexpected error occurred while processing the ZIP file.")
        return templates.TemplateResponse(
            "server_upload_zip.html",
            {
                "request": request,
                "errors": errors,
                "form_values": form_values,
            },
            status_code=500,
        )
    finally:
        # Cleanup temp file always
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        # Cleanup extracted dir only on error
        if errors and server_dir and server_dir.exists():
            shutil.rmtree(server_dir, ignore_errors=True)


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

    logs = server_manager.get_logs(server_id)

    # Compute uptime
    uptime_seconds = None
    if server.started_at and server.status == ServerStatus.RUNNING:
        uptime_seconds = int(
            (datetime.now(timezone.utc) - server.started_at).total_seconds()
        )

    # Recent audit entries
    from app.models.audit_log import AuditLog

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
        import json as _json

        try:
            patterns = _json.loads(server.backup_exclude_patterns)
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
        from app.services.java_manager import get_managed_java_path, list_managed_javas

        managed_javas = list_managed_javas()
        # Check if current java_path is likely to work
        managed = get_managed_java_path(required_java_version)
        if server.java_path == "java" and not managed:
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
    if server.server_type == ServerType.STEAM:
        from app.models.workshop_item import WorkshopItem

        workshop_result = await db.execute(
            select(WorkshopItem).where(WorkshopItem.server_id == server_id)
        )
        workshop_items = workshop_result.scalars().all()
        steam_operation_snapshot = steamcmd.get_operation_snapshot(server_id)
        steam_has_active_update_start = steam_operation_snapshot.get(
            "operation"
        ) == "update_start" and steam_operation_snapshot.get("status") in {
            "queued",
            "running",
            "waiting_for_steam_guard",
        }
    from app.models.steam_account import SteamAccount

    steam_accounts_result = await db.execute(
        select(SteamAccount).order_by(SteamAccount.display_name)
    )
    steam_accounts = steam_accounts_result.scalars().all()

    return templates.TemplateResponse(
        "server_detail.html",
        {
            "request": request,
            "server": server,
            "mods": mods,
            "conflicts": conflicts,
            "backups": backups,
            "scheduled_tasks": scheduled_tasks,
            "task_types": [t.value for t in TaskType],
            "logs": logs,
            "is_running": server_manager.is_running(server_id),
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
            "workshop_items": workshop_items,
        },
    )


@router.post("/servers/{server_id}/start")
async def start_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "operate", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if (
        server.server_type == ServerType.STEAM
        and server.steam_update_on_start
        and server.steam_app_id
    ):
        snapshot = steamcmd.get_operation_snapshot(server_id)
        if snapshot.get("operation") == "update_start" and snapshot.get("status") in {
            "queued",
            "running",
            "waiting_for_steam_guard",
        }:
            raise HTTPException(
                status_code=400, detail="Steam update and start is already in progress"
            )
        if server_manager.is_running(server_id):
            raise HTTPException(status_code=400, detail="Server is already running")
        operation_id = await steamcmd.queue_operation(
            server_id,
            "update_start",
            f"Queued Steam update and start for {server.name}.",
        )
        _spawn_background_task(
            _run_background_steam_update_then_start(server_id, operation_id)
        )
        ctx = get_audit_context(request)
        audit_service.create_task(
            audit_service.log(
                **ctx,
                action="server.start",
                resource_type="server",
                resource_id=str(server_id),
                details=f"Queued Steam update-on-start for {server.name}",
            )
        )
        return RedirectResponse(url=f"/servers/{server_id}", status_code=303)

    server_manager._reset_crash_state(server_id)
    result = await server_manager.start_server(server_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.start",
            resource_type="server",
            resource_id=str(server_id),
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/stop")
async def stop_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "operate", db)
    success = await server_manager.stop_server(server_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to stop server")
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.stop",
            resource_type="server",
            resource_id=str(server_id),
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/steam/update")
async def steam_update_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Trigger SteamCMD update for a Steam server."""
    await require_role(request, "admin")
    server = await db.get(Server, server_id)
    if not server or server.server_type != ServerType.STEAM:
        raise HTTPException(404, "Steam server not found")
    await require_server_access(request, server_id, "manage", db)
    operation_id = await steamcmd.queue_operation(
        server_id, "update", f"Queued Steam update for {server.name}."
    )
    _spawn_background_task(_run_background_steam_update(server_id, operation_id))
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.steam_update",
            resource_type="server",
            resource_id=str(server_id),
            details=f"Queued manual Steam update for {server.name}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/steam/validate")
async def steam_validate_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Trigger SteamCMD file validation for a Steam server."""
    await require_role(request, "admin")
    server = await db.get(Server, server_id)
    if not server or server.server_type != ServerType.STEAM:
        raise HTTPException(404, "Steam server not found")
    await require_server_access(request, server_id, "manage", db)
    operation_id = await steamcmd.queue_operation(
        server_id, "validate", f"Queued Steam file validation for {server.name}."
    )
    _spawn_background_task(_run_manual_steam_validate(server_id, operation_id))
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.steam_validate",
            resource_type="server",
            resource_id=str(server_id),
            details=f"Queued file validation for {server.name}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/steam/guard", response_class=JSONResponse)
async def submit_steam_guard_code(
    request: Request,
    server_id: int,
    operation_id: str = Form(...),
    steam_guard_code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    await require_server_access(request, server_id, "manage", db)
    result = await steamcmd.submit_steam_guard_code(
        server_id, operation_id, steam_guard_code
    )
    status_code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status_code)


@router.get("/servers/{server_id}/workshop", response_class=HTMLResponse)
async def workshop_page(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Workshop items management page for a Steam server."""
    server = await db.get(Server, server_id)
    if not server or server.server_type != ServerType.STEAM:
        raise HTTPException(404)
    await require_server_access(request, server_id, "view", db)
    from app.models.workshop_item import WorkshopItem

    result = await db.execute(
        select(WorkshopItem).where(WorkshopItem.server_id == server_id)
    )
    items = result.scalars().all()
    return templates.TemplateResponse(
        "workshop.html",
        {
            "request": request,
            "server": server,
            "items": items,
        },
    )


@router.post("/servers/{server_id}/workshop/add")
async def add_workshop_item(
    request: Request,
    server_id: int,
    workshop_id: str = Form(...),
    name: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Add and install a Workshop item."""
    await require_role(request, "admin")
    server = await db.get(Server, server_id)
    if not server or server.server_type != ServerType.STEAM:
        raise HTTPException(404)
    await require_server_access(request, server_id, "manage", db)
    from app.models.workshop_item import WorkshopItem

    item = WorkshopItem(
        server_id=server_id,
        workshop_id=workshop_id,
        app_id=server.steam_app_id,
        name=name or f"Workshop Item {workshop_id}",
        installed=False,
        last_updated=None,
    )
    db.add(item)
    await db.commit()
    operation_id = await steamcmd.queue_operation(
        server_id,
        "workshop_install",
        f"Queued workshop install for item {workshop_id}.",
        workshop_item_id=workshop_id,
    )
    _spawn_background_task(
        _run_workshop_install(server_id, item.id, "workshop_install", operation_id)
    )
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="workshop.install",
            resource_type="server",
            resource_id=str(server_id),
            details=f"Queued workshop item {workshop_id} for server {server.name}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}/workshop", status_code=303)


@router.post("/servers/{server_id}/workshop/{item_id}/remove")
async def remove_workshop_item(
    request: Request,
    server_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove a Workshop item."""
    await require_role(request, "admin")
    from app.models.workshop_item import WorkshopItem

    item = await db.get(WorkshopItem, item_id)
    if not item or item.server_id != server_id:
        raise HTTPException(404)
    await require_server_access(request, server_id, "manage", db)
    await db.delete(item)
    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}/workshop", status_code=303)


@router.post("/servers/{server_id}/workshop/{item_id}/update")
async def update_workshop_item(
    request: Request,
    server_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Re-download/update a Workshop item."""
    await require_role(request, "admin")
    from app.models.workshop_item import WorkshopItem

    item = await db.get(WorkshopItem, item_id)
    if not item or item.server_id != server_id:
        raise HTTPException(404)
    await require_server_access(request, server_id, "manage", db)
    operation_id = await steamcmd.queue_operation(
        server_id,
        "workshop_update",
        f"Queued workshop update for item {item.workshop_id}.",
        workshop_item_id=item.workshop_id,
    )
    _spawn_background_task(
        _run_workshop_install(server_id, item.id, "workshop_update", operation_id)
    )
    return RedirectResponse(url=f"/servers/{server_id}/workshop", status_code=303)


@router.post("/servers/{server_id}/steam/settings")
async def update_steam_settings(
    request: Request,
    server_id: int,
    steam_app_id: str = Form(""),
    steam_branch: str = Form("public"),
    steam_login_anonymous: bool = Form(True),
    steam_account_id: str = Form(""),
    steam_gslt: str = Form(""),
    clear_steam_gslt: bool = Form(False),
    steam_update_on_start: bool = Form(False),
    query_port: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    server = await db.get(Server, server_id)
    if not server or server.server_type != ServerType.STEAM:
        raise HTTPException(404, "Steam server not found")
    await require_server_access(request, server_id, "manage", db)
    steam_account_id_value = int(steam_account_id) if steam_account_id else None

    if steam_app_id and not steam_login_anonymous and not steam_account_id_value:
        raise HTTPException(
            status_code=400, detail="Select a Steam account or enable anonymous login."
        )

    server.steam_app_id = steam_app_id or None
    server.steam_branch = steam_branch or "public"
    server.steam_login_anonymous = steam_login_anonymous
    server.steam_account_id = None if steam_login_anonymous else steam_account_id_value
    if clear_steam_gslt:
        server.steam_gslt = None
    elif steam_gslt.strip():
        server.steam_gslt = steam_gslt.strip()
    server.steam_update_on_start = steam_update_on_start
    if query_port:
        new_query_port = query_port
    elif server.query_port is None:
        new_query_port = server.port + 1
    else:
        new_query_port = server.query_port

    if new_query_port != server.query_port:
        conflicts = await port_manager.check_conflicts(
            db,
            server.port,
            server.rcon_port,
            new_query_port,
            exclude_server_id=server.id,
        )
        if conflicts:
            raise HTTPException(status_code=400, detail=" ".join(conflicts))
    server.query_port = new_query_port

    app_info = steamcmd.get_app_info(steam_app_id) if steam_app_id else None
    if app_info:
        server.executable = app_info.get("executable", server.executable)
        server.start_command = (
            generate_start_command(
                steam_app_id, server.port, server.name, server.query_port
            )
            or server.start_command
        )

    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/restart")
async def restart_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "operate", db)
    await server_manager.stop_server(server_id)
    await asyncio.sleep(2)
    result = await server_manager.start_server(server_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.restart",
            resource_type="server",
            resource_id=str(server_id),
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/command")
async def send_command(
    request: Request,
    server_id: int,
    command: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "operate", db)
    err = validate_command_length(command)
    if err:
        raise HTTPException(status_code=400, detail=err)
    success = await server_manager.send_command(server_id, command)
    if not success:
        raise HTTPException(status_code=400, detail="Server is not running")
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/auto-start")
async def toggle_auto_start(
    server_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server.auto_start = not server.auto_start
    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/auto-restart-crash")
async def toggle_auto_restart_crash(
    server_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server.auto_restart_on_crash = not server.auto_restart_on_crash
    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/crash-restart-settings")
async def update_crash_restart_settings(
    server_id: int,
    request: Request,
    max_crash_restarts: int = Form(...),
    crash_restart_delay: int = Form(...),
    crash_stability_window: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if max_crash_restarts < 1 or max_crash_restarts > 20:
        raise HTTPException(
            status_code=400, detail="max_crash_restarts must be between 1 and 20"
        )
    if crash_restart_delay < 5 or crash_restart_delay > 300:
        raise HTTPException(
            status_code=400,
            detail="crash_restart_delay must be between 5 and 300 seconds",
        )
    if crash_stability_window < 60 or crash_stability_window > 3600:
        raise HTTPException(
            status_code=400,
            detail="crash_stability_window must be between 60 and 3600 seconds",
        )
    server.max_crash_restarts = max_crash_restarts
    server.crash_restart_delay = crash_restart_delay
    server.crash_stability_window = crash_stability_window
    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/install-java")
async def install_java(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if not server.mc_version:
        raise HTTPException(status_code=400, detail="Server has no MC version set")
    required = get_required_java_version(server.mc_version)
    from app.services.java_manager import download_java

    java_path = await download_java(required)
    if not java_path:
        raise HTTPException(
            status_code=500, detail=f"Failed to download Java {required}"
        )
    server.java_path = java_path
    await db.commit()
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="java.install",
            resource_type="server",
            resource_id=str(server_id),
            details=f"java_version={required}, path={java_path}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}#settings", status_code=303)


@router.post("/servers/{server_id}/jvm-flags")
async def save_jvm_flags(
    request: Request,
    server_id: int,
    jvm_flags: str = Form(""),
    server_args: str = Form(""),
    ready_log_pattern: str = Form(""),
    executable: str = Form(""),
    min_memory: int = Form(0),
    max_memory: int = Form(0),
    mc_version: str = Form(""),
    loader: str = Form(""),
    loader_version: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server.jvm_flags = jvm_flags.strip() or None
    server.server_args = server_args.strip() or None
    server.ready_log_pattern = ready_log_pattern.strip() or None
    if executable.strip():
        server.executable = executable.strip()
    if min_memory > 0 and max_memory > 0:
        if min_memory > max_memory:
            raise HTTPException(status_code=400, detail="Min RAM cannot exceed Max RAM")
        server.min_memory = min_memory
        server.max_memory = max_memory
    server.mc_version = mc_version.strip() or None
    server.loader = loader.strip() or None
    server.loader_version = loader_version.strip() or None
    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}#settings", status_code=303)


@router.post("/servers/{server_id}/memory")
async def save_server_memory(
    request: Request,
    server_id: int,
    min_memory: int = Form(...),
    max_memory: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    err = validate_memory(min_memory, max_memory)
    if err:
        raise HTTPException(status_code=400, detail=err)

    server.min_memory = min_memory
    server.max_memory = max_memory
    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}?tab=settings", status_code=303)


@router.post("/servers/{server_id}/max-backups")
async def set_max_backups(
    server_id: int,
    request: Request,
    max_backups: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if max_backups < 1 or max_backups > 100:
        raise HTTPException(
            status_code=400, detail="max_backups must be between 1 and 100"
        )
    server.max_backups = max_backups
    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/backup-settings")
async def save_backup_settings(
    request: Request,
    server_id: int,
    backup_exclude_patterns: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    import json

    patterns = [
        p.strip() for p in backup_exclude_patterns.strip().splitlines() if p.strip()
    ]
    server.backup_exclude_patterns = json.dumps(patterns) if patterns else None
    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}#settings", status_code=303)


@router.post("/servers/{server_id}/notifications")
async def save_notification_settings(
    request: Request,
    server_id: int,
    webhook_url: str = Form(""),
    muted: bool = Form(False),
    events: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server.notification_webhook_url = webhook_url.strip() or None
    server.notifications_muted = muted
    server.notification_events = events.strip() or None
    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}#settings", status_code=303)


@router.post("/servers/{server_id}/rcon", response_class=JSONResponse)
async def rcon_command(
    request: Request,
    server_id: int,
    command: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "operate", db)
    server = await db.get(Server, server_id)
    if not server:
        return JSONResponse(
            {"ok": False, "response": "Server not found"}, status_code=404
        )
    if not server.rcon_enabled or not server.rcon_port or not server.rcon_password:
        return JSONResponse(
            {"ok": False, "response": "RCON is not configured for this server"},
            status_code=400,
        )

    err = validate_command_length(command)
    if err:
        return JSONResponse({"ok": False, "response": err}, status_code=400)

    client = RCONClient()
    try:
        authed = await client.connect(
            "127.0.0.1", server.rcon_port, server.rcon_password
        )
        if not authed:
            return JSONResponse({"ok": False, "response": "RCON authentication failed"})
        response = await client.send_command(command)
        return JSONResponse({"ok": True, "response": response})
    except Exception as e:
        logger.warning(f"RCON error for server {server_id}: {e}")
        return JSONResponse({"ok": False, "response": "RCON connection failed"})
    finally:
        await client.close()


@router.post("/servers/{server_id}/delete")
async def delete_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_role(request, "admin")
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if server_manager.is_running(server_id):
        await server_manager.stop_server(server_id)

    # Delete mods
    result = await db.execute(select(Mod).where(Mod.server_id == server_id))
    for mod in result.scalars().all():
        await db.delete(mod)

    # Delete related records
    from sqlalchemy import delete as sa_delete

    await db.execute(sa_delete(Backup).where(Backup.server_id == server_id))
    await db.execute(
        sa_delete(ScheduledTask).where(ScheduledTask.server_id == server_id)
    )
    await db.execute(
        sa_delete(MetricSnapshot).where(MetricSnapshot.server_id == server_id)
    )
    from app.models.server_access import ServerAccess

    await db.execute(sa_delete(ServerAccess).where(ServerAccess.server_id == server_id))

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.delete",
            resource_type="server",
            resource_id=str(server_id),
            details=f"name={server.name}",
        )
    )

    await db.delete(server)
    await db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/servers/{server_id}/clone")
async def clone_server(
    request: Request,
    server_id: int,
    clone_name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    source = await db.get(Server, server_id)
    if not source:
        raise HTTPException(status_code=404, detail="Server not found")

    clone_name = clone_name.strip()
    err = validate_server_name(clone_name)
    if err:
        raise HTTPException(status_code=400, detail=err)

    existing = await db.execute(select(Server).where(Server.name == clone_name))
    if existing.scalars().first():
        raise HTTPException(
            status_code=400, detail=f"A server named '{clone_name}' already exists."
        )

    clone_path = os.path.join(settings.servers_dir, uuid.uuid4().hex[:12])
    if os.path.exists(clone_path):
        raise HTTPException(status_code=400, detail="Server directory already exists.")

    suggested = await port_manager.suggest_ports(db, source.server_type.value)

    try:
        await asyncio.to_thread(shutil.copytree, source.path, clone_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to copy server files: {e}")

    new_server = Server(
        name=clone_name,
        server_type=source.server_type,
        status=ServerStatus.STOPPED,
        path=clone_path,
        executable=source.executable,
        start_command=source.start_command.replace(source.path, clone_path)
        if source.path in source.start_command
        else source.start_command,
        java_path=source.java_path,
        min_memory=source.min_memory,
        max_memory=source.max_memory,
        port=suggested["game_port"],
        query_port=suggested["query_port"] if source.server_type == ServerType.STEAM else source.query_port,
        auto_start=False,
        mc_version=source.mc_version,
        loader=source.loader,
        steam_app_id=source.steam_app_id,
        rcon_enabled=source.rcon_enabled,
        rcon_port=suggested["rcon_port"] if source.rcon_enabled else None,
        rcon_password=source.rcon_password,
        auto_update_server=source.auto_update_server,
        auto_restart_on_crash=source.auto_restart_on_crash,
        max_crash_restarts=source.max_crash_restarts,
        crash_restart_delay=source.crash_restart_delay,
        crash_stability_window=source.crash_stability_window,
        environment_vars=source.environment_vars,
        jvm_flags=source.jvm_flags,
        server_args=source.server_args,
        ready_log_pattern=source.ready_log_pattern,
    )
    db.add(new_server)
    await db.commit()

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.clone",
            resource_type="server",
            resource_id=str(new_server.id),
            details=f"cloned from={source.name}, new_name={clone_name}",
        )
    )

    return RedirectResponse(url=f"/servers/{new_server.id}", status_code=303)


@router.get("/servers/{server_id}/stats")
async def server_stats(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if server_manager.is_running(server_id):
        sp = server_manager.processes.get(server_id)
        process_stats = None
        if sp and sp.process.pid:
            process_stats = resource_monitor.get_process_stats(sp.process.pid)
        return JSONResponse(
            {
                "running": True,
                "process": process_stats,
                "system": resource_monitor.get_system_stats(),
            }
        )

    return JSONResponse(
        {
            "running": False,
            "process": None,
            "system": resource_monitor.get_system_stats(),
        }
    )


@router.get("/system/stats")
async def system_stats():
    return JSONResponse(resource_monitor.get_system_stats())


@router.get("/servers/{server_id}/metrics", response_class=JSONResponse)
async def get_server_metrics(
    request: Request,
    server_id: int,
    period: str = Query("1h"),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "view", db)
    from datetime import datetime, timedelta, timezone

    periods = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
    }
    delta = periods.get(period)
    if not delta:
        raise HTTPException(
            status_code=400, detail="Invalid period. Use: 1h, 6h, 24h, 7d"
        )

    cutoff = datetime.now(timezone.utc) - delta
    result = await db.execute(
        select(MetricSnapshot)
        .where(
            MetricSnapshot.server_id == server_id, MetricSnapshot.timestamp >= cutoff
        )
        .order_by(MetricSnapshot.timestamp.asc())
    )
    snapshots = result.scalars().all()
    return JSONResponse(
        [
            {
                "timestamp": s.timestamp.isoformat(),
                "cpu_percent": s.cpu_percent,
                "memory_mb": s.memory_mb,
            }
            for s in snapshots
        ]
    )


@router.get("/servers/{server_id}/logs/search")
async def search_server_logs(
    request: Request,
    server_id: int,
    q: str = Query(..., min_length=1),
    max_results: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    results = await asyncio.to_thread(
        log_manager.search_logs, server.name, q, max_results
    )
    return JSONResponse({"results": results})


@router.get("/servers/{server_id}/logs/history")
async def server_log_history(
    request: Request,
    server_id: int,
    lines: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    log_lines = await asyncio.to_thread(log_manager.get_logs, server.name, lines)
    return JSONResponse({"lines": log_lines})


@router.get("/servers/{server_id}/worlds", response_class=JSONResponse)
async def server_worlds(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    worlds = world_manager.list_worlds(server.path)
    return JSONResponse(
        {"worlds": worlds, "is_running": server_manager.is_running(server_id)}
    )


@router.post("/servers/{server_id}/worlds/reset")
async def reset_world(
    request: Request,
    server_id: int,
    world_name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    result = await world_manager.reset_world(server_id, server.path, world_name)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="world.reset",
            resource_type="server",
            resource_id=str(server_id),
            details=f"world={world_name}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/worlds/switch")
async def switch_world(
    request: Request,
    server_id: int,
    level_name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if server_manager.is_running(server_id):
        raise HTTPException(
            status_code=400, detail="Server must be stopped to switch worlds"
        )
    result = world_manager.switch_world(server.path, level_name)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.get("/servers/{server_id}/update-check", response_class=JSONResponse)
async def check_server_update(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    info = await server_updater.check_update(server)
    if info and info.get("latest"):
        server.latest_known_version = info["latest"]
        await db.commit()
    return JSONResponse({"ok": True, "data": info})


@router.post("/servers/{server_id}/update")
async def update_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    result = await server_updater.update_server(server_id, db)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.update",
            resource_type="server",
            resource_id=str(server_id),
            details=result["message"],
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.post("/servers/{server_id}/auto-update")
async def toggle_auto_update(
    server_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server.auto_update_server = not server.auto_update_server
    await db.commit()
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)


@router.get("/servers/{server_id}/env", response_class=JSONResponse)
async def get_env_vars(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    import json

    try:
        env = json.loads(server.environment_vars or "{}")
    except (json.JSONDecodeError, TypeError):
        env = {}
    return JSONResponse({"ok": True, "data": env})


@router.post("/servers/{server_id}/env", response_class=JSONResponse)
async def set_env_vars(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    import json

    body = await request.json()
    env = body.get("env", {})

    if not isinstance(env, dict):
        raise HTTPException(status_code=400, detail="env must be a JSON object")

    # Validate keys: no empty keys, no whitespace-only keys
    for key in env:
        if not key or not key.strip():
            raise HTTPException(
                status_code=400, detail="Environment variable keys cannot be empty"
            )
        if len(key) > 256:
            raise HTTPException(status_code=400, detail=f"Key too long: {key[:50]}...")
        if len(str(env[key])) > 4096:
            raise HTTPException(
                status_code=400, detail=f"Value too long for key: {key}"
            )

    server.environment_vars = json.dumps(env)
    await db.commit()
    return JSONResponse({"ok": True})


# -- Player List (Online) --------------------------------------------------


@router.get("/servers/{server_id}/players", response_class=JSONResponse)
async def get_players(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if not server_manager.is_running(server_id):
        return JSONResponse({"players": [], "online": 0, "max": 0, "source": "offline"})

    # Try RCON first for Minecraft
    if server.rcon_enabled and server.rcon_port and server.rcon_password:
        client = RCONClient()
        try:
            authed = await client.connect(
                "127.0.0.1", server.rcon_port, server.rcon_password
            )
            if authed:
                response = await client.send_command("list")
                players = _parse_player_list(response)
                return JSONResponse(
                    {
                        "players": [{"name": p} for p in players],
                        "online": len(players),
                        "max": 0,
                        "source": "rcon",
                    }
                )
        except Exception:
            pass
        finally:
            await client.close()

    # Fallback: query protocol
    if server.server_type in (ServerType.MINECRAFT_JAVA,):
        result = await minecraft_query.query("127.0.0.1", server.port)
        if result:
            return JSONResponse(
                {
                    "players": result["players"],
                    "online": result["online"],
                    "max": result["max"],
                    "source": "slp",
                }
            )
    elif server.server_type == ServerType.STEAM:
        result = await steam_query.query_players("127.0.0.1", server.port)
        if result is not None:
            return JSONResponse(
                {
                    "players": result,
                    "online": len(result),
                    "max": 0,
                    "source": "a2s",
                }
            )

    return JSONResponse({"players": [], "online": 0, "max": 0, "source": "unavailable"})


def _parse_player_list(rcon_response: str) -> list[str]:
    """Parse Minecraft RCON 'list' response into player names."""
    # Format: "There are X of a max of Y players online: player1, player2, ..."
    if ":" in rcon_response:
        after_colon = rcon_response.split(":", 1)[1].strip()
        if after_colon:
            return [p.strip() for p in after_colon.split(",") if p.strip()]
    return []


# -- Whitelist / Ban Management --------------------------------------------


@router.get("/servers/{server_id}/players/manage", response_class=HTMLResponse)
async def player_management_page(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=302)


@router.post("/servers/{server_id}/whitelist/add")
async def whitelist_add(
    request: Request,
    server_id: int,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    player_manager.add_to_whitelist(server.path, name.strip())
    if (
        server_manager.is_running(server_id)
        and server.rcon_enabled
        and server.rcon_port
        and server.rcon_password
    ):
        await player_manager.rcon_whitelist_add(None, server, name.strip())

    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=303)


@router.post("/servers/{server_id}/whitelist/remove")
async def whitelist_remove(
    request: Request,
    server_id: int,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    player_manager.remove_from_whitelist(server.path, name.strip())
    if (
        server_manager.is_running(server_id)
        and server.rcon_enabled
        and server.rcon_port
        and server.rcon_password
    ):
        await player_manager.rcon_whitelist_remove(None, server, name.strip())

    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=303)


@router.post("/servers/{server_id}/ban")
async def ban_player_route(
    request: Request,
    server_id: int,
    name: str = Form(...),
    reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    import re

    if not re.match(r"^[a-zA-Z0-9_]{1,64}$", name.strip()):
        raise HTTPException(status_code=400, detail="Invalid player name")

    player_manager.ban_player(
        server.path, name.strip(), reason.strip() or "Banned by operator"
    )
    if (
        server_manager.is_running(server_id)
        and server.rcon_enabled
        and server.rcon_port
        and server.rcon_password
    ):
        await player_manager.rcon_ban(None, server, name.strip(), reason.strip())

    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=303)


@router.post("/servers/{server_id}/pardon")
async def pardon_player_route(
    request: Request,
    server_id: int,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    player_manager.pardon_player(server.path, name.strip())
    if (
        server_manager.is_running(server_id)
        and server.rcon_enabled
        and server.rcon_port
        and server.rcon_password
    ):
        await player_manager.rcon_pardon(None, server, name.strip())

    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=303)


# -- Server Configuration Editor -------------------------------------------


@router.get("/servers/{server_id}/config", response_class=HTMLResponse)
async def server_config_page(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=302)


@router.post("/servers/{server_id}/config")
async def save_server_config(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    from app.services.config_editor import (
        get_field_schema,
        write_properties,
    )

    form = await request.form()
    props_path = Path(server.path) / "server.properties"
    original = (
        props_path.read_text(encoding="utf-8", errors="replace")
        if props_path.exists()
        else ""
    )

    editor_mode = form.get("_editor_mode", "typed")

    if editor_mode == "raw":
        raw_content = str(form.get("_raw_content", ""))
        props_path.parent.mkdir(parents=True, exist_ok=True)
        props_path.write_text(raw_content, encoding="utf-8")
    else:
        schema = get_field_schema(server.server_type.value)
        updates = {}
        for key, field in schema.items():
            if field["type"] == "boolean":
                updates[key] = "true" if form.get(key) else "false"
            elif key in form:
                updates[key] = str(form[key])

        new_content = write_properties(original, updates)
        props_path.parent.mkdir(parents=True, exist_ok=True)
        props_path.write_text(new_content, encoding="utf-8")

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.config_edit",
            resource_type="server",
            resource_id=str(server_id),
            details=f"Edited server.properties ({editor_mode} mode)",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}?tab=config", status_code=303)


# -- Uptime Schedule -------------------------------------------------------


@router.post("/servers/{server_id}/uptime-schedule")
async def save_uptime_schedule(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    import json

    form = await request.form()
    start_time = str(form.get("start_time", "")).strip()
    stop_time = str(form.get("stop_time", "")).strip()
    days = form.getlist("days")
    warning_minutes = int(form.get("warning_minutes", 0) or 0)

    if start_time and stop_time:
        # Validate time format
        import re

        if not re.match(r"^\d{2}:\d{2}$", start_time) or not re.match(
            r"^\d{2}:\d{2}$", stop_time
        ):
            raise HTTPException(
                status_code=400, detail="Invalid time format. Use HH:MM."
            )
        day_ints = []
        for d in days:
            try:
                day_int = int(d)
                if 0 <= day_int <= 6:
                    day_ints.append(day_int)
            except (ValueError, TypeError):
                pass
        server.uptime_schedule = json.dumps(
            {
                "start_time": start_time,
                "stop_time": stop_time,
                "days": day_ints if day_ints else [0, 1, 2, 3, 4, 5, 6],
                "warning_minutes": max(0, min(60, warning_minutes)),
            }
        )
    else:
        server.uptime_schedule = None

    await db.commit()

    from app.services.task_scheduler import task_scheduler

    await task_scheduler.sync_uptime_schedule(server_id)

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="server.uptime_schedule",
            resource_type="server",
            resource_id=str(server_id),
            details=f"Schedule: {server.uptime_schedule or 'cleared'}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}#settings", status_code=303)


# -- Port Reachability Check -----------------------------------------------


@router.get("/servers/{server_id}/port-check", response_class=JSONResponse)
async def check_port(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    result = await port_manager.check_port_reachable(server.port)
    return JSONResponse(result)


# -- Bulk Operations -------------------------------------------------------


@router.post("/servers/bulk-action")
async def bulk_action(request: Request, db: AsyncSession = Depends(get_db)):
    await get_current_user(request, db)
    form = await request.form()
    action = form.get("action")
    server_ids_raw = form.getlist("server_ids")
    if not server_ids_raw:
        return RedirectResponse(url="/", status_code=303)

    try:
        server_ids = [int(x) for x in server_ids_raw]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid server IDs")

    if action not in ("start", "stop", "restart", "backup"):
        raise HTTPException(status_code=400, detail="Invalid action")

    for sid in server_ids:
        await require_server_access(request, sid, "operate", db)

    for sid in server_ids:
        try:
            if action == "start":
                await server_manager.start_server(sid)
            elif action == "stop":
                await server_manager.stop_server(sid)
            elif action == "restart":
                await server_manager.stop_server(sid)
                await asyncio.sleep(2)
                await server_manager.start_server(sid)
            elif action == "backup":
                from app.services.backup_manager import backup_manager

                await backup_manager.create_backup(sid)
        except Exception as e:
            logger.warning(f"Bulk action {action} failed for server {sid}: {e}")

    return RedirectResponse(url="/", status_code=303)


# -- Console Improvements: Saved Commands ----------------------------------


@router.post("/servers/{server_id}/saved-commands", response_class=JSONResponse)
async def save_commands(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    body = await request.json()
    commands = body.get("commands", [])
    if not isinstance(commands, list):
        raise HTTPException(status_code=400, detail="commands must be a list")
    if len(commands) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 saved commands")
    server.saved_commands = json.dumps(commands)
    await db.commit()
    return JSONResponse({"ok": True})


@router.get("/servers/{server_id}/saved-commands", response_class=JSONResponse)
async def get_saved_commands(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    try:
        commands = json.loads(server.saved_commands or "[]")
    except (json.JSONDecodeError, TypeError):
        commands = []
    return JSONResponse({"commands": commands})


@router.get("/servers/{server_id}/logs/download")
async def download_logs(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    log_file = Path(server.path) / "logs" / "latest.log"
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(str(log_file), filename=f"{server.name}_latest.log")


# -- Config Import/Export --------------------------------------------------


@router.get("/servers/{server_id}/export-config", response_class=JSONResponse)
async def export_server_config(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    from app.services.config_export import export_config

    data = await export_config(db, server_id)
    return JSONResponse(
        data,
        headers={
            "Content-Disposition": f"attachment; filename=server_{server_id}_config.json"
        },
    )


@router.post("/servers/import-config")
async def import_server_config(
    request: Request,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    content = await file.read()
    if len(content) > 1_000_000:
        raise HTTPException(status_code=400, detail="Config file too large (max 1MB)")
    try:
        config_data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON file")

    if not isinstance(config_data, dict):
        raise HTTPException(status_code=400, detail="Config must be a JSON object")

    import re

    name = config_data.get("name", "imported").replace(" ", "_")
    name = re.sub(r"[^\w\-]", "_", name)[:50]
    server_dir = Path(settings.servers_dir) / f"{name}_{uuid.uuid4().hex[:8]}"
    server_dir.mkdir(parents=True, exist_ok=True)

    from app.services.config_export import import_config

    server = await import_config(db, config_data, str(server_dir))
    return RedirectResponse(url=f"/servers/{server.id}", status_code=303)
