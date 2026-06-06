from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.backup import Backup
from app.models.server import Server
from app.services.auth import (
    get_current_user,
    get_current_user_dep,
    require_server_access,
)
from app.services.backup_manager import backup_manager
from app.services.audit_service import audit_service, get_audit_context
from app.template_utils import templates

router = APIRouter(dependencies=[Depends(get_current_user_dep)])


@router.get("/servers/{server_id}/backups", response_class=HTMLResponse)
async def list_backups(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    backups = await backup_manager.list_backups(server_id)

    return templates.TemplateResponse(
        "backups.html",
        {
            "request": request,
            "server": server,
            "backups": backups,
        },
    )


@router.post("/servers/{server_id}/backups/create")
async def create_backup(
    request: Request,
    server_id: int,
    note: str = Form(""),
    backup_type: str = Form("full"),
    compressed: bool = Form(True),
    retention_days: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if backup_type not in ("full", "incremental", "config_only"):
        backup_type = "full"

    await backup_manager.create_backup(
        server_id,
        note=note or None,
        backup_type=backup_type,
        compressed=compressed,
        retention_days=retention_days if retention_days > 0 else None,
    )
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="backup.create",
            resource_type="backup",
            resource_id=str(server_id),
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}/backups", status_code=303)


@router.get("/servers/{server_id}/backups/estimate")
async def estimate_backup(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    estimate = await backup_manager.estimate_backup_size(server_id)
    return JSONResponse(estimate)


@router.post("/servers/{server_id}/backups/{backup_id}/restore")
async def restore_backup(
    request: Request, server_id: int, backup_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    backup = await db.get(Backup, backup_id)
    if not backup or backup.server_id != server_id:
        raise HTTPException(status_code=404, detail="Backup not found")
    result = await backup_manager.restore_backup(backup_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="backup.restore",
            resource_type="backup",
            resource_id=str(backup_id),
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}/backups", status_code=303)


@router.post("/servers/{server_id}/backups/{backup_id}/delete")
async def delete_backup(
    request: Request, server_id: int, backup_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "manage", db)
    backup = await db.get(Backup, backup_id)
    if not backup or backup.server_id != server_id:
        raise HTTPException(status_code=404, detail="Backup not found")
    result = await backup_manager.delete_backup(backup_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="backup.delete",
            resource_type="backup",
            resource_id=str(backup_id),
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}/backups", status_code=303)


@router.get("/servers/{server_id}/backups/{backup_id}/download")
async def download_backup(
    request: Request, server_id: int, backup_id: int, db: AsyncSession = Depends(get_db)
):
    await require_server_access(request, server_id, "view", db)
    import os

    backup = await db.get(Backup, backup_id)
    if not backup or backup.server_id != server_id:
        raise HTTPException(status_code=404, detail="Backup not found")

    if not os.path.exists(backup.file_path):
        raise HTTPException(status_code=404, detail="Backup file not found on disk")

    return FileResponse(
        path=backup.file_path,
        filename=backup.file_name,
        media_type="application/zip",
    )
