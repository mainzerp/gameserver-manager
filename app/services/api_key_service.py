import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session
from app.models.api_key import ApiKey
from app.models.user import User
from app.services.auth import pwd_context


async def generate_api_key(user_id: int, name: str) -> tuple[str, ApiKey]:
    raw_key = "gsm_" + secrets.token_urlsafe(32)
    key_hash = pwd_context.hash(raw_key)
    key_prefix = raw_key[:12]

    async with async_session() as session:
        api_key = ApiKey(
            user_id=user_id,
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            is_active=True,
        )
        session.add(api_key)
        await session.commit()
        await session.refresh(api_key)
        return raw_key, api_key


async def verify_api_key(raw_key: str) -> User | None:
    prefix = raw_key[:12]
    async with async_session() as session:
        result = await session.execute(
            select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.is_active.is_(True))
        )
        candidates = result.scalars().all()

        for candidate in candidates:
            if pwd_context.verify(raw_key, candidate.key_hash):
                candidate.last_used = datetime.now(timezone.utc)
                await session.commit()

                user = await session.get(User, candidate.user_id)
                return user
        return None


async def revoke_api_key(key_id: int):
    async with async_session() as session:
        api_key = await session.get(ApiKey, key_id)
        if api_key:
            api_key.is_active = False
            await session.commit()
