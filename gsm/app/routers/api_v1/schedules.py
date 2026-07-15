from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.scheduled_task import ScheduledTask, TaskType
from app.services.auth import get_current_user_flexible, require_role
from app.services.task_scheduler import task_scheduler

router = APIRouter(
    prefix="/scheduler/tasks", dependencies=[Depends(get_current_user_flexible)]
)


class TaskCreate(BaseModel):
    name: str
    server_id: int
    task_type: str
    cron_expression: str
    command: str | None = None


class TaskUpdate(BaseModel):
    name: str | None = None
    cron_expression: str | None = None
    command: str | None = None
    enabled: bool | None = None


@router.get("", summary="List scheduled tasks")
async def list_tasks(request: Request, db: AsyncSession = Depends(get_db)):
    """Return all scheduled tasks. Requires operator role."""
    await require_role(request, "operator")
    result = await db.execute(
        select(ScheduledTask).order_by(ScheduledTask.created_at.desc())
    )
    tasks = result.scalars().all()
    data = []
    for t in tasks:
        data.append(
            {
                "id": t.id,
                "name": t.name,
                "server_id": t.server_id,
                "task_type": t.task_type.value,
                "cron_expression": t.cron_expression,
                "command": t.command,
                "enabled": t.enabled,
                "last_run": t.last_run.isoformat() if t.last_run else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
        )
    return JSONResponse({"ok": True, "data": data})


@router.post("", summary="Create scheduled task")
async def create_task(request: Request, body: TaskCreate):
    """Create a new scheduled task with a cron expression. Requires operator role."""
    await require_role(request, "operator")
    if not task_scheduler.validate_cron(body.cron_expression):
        return JSONResponse(
            {"ok": False, "error": "Invalid cron expression"}, status_code=400
        )

    try:
        tt = TaskType(body.task_type)
    except ValueError:
        return JSONResponse(
            {"ok": False, "error": "Invalid task type"}, status_code=400
        )

    task = ScheduledTask(
        name=body.name,
        server_id=body.server_id,
        task_type=tt,
        cron_expression=body.cron_expression,
        command=body.command if tt == TaskType.COMMAND else None,
        enabled=True,
    )
    task = await task_scheduler.add_task(task)
    return JSONResponse({"ok": True, "data": {"id": task.id, "name": task.name}})


@router.delete("/{task_id}", summary="Delete scheduled task")
async def delete_task(request: Request, task_id: int):
    """Delete a scheduled task by ID. Requires operator role."""
    await require_role(request, "operator")
    await task_scheduler.remove_task(task_id)
    return JSONResponse({"ok": True, "data": {"deleted": True}})


@router.post("/{task_id}/toggle", summary="Toggle task enabled state")
async def toggle_task(request: Request, task_id: int):
    """Enable or disable a scheduled task. Requires operator role."""
    await require_role(request, "operator")
    await task_scheduler.toggle_task(task_id)
    return JSONResponse({"ok": True, "data": {"toggled": True}})
