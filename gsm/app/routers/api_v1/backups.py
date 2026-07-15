from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.server import Server
from app.services.auth import get_current_user_flexible, require_server_access
from app.services.backup_manager import backup_manager

router = APIRouter(
    prefix="/servers/{server_id}/backups",
    dependencies=[Depends(get_current_user_flexible)],
)


class BackupCreate(BaseModel):
    note: str | None = None


@router.get("", summary="List backups")
async def list_backups(
    request: Request, server_id: int, db: AsyncSession = Depends(get_db)
):
    """Return all backups for a server."""
    await require_server_access(request, server_id, "view", db)
    server = await db.get(Server, server_id)
    if not server:
        return JSONResponse({"ok": False, "error": "Server not found"}, status_code=404)

    backups = await backup_manager.list_backups(server_id)
    data = []
    for b in backups:
        data.append(
            {
                "id": b.id,
                "file_name": b.file_name,
                "size_bytes": b.size_bytes,
                "note": b.note,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
        )
    return JSONResponse({"ok": True, "data": data})


@router.post("", summary="Create backup")
async def create_backup(
    request: Request,
    server_id: int,
    body: BackupCreate = BackupCreate(),
    db: AsyncSession = Depends(get_db),
):
    """Trigger a new backup for a server."""
    await require_server_access(request, server_id, "manage", db)
    try:
        backup = await backup_manager.create_backup(server_id, note=body.note)
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "id": backup.id,
                    "file_name": backup.file_name,
                    "size_bytes": backup.size_bytes,
                },
            }
        )
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/{backup_id}/restore", summary="Restore backup")
async def restore_backup(
    request: Request, server_id: int, backup_id: int, db: AsyncSession = Depends(get_db)
):
    """Restore a server from a specific backup."""
    await require_server_access(request, server_id, "manage", db)
    from app.models.backup import Backup

    backup = await db.get(Backup, backup_id)
    if not backup or backup.server_id != server_id:
        return JSONResponse({"ok": False, "error": "Backup not found"}, status_code=404)
    result = await backup_manager.restore_backup(backup_id)
    if not result["ok"]:
        return JSONResponse({"ok": False, "error": result["error"]}, status_code=400)
    return JSONResponse({"ok": True, "data": {"restored": True}})


@router.delete("/{backup_id}", summary="Delete backup")
async def delete_backup(
    request: Request, server_id: int, backup_id: int, db: AsyncSession = Depends(get_db)
):
    """Permanently delete a backup file and record."""
    await require_server_access(request, server_id, "manage", db)
    from app.models.backup import Backup

    backup = await db.get(Backup, backup_id)
    if not backup or backup.server_id != server_id:
        return JSONResponse({"ok": False, "error": "Backup not found"}, status_code=404)
    result = await backup_manager.delete_backup(backup_id)
    if not result["ok"]:
        return JSONResponse({"ok": False, "error": result["error"]}, status_code=400)
    return JSONResponse({"ok": True, "data": {"deleted": True}})
