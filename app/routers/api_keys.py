from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.api_key import ApiKey
from app.models.user import User
from app.services.api_key_service import generate_api_key, revoke_api_key
from app.services.auth import get_current_user, get_current_user_dep
from app.services.audit_service import audit_service, get_audit_context
from app.template_utils import templates

router = APIRouter(prefix="/api-keys", dependencies=[Depends(get_current_user_dep)])


@router.get("", response_class=HTMLResponse)
async def list_api_keys(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return templates.TemplateResponse(
        "api_keys.html",
        {
            "request": request,
            "keys": keys,
            "new_key": None,
        },
    )


@router.post("/create")
async def create_api_key(
    request: Request, name: str = Form(...), db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    raw_key, api_key = await generate_api_key(user.id, name)

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="apikey.create",
            resource_type="apikey",
            details=f"name={name}",
        )
    )

    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()

    return templates.TemplateResponse(
        "api_keys.html",
        {
            "request": request,
            "keys": keys,
            "new_key": raw_key,
        },
    )


@router.post("/{key_id}/revoke")
async def revoke_key(request: Request, key_id: int, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    key = await db.get(ApiKey, key_id)
    if not key or (key.user_id != user.id and user.role != "admin"):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="API key not found")
    await revoke_api_key(key_id)
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="apikey.revoke",
            resource_type="apikey",
            resource_id=str(key_id),
        )
    )
    return RedirectResponse(url="/api-keys", status_code=303)
