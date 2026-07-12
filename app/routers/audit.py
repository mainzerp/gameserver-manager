from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.audit_service import audit_service
from app.services.auth import get_current_user_dep, require_role
from app.template_utils import templates

router = APIRouter(dependencies=[Depends(get_current_user_dep)])


@router.get("/audit", response_class=HTMLResponse)
async def audit_log_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")
    entries, total = await audit_service.query(db, limit=50, offset=0)
    return templates.TemplateResponse(
        "audit_log.html",
        {
            "request": request,
            "entries": entries,
            "total": total,
        },
    )


@router.get("/audit/data", response_class=JSONResponse)
async def audit_log_data(
    request: Request,
    action: str = Query(None),
    resource_type: str = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    entries, total = await audit_service.query(
        db,
        action=action,
        resource_type=resource_type,
        limit=limit,
        offset=offset,
    )
    data = [
        {
            "id": e.id,
            "username": e.username,
            "action": e.action,
            "resource_type": e.resource_type,
            "resource_id": e.resource_id,
            "details": e.details,
            "ip_address": e.ip_address,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        }
        for e in entries
    ]
    return JSONResponse({"ok": True, "data": data, "total": total})
