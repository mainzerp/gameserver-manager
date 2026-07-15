import json
import logging
import re
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.routers.servers._shared import get_current_user_dep, get_db, require_server_access
from app.services.audit_service import audit_service, get_audit_context
from app.services.config_editor import (
    get_field_schema,
    write_properties,
)
from app.services.java_manager import (
    download_java,
    get_required_java_version,
)
from app.services.rcon_client import RCONClient
from app.services.task_scheduler import task_scheduler
from app.validation import (
    validate_command_length,
    validate_memory,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_dep)])

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

@router.get("/servers/{server_id}/env", response_class=JSONResponse)
async def get_env_vars(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
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

