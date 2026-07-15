from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session

# Re-export get_db for route modules that share the dependency
from app.database import get_db as get_db
from app.models.server import Server, ServerStatus, ServerType
from app.services.auth import (
    get_current_user_dep as get_current_user_dep,
)
from app.services.auth import (
    require_role as require_role,
)
from app.services.auth import (
    require_server_access as require_server_access,
)
from app.services.server_manager import server_manager
from app.services.server_updater import server_updater
from app.services.steam_workshop import steam_workshop_service
from app.services.steamcmd import steamcmd
from app.services.task_registry import task_registry

__all__ = [
    "async_session",
    "get_current_user_dep",
    "get_db",
    "require_role",
    "require_server_access",
    "spawn_background_task",
    "refresh_workshop_item_metadata",
    "run_create_steam_install",
    "run_manual_steam_validate",
    "run_background_steam_update",
    "run_background_steam_update_then_start",
    "run_workshop_install",
    "parse_player_list",
]


def spawn_background_task(coro):
    task_registry.spawn(coro)


async def refresh_workshop_item_metadata(item, db: AsyncSession) -> None:
    metadata = await steam_workshop_service.fetch_metadata(item.workshop_id)
    if not metadata:
        return
    if metadata.get("name"):
        item.name = metadata["name"]
    if metadata.get("description"):
        item.description = metadata["description"]
    if metadata.get("file_size") is not None:
        item.file_size = metadata["file_size"]
    if metadata.get("last_updated"):
        item.last_updated = metadata["last_updated"]
    await db.flush()


async def run_create_steam_install(
    server_id: int, operation_id: str | None = None
) -> None:
    async with async_session() as db:
        server = await db.get(Server, server_id)
        if (
            not server
            or server.server_type != ServerType.STEAM
            or not server.steam_app_id
        ):
            return

        steam_kwargs, steam_error = await steamcmd.get_server_install_kwargs(
            db, server, interactive=True
        )
        if steam_error:
            server.status = ServerStatus.CRASHED
            await db.commit()
            await steamcmd.record_operation_failure(server.id, "install", steam_error)
            return

        result = await steamcmd.install_server(
            app_id=server.steam_app_id,
            install_dir=server.path,
            validate=True,
            operation_type="install",
            operation_id=operation_id,
            **steam_kwargs,
        )
        if result.get("ok"):
            server.status = ServerStatus.STOPPED
            server.steam_build_id = result.get("build_id")
            server.steam_last_update = datetime.now(timezone.utc)
            app_info = steamcmd.get_app_info(server.steam_app_id)
            if app_info:
                server.executable = app_info.get("executable", server.executable)
                query_port = server.query_port or (server.port + 1)
                start_args = app_info.get("start_args", "").format(
                    port=server.port,
                    name=server.name,
                    query_port=query_port,
                )
                server.start_command = (
                    f"./{app_info['executable']} {start_args}".strip()
                )
        else:
            server.status = ServerStatus.CRASHED
        await db.commit()


async def run_manual_steam_validate(
    server_id: int, operation_id: str | None = None
) -> None:
    async with async_session() as db:
        server = await db.get(Server, server_id)
        if (
            not server
            or server.server_type != ServerType.STEAM
            or not server.steam_app_id
        ):
            return

        steam_kwargs, steam_error = await steamcmd.get_server_install_kwargs(
            db, server, interactive=True
        )
        if steam_error:
            await steamcmd.record_operation_failure(server_id, "validate", steam_error)
            return

        result = await steamcmd.validate_server(
            app_id=server.steam_app_id,
            install_dir=server.path,
            operation_type="validate",
            operation_id=operation_id,
            **steam_kwargs,
        )
        if result.get("ok") and result.get("build_id"):
            server.steam_build_id = result["build_id"]
            server.steam_last_update = datetime.now(timezone.utc)
            await db.commit()


