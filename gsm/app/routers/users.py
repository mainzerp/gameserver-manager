from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.server import Server
from app.models.server_access import ServerAccess
from app.models.user import User
from app.services.audit_service import audit_service, get_audit_context
from app.services.auth import (
    hash_password,
    require_role,
)
from app.template_utils import templates

router = APIRouter(prefix="/users")


async def _require_admin(request: Request) -> User:
    return await require_role(request, "admin")


@router.get("", response_class=HTMLResponse)
async def user_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_admin(request)
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()
    return templates.TemplateResponse(request, "users.html", {
            "users": users,
            "current_user": user,
        })


@router.post("/create")
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("viewer"),
    db: AsyncSession = Depends(get_db),
):
    await _require_admin(request)

    if role not in ("admin", "operator", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if len(username) < 3 or len(username) > 50:
        raise HTTPException(status_code=400, detail="Username must be 3-50 characters")
    if len(password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters"
        )

    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Username already exists")

    new_user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=(role == "admin"),
        role=role,
    )
    db.add(new_user)
    await db.commit()

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="user.create",
            resource_type="user",
            details=f"username={username}, role={role}",
        )
    )
    return RedirectResponse(url="/users", status_code=303)


@router.post("/{user_id}/update")
async def update_user(
    request: Request,
    user_id: int,
    role: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await _require_admin(request)
    if role not in ("admin", "operator", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target.role = role
    target.is_admin = role == "admin"
    await db.commit()

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="user.update_role",
            resource_type="user",
            resource_id=str(user_id),
            details=f"new_role={role}",
        )
    )
    return RedirectResponse(url="/users", status_code=303)


@router.post("/{user_id}/delete")
async def delete_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    current = await _require_admin(request)
    if current.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Delete associated server access entries
    from sqlalchemy import delete as sa_delete

    await db.execute(sa_delete(ServerAccess).where(ServerAccess.user_id == user_id))

    await db.delete(target)
    await db.commit()

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="user.delete",
            resource_type="user",
            resource_id=str(user_id),
            details=f"username={target.username}",
        )
    )
    return RedirectResponse(url="/users", status_code=303)


@router.get("/{user_id}/access", response_class=HTMLResponse)
async def user_access_page(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    await _require_admin(request)

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(select(Server).order_by(Server.name))
    servers = result.scalars().all()

    result = await db.execute(
        select(ServerAccess).where(ServerAccess.user_id == user_id)
    )
    access_entries = result.scalars().all()
    access_map = {a.server_id: a.permission for a in access_entries}

    return templates.TemplateResponse(request, "user_access.html", {
            "target_user": target,
            "servers": servers,
            "access_map": access_map,
        })


@router.post("/{user_id}/access")
async def update_user_access(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    await _require_admin(request)
    form = await request.form()

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Delete existing access
    result = await db.execute(
        select(ServerAccess).where(ServerAccess.user_id == user_id)
    )
    for access in result.scalars().all():
        await db.delete(access)

    # Add new access from form
    result = await db.execute(select(Server))
    servers = result.scalars().all()
    for server in servers:
        perm = form.get(f"server_{server.id}")
        if perm and perm in ("view", "operate", "manage"):
            access = ServerAccess(
                user_id=user_id,
                server_id=server.id,
                permission=perm,
            )
            db.add(access)

    await db.commit()

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="user.update_access",
            resource_type="user",
            resource_id=str(user_id),
        )
    )
    return RedirectResponse(url=f"/users/{user_id}/access", status_code=303)
