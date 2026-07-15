import asyncio
import logging

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server, ServerType
from app.routers.servers._shared import (
    get_current_user_dep,
    get_db,
    require_server_access,
    run_background_steam_update_then_start,
    spawn_background_task,
)
from app.services.audit_service import audit_service, get_audit_context
from app.services.server_manager import server_manager
from app.services.steamcmd import steamcmd
from app.validation import (
    validate_command_length,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_dep)])

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
        if server_manager.is_running(server_id, db=db):
            raise HTTPException(status_code=400, detail="Server is already running")
        operation_id = await steamcmd.queue_operation(
            server_id,
            "update_start",
            f"Queued Steam update and start for {server.name}.",
        )
        spawn_background_task(
            run_background_steam_update_then_start(server_id, operation_id)
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
    result = await server_manager.start_server(server_id, db=db)
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
    success = await server_manager.stop_server(server_id, db=db)
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

@router.post("/servers/{server_id}/restart")
async def restart_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "operate", db)
    await server_manager.stop_server(server_id, db=db)
    await asyncio.sleep(2)
    result = await server_manager.start_server(server_id, db=db)
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
    success = await server_manager.send_command(server_id, command, db=db)
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

