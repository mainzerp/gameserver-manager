import asyncio
import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import async_session, init_db
from app.middleware.csrf import CSRFMiddleware
from app.middleware.locale import LocaleMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.models.server import Server, ServerStatus
from app.routers import api_keys as api_keys_router
from app.routers import audit as audit_router
from app.routers import auth as auth_router
from app.routers import backups as backups_router
from app.routers import files, mods, servers, ws
from app.routers import health as health_router
from app.routers import invites as invites_router
from app.routers import metrics as metrics_router
from app.routers import nodes as nodes_router
from app.routers import scheduler as scheduler_router
from app.routers import site_settings as site_settings_router
from app.routers import status as status_router
from app.routers import users as users_router
from app.routers import webhooks as webhooks_router
from app.routers.api_v1 import api_router
from app.services import settings_service
from app.services.audit_service import audit_service
from app.services.auth import RedirectException
from app.services.docker_manager import docker_manager
from app.services.mod_updater import mod_updater
from app.services.node_manager import node_manager
from app.services.resource_monitor import resource_monitor
from app.services.server_manager import server_manager
from app.services.server_updater import server_updater
from app.services.sftp_server import sftp_manager
from app.services.task_scheduler import task_scheduler
from app.services.update_checker import update_checker

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting GameServer Manager...")

    _INSECURE_SECRET_KEYS = {
        "change-me-in-production",
        "replace-with-a-strong-random-secret-key-here",
    }

    if settings.secret_key in _INSECURE_SECRET_KEYS:
        logger.critical(
            "SECURITY: Using default/placeholder secret key. "
            "Set GSM_SECRET_KEY environment variable. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
        raise SystemExit(
            "Refusing to start with default secret key. Set GSM_SECRET_KEY."
        )

    await init_db()

    async with async_session() as session:
        await settings_service.load_from_db(session)

    # Auto-install SteamCMD if configured and not available
    from app.services.steamcmd import steamcmd

    if settings.steamcmd_auto_install:
        if not steamcmd.is_available:
            logger.info("SteamCMD not found, attempting auto-install...")
            available = await steamcmd.ensure_available()
            if available:
                logger.info("SteamCMD auto-installed successfully")
            else:
                logger.warning(
                    "SteamCMD auto-install failed; Steam servers will not be available"
                )
        else:
            logger.info("SteamCMD detected")

    await server_manager.recover_on_startup()

    # Auto-start servers
    async with async_session() as session:
        result = await session.execute(
            select(Server).where(
                Server.auto_start.is_(True), Server.status == ServerStatus.STOPPED
            )
        )
        auto_start_servers = result.scalars().all()
        for s in auto_start_servers:
            res = await server_manager.start_server(s.id)
            if res["ok"]:
                logger.info(f"Auto-started server {s.name}")
            else:
                logger.warning(f"Failed to auto-start server {s.name}: {res['error']}")
            await asyncio.sleep(2)

    # Schedule periodic mod update checks
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        mod_updater.check_updates,
        "interval",
        minutes=settings.mod_check_interval_minutes,
        id="mod_update_check",
    )
    scheduler.start()
    logger.info(
        f"Mod update checker scheduled every {settings.mod_check_interval_minutes} minutes"
    )

    scheduler.add_job(
        resource_monitor.collect_metrics,
        "interval",
        seconds=settings.metric_interval_seconds,
        id="metric_collection",
    )
    scheduler.add_job(
        resource_monitor.cleanup_old_metrics,
        "interval",
        hours=24,
        id="metric_cleanup",
    )
    logger.info(
        f"Metric collection scheduled every {settings.metric_interval_seconds} seconds"
    )

    scheduler.add_job(
        audit_service.cleanup,
        "interval",
        hours=24,
        id="audit_cleanup",
        kwargs={"days": 90},
    )
    logger.info("Audit log cleanup scheduled every 24 hours (retention: 90 days)")

    if settings.update_check_enabled and settings.update_repo:
        scheduler.add_job(
            update_checker.check_for_updates,
            "interval",
            hours=settings.update_check_interval_hours,
            id="update_check",
        )
        asyncio.create_task(update_checker.check_for_updates())
        logger.info(
            f"Update checker scheduled every {settings.update_check_interval_hours} hours"
        )

    app.state.scheduler = scheduler
    task_scheduler.set_scheduler(scheduler)
    await task_scheduler.load_tasks()

    # Server update check job
    scheduler.add_job(
        server_updater.check_all_servers,
        "interval",
        hours=6,
        id="server_update_check",
    )
    logger.info("Server update checker scheduled every 6 hours")

    # Node health check job
    if settings.multi_node_enabled:
        async with async_session() as session:
            await node_manager.register_local_node(session)
        scheduler.add_job(
            node_manager.check_all_nodes,
            "interval",
            minutes=1,
            id="node_health_check",
        )
        logger.info("Node health checker scheduled every 1 minute")

    # Max compatible MC version daily check
    scheduler.add_job(
        mod_updater.update_all_max_compatible_versions,
        "interval",
        hours=24,
        id="max_compat_version_check",
    )
    logger.info("Max compatible version check scheduled every 24 hours")

    # Start optional services
    await sftp_manager.start()

    yield

    # Shutdown
    await audit_service.flush()
    await sftp_manager.stop()
    if settings.docker_isolation_enabled:
        await docker_manager.close()
    await server_manager.stop_all_servers()
    logger.info("All servers stopped gracefully")
    scheduler.shutdown()
    await mod_updater.close()
    logger.info("GameServer Manager stopped.")


app = FastAPI(
    title=settings.app_name,
    description="REST API for managing and monitoring game servers",
    version=settings.version,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Servers", "description": "Server management operations"},
        {"name": "Backups", "description": "Backup creation and restoration"},
        {"name": "Schedules", "description": "Scheduled task management"},
        {"name": "System", "description": "System status and configuration"},
    ],
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(LocaleMiddleware)
app.add_middleware(
    CSRFMiddleware,
    exempt_paths=[
        "/login",
        "/setup",
        "/status",
        "/login/2fa",
        "/set-locale",
        "/metrics",
        "/api/docs",
        "/api/redoc",
        "/api/openapi.json",
        "/invite/",
        "/health",
    ],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="gsm_session",
    max_age=86400,
    same_site="lax",
    https_only=settings.ssl_enabled,
)


@app.exception_handler(RedirectException)
async def redirect_exception_handler(request: Request, exc: RedirectException):
    return RedirectResponse(url=exc.url, status_code=303)


# Routers
app.include_router(auth_router.router)
app.include_router(health_router.router)
app.include_router(servers.router)
app.include_router(mods.router)
app.include_router(files.router)
app.include_router(ws.router)
app.include_router(backups_router.router)
app.include_router(scheduler_router.router)
app.include_router(api_keys_router.router)
app.include_router(status_router.router)
app.include_router(audit_router.router)
app.include_router(users_router.router)
app.include_router(webhooks_router.router)
app.include_router(metrics_router.router)
app.include_router(nodes_router.router)
app.include_router(site_settings_router.router)
app.include_router(invites_router.router)
app.include_router(api_router)

# Static files
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
