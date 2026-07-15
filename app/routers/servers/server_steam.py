import logging

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server, ServerType
from app.models.workshop_item import WorkshopItem
from app.routers.servers._shared import (
    get_current_user_dep,
    get_db,
    require_role,
    require_server_access,
    run_background_steam_update,
    run_manual_steam_validate,
    run_workshop_install,
    spawn_background_task,
)
from app.services.audit_service import audit_service, get_audit_context
from app.services.port_manager import port_manager
from app.services.steamcmd import generate_start_command, steamcmd
from app.template_utils import templates

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_dep)])

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
    spawn_background_task(run_background_steam_update(server_id, operation_id))
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
    spawn_background_task(run_manual_steam_validate(server_id, operation_id))
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
    result = await db.execute(
        select(WorkshopItem).where(WorkshopItem.server_id == server_id)
    )
    items = result.scalars().all()
    return templates.TemplateResponse(request, "workshop.html", {
            "server": server,
            "items": items,
        })

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
    spawn_background_task(
        run_workshop_install(server_id, item.id, "workshop_install", operation_id)
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
    spawn_background_task(
        run_workshop_install(server_id, item.id, "workshop_update", operation_id)
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

