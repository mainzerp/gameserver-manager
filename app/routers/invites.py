from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.invite_link import InviteLink
from app.services.auth import (
    get_current_user,
    get_current_user_dep,
    require_role,
    require_server_access,
)
from app.services.invite_service import invite_service
from app.template_utils import templates

router = APIRouter(dependencies=[Depends(get_current_user_dep)])


@router.post("/servers/{server_id}/invite", response_class=JSONResponse)
async def create_invite(
    request: Request,
    server_id: int,
    role: str = Form("viewer"),
    max_uses: str = Form(""),
    hours_valid: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    user = await get_current_user(request, db)

    expires_at = None
    if hours_valid and hours_valid.strip():
        try:
            hours = int(hours_valid)
            if 1 <= hours <= 8760:
                from datetime import timedelta

                expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        except (ValueError, TypeError):
            pass

    parsed_max_uses = None
    if max_uses and max_uses.strip():
        try:
            parsed_max_uses = int(max_uses)
            if parsed_max_uses < 1:
                parsed_max_uses = None
        except (ValueError, TypeError):
            pass

    invite = await invite_service.create_invite(
        db,
        created_by=user.id,
        server_id=server_id,
        role=role,
        max_uses=parsed_max_uses,
        expires_at=expires_at,
    )
    invite_url = f"{request.base_url}invite/{invite.code}"
    return JSONResponse(
        {
            "ok": True,
            "url": invite_url,
            "code": invite.code,
            "id": invite.id,
        }
    )


@router.get("/servers/{server_id}/invites", response_class=JSONResponse)
async def list_invites(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    invites = await invite_service.list_invites(db, server_id=server_id)
    return JSONResponse(
        [
            {
                "id": inv.id,
                "code": inv.code,
                "role": inv.role,
                "max_uses": inv.max_uses,
                "uses": inv.uses,
                "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
                "is_active": inv.is_active,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
            }
            for inv in invites
        ]
    )


@router.post(
    "/servers/{server_id}/invites/{invite_id}/revoke", response_class=JSONResponse
)
async def revoke_invite(
    request: Request,
    server_id: int,
    invite_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "manage", db)
    ok = await invite_service.revoke_invite(db, invite_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Invite not found")
    return JSONResponse({"ok": True})


@router.get("/invite/{code}", response_class=HTMLResponse)
async def redeem_invite_page(request: Request, code: str):
    return templates.TemplateResponse(
        "invite_redeem.html",
        {
            "request": request,
            "code": code,
        },
    )


@router.post("/invite/{code}")
async def redeem_invite(
    request: Request,
    code: str,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    result = await invite_service.redeem_invite(db, code, user.id)
    if result["ok"]:
        if result.get("server_id"):
            return RedirectResponse(
                url=f"/servers/{result['server_id']}", status_code=303
            )
        return RedirectResponse(url="/", status_code=303)
    raise HTTPException(status_code=400, detail=result["error"])
