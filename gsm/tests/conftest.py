"""Shared test configuration and helpers for the gameserver test suite.

This file is additive: existing unittest-style tests are unaffected.
New tests may use the helpers below to bootstrap an in-memory SQLite
database and an ASGITransport test client.

Usage (unittest style)::

    from tests.conftest import create_test_engine, init_test_db, make_session_factory

    async def asyncSetUp(self):
        self.engine = create_test_engine()
        await init_test_db(self.engine)
        self.session_factory = make_session_factory(self.engine)
"""

import os

# Set env vars before any app imports so config defaults to test-safe values.
os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("GSM_SECRET_KEY", "test-secret-key-not-for-production")

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

import app.models  # noqa: F401 -- register all models on Base.metadata
from app.database import Base, get_db


def create_test_engine():
    """Create an in-memory SQLite async engine."""
    return create_async_engine("sqlite+aiosqlite:///:memory:")


async def init_test_db(engine):
    """Create all tables via ``Base.metadata.create_all`` on the given engine."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def make_session_factory(engine):
    """Return an ``async_sessionmaker`` bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


def make_test_app(session_factory, routers=None, extra_overrides=None):
    """Build a minimal FastAPI app with DB override for integration tests.

    Args:
        session_factory: an ``async_sessionmaker`` to back all DB sessions.
        routers: optional list of ``APIRouter`` objects to include.
        extra_overrides: optional dict of ``{dependency: override_callable}``.

    Returns:
        ``(app, transport)`` where *transport* is an ``httpx.ASGITransport``.
        Wrap it: ``httpx.AsyncClient(transport=transport, base_url="http://testserver")``
    """
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-not-for-prod")

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    if extra_overrides:
        app.dependency_overrides.update(extra_overrides)

    if routers:
        for router in routers:
            app.include_router(router)

    transport = httpx.ASGITransport(app=app)
    return app, transport
