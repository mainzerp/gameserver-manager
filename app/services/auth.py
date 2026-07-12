import json
import secrets
from datetime import datetime, timezone

import pyotp
from fastapi import HTTPException, Request
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.server_access import ServerAccess
from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ROLE_LEVELS = {"viewer": 0, "operator": 1, "admin": 2}
PERMISSION_LEVELS = {"view": 0, "operate": 1, "manage": 2}


class RedirectException(Exception):
    def __init__(self, url: str):
        self.url = url


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


async def get_current_user_dep(request: Request) -> User:
    """Dependency-safe wrapper for FastAPI Depends() -- no AsyncSession parameter."""
    return await get_current_user(request)


async def get_current_user(request: Request, db: AsyncSession | None = None) -> User:
    user_id = request.session.get("user_id")

    if db is not None:
        if not user_id:
            result = await db.execute(select(User).limit(1))
            if not result.scalars().first():
                raise RedirectException("/setup")
            raise RedirectException("/login")
        user = await db.get(User, user_id)
        if not user:
            request.session.clear()
            raise RedirectException("/login")
        return user

    async with async_session() as db:
        if not user_id:
            result = await db.execute(select(User).limit(1))
            if not result.scalars().first():
                raise RedirectException("/setup")
            raise RedirectException("/login")

        user = await db.get(User, user_id)
        if not user:
            request.session.clear()
            raise RedirectException("/login")

        return user


async def get_api_user(request: Request) -> User | None:
    from app.services.api_key_service import verify_api_key

    auth_header = request.headers.get("Authorization", "")
    api_key_header = request.headers.get("X-API-Key", "")

    raw_key = None
    if auth_header.startswith("Bearer "):
        raw_key = auth_header[7:]
    elif api_key_header:
        raw_key = api_key_header

    if raw_key:
        return await verify_api_key(raw_key)
    return None


async def get_current_user_flexible(request: Request) -> User:
    user_id = request.session.get("user_id")
    if user_id:
        async with async_session() as db:
            user = await db.get(User, user_id)
            if user:
                return user

    user = await get_api_user(request)
    if user:
        return user

    raise HTTPException(status_code=401, detail="Authentication required")


# -- TOTP helpers ----------------------------------------------------------


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_uri(secret: str, username: str) -> str:
    from app.config import settings

    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=settings.app_name)


def verify_totp(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=0)


async def verify_totp_with_replay_protection(
    user: User, code: str, db: AsyncSession
) -> bool:
    """Verify TOTP and reject replay of the last used code within the same time step."""
    if not verify_totp(user.totp_secret, code):
        return False

    import time

    now_ts = int(time.time())
    time_step = 30

    # Refresh user to get latest last_totp_used_at
    db_user = await db.get(User, user.id)
    if db_user and db_user.last_totp_used_at:
        last_ts = int(db_user.last_totp_used_at.timestamp())
        if (now_ts // time_step) == (last_ts // time_step):
            # Same time step - reject as replay
            return False

    if db_user:
        db_user.last_totp_used_at = datetime.now(timezone.utc)
        await db.commit()
    return True


def generate_recovery_codes(count: int = 8) -> list[str]:
    return [secrets.token_hex(16).upper() for _ in range(count)]


def hash_recovery_codes(codes: list[str]) -> str:
    return json.dumps([pwd_context.hash(c) for c in codes])


def verify_recovery_code(stored_json: str, code: str) -> tuple[bool, str]:
    hashes = json.loads(stored_json)
    for i, h in enumerate(hashes):
        if pwd_context.verify(code.strip().upper(), h):
            hashes.pop(i)
            return True, json.dumps(hashes)
    return False, stored_json


# -- RBAC helpers ----------------------------------------------------------


async def _get_any_user(request: Request) -> User:
    """Get user from session or API key, for RBAC checks."""
    user_id = request.session.get("user_id")
    if user_id:
        async with async_session() as db:
            user = await db.get(User, user_id)
            if user:
                return user
    user = await get_api_user(request)
    if user:
        return user
    raise HTTPException(status_code=401, detail="Authentication required")


async def require_role(request: Request, min_role: str) -> User:
    user = await _get_any_user(request)
    if ROLE_LEVELS.get(user.role, 0) < ROLE_LEVELS.get(min_role, 99):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user


async def require_server_access(
    request: Request,
    server_id: int,
    min_permission: str,
    db: AsyncSession,
) -> User:
    user = await _get_any_user(request)
    if user.role == "admin":
        return user
    result = await db.execute(
        select(ServerAccess).where(
            ServerAccess.user_id == user.id,
            ServerAccess.server_id == server_id,
        )
    )
    row = result.scalars().first()
    if not row or PERMISSION_LEVELS.get(row.permission, 0) < PERMISSION_LEVELS.get(
        min_permission, 99
    ):
        raise HTTPException(status_code=403, detail="No access to this server")
    return user


async def get_accessible_server_ids(user: User, db: AsyncSession) -> list[int] | None:
    if user.role == "admin":
        return None
    result = await db.execute(
        select(ServerAccess.server_id).where(ServerAccess.user_id == user.id)
    )
    return [row[0] for row in result.all()]
