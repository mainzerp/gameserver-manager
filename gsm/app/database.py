import asyncio
import functools
import logging
from pathlib import Path

from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from alembic import command
from app.config import settings

logger = logging.getLogger(__name__)

_db_url = settings.database_url

engine_kwargs = {"echo": settings.debug}

if _db_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["pool_size"] = 5
    engine_kwargs["max_overflow"] = 10
    engine_kwargs["pool_recycle"] = 3600
    engine_kwargs["pool_pre_ping"] = True

engine = create_async_engine(_db_url, **engine_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


_DB_RETRY_MAX = 10
_DB_RETRY_CAP = 30.0


async def init_db():
    _alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    alembic_cfg = Config(str(_alembic_ini))
    loop = asyncio.get_running_loop()

    for attempt in range(1, _DB_RETRY_MAX + 1):
        try:
            await loop.run_in_executor(None, functools.partial(command.upgrade, alembic_cfg, "head"))
            return
        except Exception:
            if attempt == _DB_RETRY_MAX:
                raise
            wait = min(2.0**attempt, _DB_RETRY_CAP)
            logger.warning(
                "Alembic migration failed (attempt %d/%d), retrying in %.0fs...",
                attempt,
                _DB_RETRY_MAX,
                wait,
            )
            await asyncio.sleep(wait)
