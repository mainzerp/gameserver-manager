import asyncio

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.mod import Mod
from app.models.server import Server
from app.services.auth import (
    get_accessible_server_ids,
    get_current_user_flexible,
    require_server_access,
)
from app.services.log_manager import log_manager
from app.services.port_manager import port_manager
from app.services.resource_monitor import resource_monitor
from app.services.server_manager import server_manager
from app.services.server_templates import get_templates

router = APIRouter(prefix="/servers", dependencies=[Depends(get_current_user_flexible)])


class CommandBody(BaseModel):
    command: str


@router.get(
    "", summary="List servers", response_description="List of accessible servers"
)
async def list_servers(request: Request, db: AsyncSession = Depends(get_db)):
    """Return all servers accessible to the authenticated user."""
    user = await get_current_user_flexible(request)
    accessible_ids = await get_accessible_server_ids(user, db)
    query = select(Server)
    if accessible_ids is not None:
        query = query.where(Server.id.in_(accessible_ids))
    result = await db.execute(query)
    servers = result.scalars().all()
    data = []
    for s in servers:
        data.append(
            {
                "id": s.id,
                "name": s.name,
                "type": s.server_type.value,
                "status": s.status.value,
                "port": s.port,
                "running": server_manager.is_running(s.id),
            }
        )
    return JSONResponse({"ok": True, "data": data})


@router.get(
    "/{server_id}",
    summary="Get server details",
    response_description="Server detail object",
)
async def get_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Return full details for a single server."""
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        return JSONResponse({"ok": False, "error": "Server not found"}, status_code=404)
    return JSONResponse(
        {
            "ok": True,
            "data": {
                "id": server.id,
                "name": server.name,
                "type": server.server_type.value,
                "status": server.status.value,
                "port": server.port,
                "path": server.path,
                "mc_version": server.mc_version,
                "loader": server.loader,
                "min_memory": server.min_memory,
                "max_memory": server.max_memory,
                "auto_start": server.auto_start,
                "running": server_manager.is_running(server.id),
                "created_at": server.created_at.isoformat()
                if server.created_at
                else None,
            },
        }
    )


@router.post("/{server_id}/start", summary="Start server")
async def start_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Start a stopped server process."""
    await require_server_access(request, server_id, "operate", db)
    result = await server_manager.start_server(server_id)
    if not result["ok"]:
        return JSONResponse({"ok": False, "error": result["error"]}, status_code=400)
    return JSONResponse({"ok": True, "data": {"status": "started"}})


@router.post("/{server_id}/stop", summary="Stop server")
async def stop_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Stop a running server process."""
    await require_server_access(request, server_id, "operate", db)
    success = await server_manager.stop_server(server_id)
    if not success:
        return JSONResponse(
            {"ok": False, "error": "Failed to stop server"}, status_code=400
        )
    return JSONResponse({"ok": True, "data": {"status": "stopped"}})


@router.post("/{server_id}/restart", summary="Restart server")
async def restart_server(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Stop then start a server process."""
    await require_server_access(request, server_id, "operate", db)
    await server_manager.stop_server(server_id)
    await asyncio.sleep(2)
    result = await server_manager.start_server(server_id)
    if not result["ok"]:
        return JSONResponse({"ok": False, "error": result["error"]}, status_code=400)
    return JSONResponse({"ok": True, "data": {"status": "restarted"}})


@router.post("/{server_id}/command", summary="Send console command")
async def send_command(
    request: Request,
    server_id: int,
    body: CommandBody,
    db: AsyncSession = Depends(get_db),
):
    """Send a console command to a running server."""
    await require_server_access(request, server_id, "operate", db)
    success = await server_manager.send_command(server_id, body.command)
    if not success:
        return JSONResponse(
            {"ok": False, "error": "Server is not running"}, status_code=400
        )
    return JSONResponse({"ok": True, "data": {"sent": True}})


@router.get("/{server_id}/stats", summary="Get server resource stats")
async def server_stats(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Return CPU and RAM statistics for a running server."""
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        return JSONResponse({"ok": False, "error": "Server not found"}, status_code=404)

    if server_manager.is_running(server_id):
        sp = server_manager.processes.get(server_id)
        process_stats = None
        if sp and sp.process.pid:
            process_stats = resource_monitor.get_process_stats(sp.process.pid)
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "running": True,
                    "process": process_stats,
                    "system": resource_monitor.get_system_stats(),
                },
            }
        )
    return JSONResponse(
        {
            "ok": True,
            "data": {
                "running": False,
                "process": None,
                "system": resource_monitor.get_system_stats(),
            },
        }
    )


@router.get("/{server_id}/logs", summary="Get server logs")
async def server_logs(
    request: Request,
    server_id: int,
    lines: int = 500,
    q: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Return recent log lines or search log contents."""
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        return JSONResponse({"ok": False, "error": "Server not found"}, status_code=404)

    if q:
        results = await asyncio.to_thread(log_manager.search_logs, server.name, q, 200)
        return JSONResponse({"ok": True, "data": {"results": results}})

    log_lines = await asyncio.to_thread(log_manager.get_logs, server.name, lines)
    return JSONResponse({"ok": True, "data": {"lines": log_lines}})


@router.get("/{server_id}/mods", summary="List server mods")
async def server_mods(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Return all mods installed on a server."""
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        return JSONResponse({"ok": False, "error": "Server not found"}, status_code=404)

    result = await db.execute(select(Mod).where(Mod.server_id == server_id))
    mods = result.scalars().all()
    data = []
    for m in mods:
        data.append(
            {
                "id": m.id,
                "name": m.name,
                "installed_version": m.installed_version,
                "latest_version": m.latest_version,
                "update_available": m.update_available,
                "source": m.source,
            }
        )
    return JSONResponse({"ok": True, "data": data})


@router.get("/suggest-ports", summary="Suggest available ports")
async def suggest_ports(
    server_type: str = Query("minecraft_java"),
    db: AsyncSession = Depends(get_db),
):
    """Return suggested available game and RCON port numbers."""
    suggested = await port_manager.suggest_ports(db, server_type)
    return JSONResponse({"ok": True, "data": suggested})


@router.get("/templates", summary="List server templates")
async def list_templates():
    """Return all predefined server configuration templates."""
    return JSONResponse({"ok": True, "data": get_templates()})
