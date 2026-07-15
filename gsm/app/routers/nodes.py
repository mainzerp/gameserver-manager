import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.node import Node
from app.models.server import Server
from app.services.auth import get_current_user_dep, require_role
from app.services.node_manager import node_manager
from app.template_utils import templates

router = APIRouter(prefix="/nodes", dependencies=[Depends(get_current_user_dep)])


@router.get("/", response_class=HTMLResponse)
async def node_list(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "admin")
    result = await db.execute(select(Node).order_by(Node.is_local.desc(), Node.name))
    nodes = result.scalars().all()

    node_server_counts: dict[int, int] = {}
    for node in nodes:
        count_result = await db.execute(select(Server).where(Server.node_id == node.id))
        node_server_counts[node.id] = len(count_result.scalars().all())

    return templates.TemplateResponse(request, "nodes.html", {
            "nodes": nodes,
            "node_server_counts": node_server_counts,
            "multi_node_enabled": settings.multi_node_enabled,
        })


@router.post("/create")
async def create_node(
    request: Request,
    name: str = Form(...),
    hostname: str = Form(...),
    api_url: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    existing = await db.execute(select(Node).where(Node.name == name))
    if existing.scalars().first():
        request.session["flash"] = f"A node named '{name}' already exists."
        return RedirectResponse(url="/nodes", status_code=303)

    auth_token = secrets.token_urlsafe(32)
    node = Node(
        name=name,
        hostname=hostname,
        api_url=api_url.rstrip("/"),
        auth_token=auth_token,
        is_local=False,
        status="unknown",
    )
    db.add(node)
    await db.commit()
    request.session["flash"] = f"Node '{name}' created. Auth token: {auth_token}"
    return RedirectResponse(url="/nodes", status_code=303)


@router.post("/{node_id}/delete")
async def delete_node(
    request: Request,
    node_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    node = await db.get(Node, node_id)
    if not node:
        return RedirectResponse(url="/nodes", status_code=303)

    if node.is_local:
        request.session["flash"] = "Cannot delete the local node."
        return RedirectResponse(url="/nodes", status_code=303)

    servers_result = await db.execute(select(Server).where(Server.node_id == node_id))
    if servers_result.scalars().first():
        request.session["flash"] = (
            "Cannot delete a node that has servers assigned to it."
        )
        return RedirectResponse(url="/nodes", status_code=303)

    await db.delete(node)
    await db.commit()
    request.session["flash"] = f"Node '{node.name}' deleted."
    return RedirectResponse(url="/nodes", status_code=303)


@router.post("/{node_id}/test")
async def test_node(
    request: Request,
    node_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "admin")
    node = await db.get(Node, node_id)
    if not node:
        return JSONResponse({"ok": False, "error": "Node not found"}, status_code=404)
    is_online = await node_manager.check_node_health(node, db)
    return JSONResponse(
        {"ok": True, "data": {"status": node.status, "online": is_online}}
    )
