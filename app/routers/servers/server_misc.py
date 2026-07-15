import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metric import MetricSnapshot
from app.models.server import Server
from app.routers.servers._shared import get_current_user_dep, get_db, require_server_access
from app.services.audit_service import audit_service, get_audit_context
from app.services.auth import (
    get_current_user,
)
from app.services.backup_manager import backup_manager
from app.services.config_export import export_config
from app.services.port_manager import port_manager
from app.services.resource_monitor import resource_monitor
from app.services.server_manager import server_manager
from app.services.server_updater import server_updater
from app.services.status_service import status_service

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_dep)])

@router.get("/servers/{server_id}/stats")
async def server_stats(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if server_manager.is_running(server_id, db=db):
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

@router.get("/servers/{server_id}/telemetry")
async def server_telemetry(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    telemetry = await status_service.get_server_telemetry(server)
    return JSONResponse(telemetry)

@router.get("/servers/{server_id}/metrics", response_class=JSONResponse)
async def get_server_metrics(
    request: Request,
    server_id: int,
    period: str = Query("1h"),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "view", db)
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
                await server_manager.start_server(sid, db=db)
            elif action == "stop":
                await server_manager.stop_server(sid, db=db)
            elif action == "restart":
                await server_manager.stop_server(sid, db=db)
                await asyncio.sleep(2)
                await server_manager.start_server(sid, db=db)
            elif action == "backup":
                await backup_manager.create_backup(sid, db=db)
        except Exception as e:
            logger.warning(f"Bulk action {action} failed for server {sid}: {e}")

    return RedirectResponse(url="/", status_code=303)


# -- Console Improvements: Saved Commands ----------------------------------

@router.get("/servers/{server_id}/export-config", response_class=JSONResponse)
async def export_server_config(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    data = await export_config(db, server_id)
    return JSONResponse(
        data,
        headers={
            "Content-Disposition": f"attachment; filename=server_{server_id}_config.json"
        },
    )

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

