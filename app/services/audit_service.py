import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self):
        self._pending_tasks: set[asyncio.Task] = set()

    def create_task(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    async def flush(self):
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)

    async def log(
        self,
        user_id: int | None,
        username: str | None,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: str | None = None,
        ip_address: str | None = None,
    ):
        try:
            async with async_session() as session:
                entry = AuditLog(
                    user_id=user_id,
                    username=username,
                    action=action,
                    resource_type=resource_type,
                    resource_id=str(resource_id) if resource_id else None,
                    details=details,
                    ip_address=ip_address,
                )
                session.add(entry)
                await session.commit()
        except Exception as e:
            logger.warning(f"Audit log failed: {e}")

    async def query(
        self,
        db: AsyncSession,
        action: str | None = None,
        user_id: int | None = None,
        resource_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditLog], int]:
        q = select(AuditLog)
        count_q = select(func.count()).select_from(AuditLog)

        if action:
            q = q.where(AuditLog.action == action)
            count_q = count_q.where(AuditLog.action == action)
        if user_id:
            q = q.where(AuditLog.user_id == user_id)
            count_q = count_q.where(AuditLog.user_id == user_id)
        if resource_type:
            q = q.where(AuditLog.resource_type == resource_type)
            count_q = count_q.where(AuditLog.resource_type == resource_type)

        total = (await db.execute(count_q)).scalar() or 0
        result = await db.execute(
            q.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit)
        )
        entries = result.scalars().all()
        return entries, total

    async def cleanup(self, days: int = 90):
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            async with async_session() as session:
                await session.execute(
                    delete(AuditLog).where(AuditLog.timestamp < cutoff)
                )
                await session.commit()
                logger.info(f"Cleaned up audit logs older than {days} days")
        except Exception as e:
            logger.warning(f"Audit cleanup failed: {e}")


def get_audit_context(request: Request) -> dict:
    user_id = request.session.get("user_id")
    username = request.session.get("username")
    ip_address = request.client.host if request.client else None
    return {
        "user_id": user_id,
        "username": username,
        "ip_address": ip_address,
    }


audit_service = AuditService()
