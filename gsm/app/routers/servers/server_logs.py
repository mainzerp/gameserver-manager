import asyncio
import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.routers.servers._shared import get_current_user_dep, get_db, require_server_access
from app.services.log_manager import log_manager

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_dep)])

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

