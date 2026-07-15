import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.backup import Backup
from app.models.metric import MetricSnapshot
from app.models.mod import Mod
from app.models.node import Node
from app.models.scheduled_task import ScheduledTask
from app.models.server import Server, ServerStatus, ServerType
from app.models.server_access import ServerAccess
from app.models.steam_account import SteamAccount
from app.routers.files import _extract_archive
from app.routers.servers._shared import (
    get_current_user_dep,
    get_db,
    require_role,
    run_create_steam_install,
    spawn_background_task,
)
from app.services.audit_service import audit_service, get_audit_context
from app.services.config_editor import (
    generate_default_properties,
)
from app.services.config_export import import_config
from app.services.jar_downloader import download_bedrock_server, download_server_jar
from app.services.java_manager import (
    find_java_for_mc,
)
from app.services.port_manager import port_manager
from app.services.server_detector import detect_server_info
from app.services.server_manager import server_manager
from app.services.server_templates import get_templates
from app.services.steamcmd import steamcmd
from app.template_utils import templates
from app.validation import (
    validate_mc_version,
    validate_memory,
    validate_port,
    validate_server_name,
    validate_server_type,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_dep)])

MAX_ZIP_UPLOAD_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB compressed
ZIP_MAGIC = b"PK\x03\x04"  # Local file header signature

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
        steam_accounts_err = (await db.execute(select(SteamAccount))).scalars().all()
        return templates.TemplateResponse(request, "server_create.html", {
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
            })

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
        spawn_background_task(run_create_steam_install(server.id, operation_id))

    return RedirectResponse(url=f"/servers/{server.id}", status_code=303)

@router.get("/servers/import", response_class=HTMLResponse)
async def import_server_form(request: Request):
    return templates.TemplateResponse(request, "server_import.html", {
            "errors": [],
            "form_values": {},
        })

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
        return templates.TemplateResponse(request, "server_import.html", {
                "errors": errors,
                "form_values": form_values,
            })

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
    return templates.TemplateResponse(request, "server_upload_zip.html", {
            "errors": [],
            "form_values": {},
        })

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
        return templates.TemplateResponse(request, "server_upload_zip.html", {
                "errors": errors,
                "form_values": form_values,
            }, status_code=422)

    # --- Check for duplicate server name ---
    existing_name = await db.execute(select(Server).where(Server.name == name))
    if existing_name.scalars().first():
        errors.append(f"A server named '{name}' already exists.")
        await zip_file.close()
        return templates.TemplateResponse(request, "server_upload_zip.html", {
                "errors": errors,
                "form_values": form_values,
            }, status_code=422)

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
            return templates.TemplateResponse(request, "server_upload_zip.html", {
                    "errors": errors,
                    "form_values": form_values,
                }, status_code=422)

        # --- Create destination directory ---
        safe_name = re.sub(r"[^\w\-]", "_", name.strip())[:30]
        server_dir = Path(settings.servers_dir) / f"{safe_name}_{uuid.uuid4().hex[:8]}"
        server_dir.mkdir(parents=True, exist_ok=True)

        # --- Extract ZIP (path traversal + size protection in _extract_archive) ---
        try:
            await asyncio.to_thread(_extract_archive, tmp_path, str(server_dir))
        except ValueError as e:
            errors.append(str(e))
            return templates.TemplateResponse(request, "server_upload_zip.html", {
                    "errors": errors,
                    "form_values": form_values,
                }, status_code=422)

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
        return templates.TemplateResponse(request, "server_upload_zip.html", {
                "errors": errors,
                "form_values": form_values,
            }, status_code=500)
    finally:
        # Cleanup temp file always
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        # Cleanup extracted dir only on error
        if errors and server_dir and server_dir.exists():
            shutil.rmtree(server_dir, ignore_errors=True)

@router.post("/servers/{server_id}/delete")
async def delete_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_role(request, "admin")
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if server_manager.is_running(server_id, db=db):
        await server_manager.stop_server(server_id, db=db)

    # Delete mods
    result = await db.execute(select(Mod).where(Mod.server_id == server_id))
    for mod in result.scalars().all():
        await db.delete(mod)

    # Delete related records
    await db.execute(sa_delete(Backup).where(Backup.server_id == server_id))
    await db.execute(
        sa_delete(ScheduledTask).where(ScheduledTask.server_id == server_id)
    )
    await db.execute(
        sa_delete(MetricSnapshot).where(MetricSnapshot.server_id == server_id)
    )
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

    name = config_data.get("name", "imported").replace(" ", "_")
    name = re.sub(r"[^\w\-]", "_", name)[:50]
    server_dir = Path(settings.servers_dir) / f"{name}_{uuid.uuid4().hex[:8]}"
    server_dir.mkdir(parents=True, exist_ok=True)

    server = await import_config(db, config_data, str(server_dir))
    return RedirectResponse(url=f"/servers/{server.id}", status_code=303)

