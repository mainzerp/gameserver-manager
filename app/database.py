from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

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


async def init_db():
    import asyncio
    import functools

    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, functools.partial(command.upgrade, alembic_cfg, "head")
    )
