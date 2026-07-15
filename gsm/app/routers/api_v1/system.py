from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services.auth import get_current_user_flexible
from app.services.resource_monitor import resource_monitor
from app.services.status_service import status_service
from app.services.update_checker import update_checker

router = APIRouter(prefix="/system", dependencies=[Depends(get_current_user_flexible)])


@router.get("/stats", summary="Get system resource stats")
async def system_stats():
    """Return current CPU, RAM, and disk usage for the host system."""
    return JSONResponse({"ok": True, "data": resource_monitor.get_system_stats()})


@router.get("/version", summary="Get application version")
async def system_version():
    """Return the application name and current version."""
    return JSONResponse(
        {
            "ok": True,
            "data": {
                "app_name": settings.app_name,
                "version": settings.version,
            },
        }
    )


@router.get("/status/public", summary="Get public server status")
async def public_status_api(db: AsyncSession = Depends(get_db)):
    """Return public server status data. Only available if public_status_enabled is true."""
    from fastapi import HTTPException

    if not settings.public_status_enabled:
        raise HTTPException(status_code=404)
    servers = await status_service.get_public_status(db)
    return JSONResponse({"ok": True, "data": servers})


@router.get("/updates", summary="Get update status")
async def check_updates():
    """Return the current application update check status."""
    return JSONResponse({"ok": True, "data": update_checker.get_status()})


@router.post("/updates/check", summary="Force update check")
async def force_check():
    """Trigger an immediate check for application updates."""
    await update_checker.check_for_updates()
    return JSONResponse({"ok": True, "data": update_checker.get_status()})
