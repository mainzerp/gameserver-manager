import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invite_link import InviteLink
from app.models.server_access import ServerAccess


class InviteService:
    async def create_invite(
        self,
        db: AsyncSession,
        created_by: int,
        server_id: int | None = None,
        role: str = "viewer",
        max_uses: int | None = None,
        expires_at: datetime | None = None,
    ) -> InviteLink:
        if role not in ("viewer", "operator", "manage"):
            raise ValueError("Invalid role")
        invite = InviteLink(
            code=secrets.token_urlsafe(16),
            created_by=created_by,
            server_id=server_id,
            role=role,
            max_uses=max_uses,
            expires_at=expires_at,
        )
        db.add(invite)
        await db.commit()
        await db.refresh(invite)
        return invite

    async def list_invites(
        self, db: AsyncSession, server_id: int | None = None
    ) -> list[InviteLink]:
        query = select(InviteLink).order_by(InviteLink.created_at.desc())
        if server_id is not None:
            query = query.where(InviteLink.server_id == server_id)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def revoke_invite(self, db: AsyncSession, invite_id: int) -> bool:
        invite = await db.get(InviteLink, invite_id)
        if not invite:
            return False
        invite.is_active = False
        await db.commit()
        return True

    async def redeem_invite(self, db: AsyncSession, code: str, user_id: int) -> dict:
        result = await db.execute(
            select(InviteLink).where(
                InviteLink.code == code, InviteLink.is_active == True
            )
        )
        invite = result.scalars().first()
        if not invite:
            return {"ok": False, "error": "Invalid or revoked invite link"}

        if invite.expires_at and invite.expires_at < datetime.now(timezone.utc):
            return {"ok": False, "error": "Invite link has expired"}

        if invite.max_uses is not None and invite.uses >= invite.max_uses:
            return {"ok": False, "error": "Invite link has reached maximum uses"}

        if invite.server_id:
            # Check if user already has access
            existing = await db.execute(
                select(ServerAccess).where(
                    ServerAccess.user_id == user_id,
                    ServerAccess.server_id == invite.server_id,
                )
            )
            if not existing.scalars().first():
                access = ServerAccess(
                    user_id=user_id,
                    server_id=invite.server_id,
                    permission=invite.role,
                )
                db.add(access)

        invite.uses += 1
        if invite.max_uses is not None and invite.uses >= invite.max_uses:
            invite.is_active = False

        await db.commit()
        return {"ok": True, "server_id": invite.server_id}


invite_service = InviteService()