async def run_background_steam_update(
    server_id: int, operation_id: str | None = None
) -> None:
    async with async_session() as db:
        await server_updater.update_server(
            server_id, db, interactive=True, operation_id=operation_id
        )


async def run_background_steam_update_then_start(
    server_id: int, operation_id: str
) -> None:
    async with async_session() as db:
        server = await db.get(Server, server_id)
        if (
            not server
            or server.server_type != ServerType.STEAM
            or not server.steam_app_id
        ):
            return

        steam_kwargs, steam_error = await steamcmd.get_server_install_kwargs(
            db, server, interactive=False
        )
        if steam_error:
            await steamcmd._publish_event(
                server_id=server_id,
                event_type="failed",
                operation_id=operation_id,
                operation_type="update_start",
                message=steam_error,
                percent=0.0,
                status="failed",
            )
            return

        result = await steamcmd.update_server(
            app_id=server.steam_app_id,
            install_dir=server.path,
            operation_type="update_start",
            operation_id=operation_id,
            **steam_kwargs,
        )
        if not result.get("ok"):
            return

        if result.get("build_id"):
            server.steam_build_id = result["build_id"]
            server.steam_last_update = datetime.now(timezone.utc)
            await db.commit()

        await steamcmd._publish_event(
            server_id=server_id,
            event_type="progress",
            operation_id=operation_id,
            operation_type="update_start",
            message="Steam update completed. Starting server...",
            percent=100.0,
            build_id=result.get("build_id"),
            status="running",
        )

    server_manager._reset_crash_state(server_id)
    start_result = await server_manager.start_server(server_id, skip_steam_update=True)
    if not start_result.get("ok"):
        await steamcmd._publish_event(
            server_id=server_id,
            event_type="failed",
            operation_id=operation_id,
            operation_type="update_start",
            message=start_result.get("error")
            or "Steam update completed, but the server failed to start.",
            percent=100.0,
            status="failed",
        )
        return

    await steamcmd._publish_event(
        server_id=server_id,
        event_type="completed",
        operation_id=operation_id,
        operation_type="update_start",
        message="Steam update completed. Server start requested.",
        percent=100.0,
        status="completed",
    )


async def run_workshop_install(
    server_id: int, item_id: int, operation_type: str, operation_id: str | None = None
) -> None:
    async with async_session() as db:
        server = await db.get(Server, server_id)
        if (
            not server
            or server.server_type != ServerType.STEAM
            or not server.steam_app_id
        ):
            return

        from app.models.workshop_item import WorkshopItem

        item = await db.get(WorkshopItem, item_id)
        if not item or item.server_id != server_id:
            return

        steam_kwargs, steam_error = await steamcmd.get_server_install_kwargs(
            db, server, interactive=True
        )
        if steam_error:
            await steamcmd.record_operation_failure(
                server_id,
                operation_type,
                steam_error,
                workshop_item_id=item.workshop_id,
            )
            return

        result = await steamcmd.install_workshop_item(
            app_id=item.app_id,
            workshop_id=item.workshop_id,
            install_dir=server.path,
            login_anonymous=steam_kwargs.get("login_anonymous", True),
            username=steam_kwargs.get("username"),
            password=steam_kwargs.get("password"),
            server_id=server_id,
            operation_type=operation_type,
            operation_id=operation_id,
            interactive=True,
        )
        if not result.get("ok"):
            await db.commit()
            return

        item.installed = True
        item.last_updated = datetime.now(timezone.utc)
        await refresh_workshop_item_metadata(item, db)
        await db.commit()


def parse_player_list(rcon_response: str) -> list[str]:
    """Parse Minecraft RCON 'list' response into player names."""
    # Format: "There are X of a max of Y players online: player1, player2, ..."
    if ":" in rcon_response:
        after_colon = rcon_response.split(":", 1)[1].strip()
        if after_colon:
            return [p.strip() for p in after_colon.split(",") if p.strip()]
    return []
