from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.database import get_db
from app.models.scheduled_task import ScheduledTask, TaskType
from app.models.server import Server
from app.services.audit_service import audit_service, get_audit_context
from app.services.auth import (
    get_current_user_dep,
    require_role,
    require_server_access,
)
from app.services.task_registry import task_registry
from app.services.task_scheduler import task_scheduler
from app.template_utils import templates

router = APIRouter(prefix="/scheduler", dependencies=[Depends(get_current_user_dep)])


@router.get("", response_class=HTMLResponse)
async def scheduler_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_role(request, "operator")
    result = await db.execute(
        select(ScheduledTask).order_by(ScheduledTask.created_at.desc())
    )
    tasks = result.scalars().all()

    result = await db.execute(select(Server))
    servers = result.scalars().all()
    server_map = {s.id: s.name for s in servers}

    return templates.TemplateResponse(request, "scheduler.html", {
            "tasks": tasks,
            "servers": servers,
            "server_map": server_map,
            "task_types": [t.value for t in TaskType],
        })


@router.post("/create")
async def create_task(
    request: Request,
    name: str = Form(...),
    server_id: int = Form(...),
    task_type: str = Form(...),
    cron_expression: str = Form(...),
    command: str = Form(""),
    condition: str = Form("always"),
    db: AsyncSession = Depends(get_db),
):
    await require_role(request, "operator")
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=400, detail="Server not found")
    if not task_scheduler.validate_cron(cron_expression):
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    try:
        tt = TaskType(task_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid task type")

    task = ScheduledTask(
        name=name,
        server_id=server_id,
        task_type=tt,
        cron_expression=cron_expression,
        command=command if tt == TaskType.COMMAND else None,
        condition=condition if condition != "always" else None,
        enabled=True,
    )
    await task_scheduler.add_task(task)
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="task.create",
            resource_type="task",
            resource_id=str(task.id),
            details=f"name={name}, type={task_type}",
        )
    )
    return RedirectResponse(url="/scheduler", status_code=303)


@router.post("/{task_id}/run")
async def run_task_now(
    request: Request, task_id: int, db: AsyncSession = Depends(get_db)
):
    await require_role(request, "operator")
    task = await db.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task_registry.spawn(task_scheduler.run_task_now(task_id))
    return RedirectResponse(url="/scheduler", status_code=303)


@router.post("/{task_id}/toggle")
async def toggle_task(request: Request, task_id: int):
    await require_role(request, "operator")
    await task_scheduler.toggle_task(task_id)
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="task.update",
            resource_type="task",
            resource_id=str(task_id),
            details="toggled",
        )
    )
    return RedirectResponse(url="/scheduler", status_code=303)


@router.post("/{task_id}/delete")
async def delete_task(request: Request, task_id: int):
    await require_role(request, "operator")
    await task_scheduler.remove_task(task_id)
    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="task.delete",
            resource_type="task",
            resource_id=str(task_id),
        )
    )
    return RedirectResponse(url="/scheduler", status_code=303)


# ---- Per-server scheduler endpoints ----


@router.get("/server/{server_id}", response_class=JSONResponse)
async def server_scheduler_list(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "operate", db)
    result = await db.execute(
        select(ScheduledTask)
        .where(ScheduledTask.server_id == server_id)
        .order_by(ScheduledTask.created_at.desc())
    )
    tasks = result.scalars().all()
    return JSONResponse(
        content={
            "tasks": [
                {
                    "id": t.id,
                    "name": t.name,
                    "task_type": t.task_type.value,
                    "cron_expression": t.cron_expression,
                    "command": t.command,
                    "enabled": t.enabled,
                    "last_run": t.last_run.isoformat() if t.last_run else None,
                    "next_run": t.next_run.isoformat() if t.next_run else None,
                }
                for t in tasks
            ]
        }
    )


@router.post("/server/{server_id}/create")
async def server_create_task(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    task_type: str = Form(...),
    cron_expression: str = Form(...),
    command: str = Form(""),
    condition: str = Form("always"),
):
    await require_server_access(request, server_id, "operate", db)

    if not task_scheduler.validate_cron(cron_expression):
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    try:
        tt = TaskType(task_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid task type")

    task = ScheduledTask(
        name=name,
        server_id=server_id,
        task_type=tt,
        cron_expression=cron_expression,
        command=command if tt == TaskType.COMMAND else None,
        condition=condition if condition != "always" else None,
        enabled=True,
    )
    await task_scheduler.add_task(task)

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="task.create",
            resource_type="task",
            resource_id=str(task.id),
            details=f"name={name}, type={task_type}, server={server_id}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}?tab=scheduler", status_code=303)


@router.post("/server/{server_id}/{task_id}/toggle")
async def server_toggle_task(
    request: Request,
    server_id: int,
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "operate", db)

    task = await db.get(ScheduledTask, task_id)
    if not task or task.server_id != server_id:
        raise HTTPException(status_code=404, detail="Task not found")

    await task_scheduler.toggle_task(task_id)

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="task.update",
            resource_type="task",
            resource_id=str(task_id),
            details=f"toggled, server={server_id}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}?tab=scheduler", status_code=303)


@router.post("/server/{server_id}/{task_id}/delete")
async def server_delete_task(
    request: Request,
    server_id: int,
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "operate", db)

    task = await db.get(ScheduledTask, task_id)
    if not task or task.server_id != server_id:
        raise HTTPException(status_code=404, detail="Task not found")

    await task_scheduler.remove_task(task_id)

    ctx = get_audit_context(request)
    audit_service.create_task(
        audit_service.log(
            **ctx,
            action="task.delete",
            resource_type="task",
            resource_id=str(task_id),
            details=f"server={server_id}",
        )
    )
    return RedirectResponse(url=f"/servers/{server_id}?tab=scheduler", status_code=303)


@router.post("/server/{server_id}/{task_id}/run")
async def server_run_task_now(
    request: Request,
    server_id: int,
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    await require_server_access(request, server_id, "operate", db)
    task = await db.get(ScheduledTask, task_id)
    if not task or task.server_id != server_id:
        raise HTTPException(status_code=404, detail="Task not found")
    task_registry.spawn(task_scheduler.run_task_now(task_id))
    return RedirectResponse(url=f"/servers/{server_id}?tab=scheduler", status_code=303)
