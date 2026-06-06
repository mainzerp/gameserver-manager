from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services.status_service import status_service
from app.template_utils import templates

router = APIRouter()


@router.get("/status", response_class=HTMLResponse)
async def public_status_page(request: Request, db: AsyncSession = Depends(get_db)):
    if not settings.public_status_enabled:
        raise HTTPException(status_code=404)
    servers = await status_service.get_public_status(db)
    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "servers": servers,
            "site_title": settings.app_name,
        },
    )


@router.get("/status/json")
async def public_status_json(request: Request, db: AsyncSession = Depends(get_db)):
    if not settings.public_status_enabled:
        raise HTTPException(status_code=404)
    servers = await status_service.get_public_status(db)
    return JSONResponse(servers)
