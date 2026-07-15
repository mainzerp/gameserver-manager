import logging

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.routers.servers._shared import get_current_user_dep, get_db, require_server_access
from app.services.audit_service import audit_service, get_audit_context
from app.services.server_manager import server_manager
from app.services.world_manager import world_manager

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_dep)])

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
        {"worlds": worlds, "is_running": server_manager.is_running(server_id, db=db)}
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
    if server_manager.is_running(server_id, db=db):
        raise HTTPException(
            status_code=400, detail="Server must be stopped to switch worlds"
        )
    result = world_manager.switch_world(server.path, level_name)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return RedirectResponse(url=f"/servers/{server_id}", status_code=303)

