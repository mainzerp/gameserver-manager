import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.models.mod import Mod
from app.models.server import Server, ServerType
from app.services.auth import (
    get_accessible_server_ids,
    get_current_user_flexible,
    require_server_access,
)
from app.services.log_manager import log_manager
from app.services.port_manager import port_manager
from app.services.query_protocol import steam_query
from app.services.resource_monitor import resource_monitor
from app.services.server_manager import server_manager
from app.services.server_templates import get_templates
from app.services.server_updater import server_updater
from app.services.steamcmd import steamcmd

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/servers", dependencies=[Depends(get_current_user_flexible)])


def _spawn_background_task(coro):
    asyncio.create_task(coro)



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
    """Return suggested available game, RCON, and query port numbers."""
    suggested = await port_manager.suggest_ports(db, server_type)
    return JSONResponse({"ok": True, "data": suggested})


@router.get("/templates", summary="List server templates")
async def list_templates():
    """Return all predefined server configuration templates."""
    return JSONResponse({"ok": True, "data": get_templates()})


class SteamGuardBody(BaseModel):
    operation_id: str
    steam_guard_code: str


async def _run_api_steam_validate(
    server_id: int, operation_id: str | None
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
            await steamcmd.record_operation_failure(
                server_id, "validate", steam_error
            )
            return

        result = await steamcmd.validate_server(
            app_id=server.steam_app_id,
            install_dir=server.path,
            operation_id=operation_id,
            **steam_kwargs,
        )
        if result.get("ok") and result.get("build_id"):
            server.steam_build_id = result["build_id"]
            server.steam_last_update = datetime.now(timezone.utc)
            await db.commit()


async def _run_api_steam_update(
    server_id: int, operation_id: str | None
) -> None:
    async with async_session() as db:
        server = await db.get(Server, server_id)
        if (
            not server
            or server.server_type != ServerType.STEAM
            or not server.steam_app_id
        ):
            return

        await server_updater.update_server(
            server_id,
            db,
            create_backup=False,
            interactive=True,
            operation_id=operation_id,
        )


@router.post("/{server_id}/steam/update", summary="Queue SteamCMD update")
async def api_steam_update(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Queue a SteamCMD update for a Steam server."""
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server or server.server_type != ServerType.STEAM:
        return JSONResponse({"ok": False, "error": "Steam server not found"}, status_code=404)
    if not server.steam_app_id:
        return JSONResponse(
            {"ok": False, "error": "Server has no Steam app ID"}, status_code=400
        )

    operation_id = await steamcmd.queue_operation(
        server_id, "update", f"Queued Steam update for {server.name}."
    )
    _spawn_background_task(_run_api_steam_update(server_id, operation_id))
    return JSONResponse({"ok": True, "data": {"operation_id": operation_id}})


@router.post("/{server_id}/steam/validate", summary="Queue SteamCMD validate")
async def api_steam_validate(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Queue a SteamCMD file validation for a Steam server."""
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server or server.server_type != ServerType.STEAM:
        return JSONResponse({"ok": False, "error": "Steam server not found"}, status_code=404)
    if not server.steam_app_id:
        return JSONResponse(
            {"ok": False, "error": "Server has no Steam app ID"}, status_code=400
        )

    operation_id = await steamcmd.queue_operation(
        server_id, "validate", f"Queued Steam file validation for {server.name}."
    )
    _spawn_background_task(_run_api_steam_validate(server_id, operation_id))
    return JSONResponse({"ok": True, "data": {"operation_id": operation_id}})


@router.post("/{server_id}/steam/guard", summary="Submit Steam Guard code")
async def api_steam_guard(
    request: Request,
    server_id: int,
    body: SteamGuardBody,
    db: AsyncSession = Depends(get_db),
):
    """Submit a Steam Guard code to resume a waiting SteamCMD operation."""
    await require_server_access(request, server_id, "manage", db)
    result = await steamcmd.submit_steam_guard_code(
        server_id, body.operation_id, body.steam_guard_code
    )
    status_code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status_code)


@router.get("/{server_id}/steam/status", summary="Get Steam server status")
async def api_steam_status(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Return A2S_INFO query result for a Steam server."""
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server or server.server_type != ServerType.STEAM:
        return JSONResponse({"ok": False, "error": "Steam server not found"}, status_code=404)
    if not server.query_port:
        return JSONResponse(
            {"ok": False, "error": "Server has no query port configured"}, status_code=400
        )

    info = await steam_query.query_info("127.0.0.1", server.query_port)
    if info is None:
        return JSONResponse(
            {"ok": False, "error": "A2S_INFO query failed or server not responding"},
            status_code=503,
        )
    return JSONResponse({"ok": True, "data": info})
