"""Automatic server update checking and application."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.server import Server
from app.services.backup_manager import backup_manager
from app.services.jar_downloader import download_server_jar, get_latest_version
from app.services.notification_service import notification_service
from app.services.server_manager import server_manager

logger = logging.getLogger(__name__)


class ServerUpdater:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def check_update(self, server: Server, db: AsyncSession | None = None) -> dict | None:
        """Check if a server update is available.

        Returns {"current": str, "latest": str, "update_available": bool} or None.
        """
        if server.server_type.value.startswith("minecraft"):
            if server.server_type.value == "minecraft_bedrock":
                from app.services.jar_downloader import get_latest_bedrock_version

                latest = await get_latest_bedrock_version()
                current = server.latest_known_version or server.mc_version or ""
                return {
                    "current": current,
                    "latest": latest,
                    "update_available": latest != current,
                }

            loader = server.loader or "vanilla"
            latest = await get_latest_version(loader, server.mc_version)
            if not latest:
                return None

            current = server.latest_known_version or server.mc_version or ""
            update_available = latest != current

            return {
                "current": current,
                "latest": latest,
                "update_available": update_available,
            }

        from app.models.server import ServerType

        if server.server_type == ServerType.STEAM and server.steam_app_id:
            from app.services.steamcmd import steamcmd

            creds = {}
            if db is not None and not server.steam_login_anonymous and server.steam_account_id:
                creds = await steamcmd.get_account_credentials(db, server.steam_account_id)
            remote_build_id = await steamcmd.get_remote_build_id_for_branch(
                server.steam_app_id,
                server.steam_branch or "public",
                login_anonymous=not bool(creds),
                username=creds.get("username"),
                password=creds.get("password"),
                steam_guard_code=creds.get("steam_guard_code"),
            )
            if not remote_build_id:
                return None
            current = server.steam_build_id or ""
            return {
                "current": current,
                "latest": remote_build_id,
                "local_build_id": current,
                "remote_build_id": remote_build_id,
                "update_available": remote_build_id != current,
            }

        return None

    async def update_server(
        self,
        server_id: int,
        db: AsyncSession,
        create_backup: bool = True,
        interactive: bool = False,
        operation_id: str | None = None,
    ) -> dict:
        """Stop server, optionally backup, re-download JAR, restart.

        Returns {"ok": bool, "message": str}.
        """
        server = await db.get(Server, server_id)
        if not server:
            return {"ok": False, "message": "Server not found"}

        was_running = server_manager.is_running(server_id)

        if was_running:
            logger.info(f"Stopping server {server.name} for update...")
            await server_manager.stop_server(server_id)
            import asyncio

            await asyncio.sleep(3)

        if create_backup:
            try:
                await backup_manager.create_backup(server_id, note="Pre-update backup")
                logger.info(f"Pre-update backup created for {server.name}")
            except Exception as e:
                logger.warning(
                    f"Failed to create pre-update backup for {server.name}: {e}"
                )

        # Steam server update path
        from app.models.server import ServerType

        if server.server_type == ServerType.STEAM and server.steam_app_id:
            from app.services.steamcmd import steamcmd

            kwargs, error = await steamcmd.get_server_install_kwargs(
                db, server, interactive=interactive
            )
            if error:
                msg = f"Steam update failed for {server.name}: {error}"
                logger.error(msg)
                if interactive:
                    await steamcmd.record_operation_failure(server.id, "update", msg)
                return {"ok": False, "message": msg}
            result = await steamcmd.update_server(
                app_id=server.steam_app_id,
                install_dir=server.path,
                operation_id=operation_id,
                **kwargs,
            )
            if not result["ok"]:
                msg = f"Steam update failed for {server.name}: {result['message']}"
                logger.error(msg)
                await notification_service.notify(
                    "update", f"Update Failed: {server.name}", msg, color=0xEF4444
                )
                if was_running:
                    await server_manager.start_server(server_id)
                return {"ok": False, "message": msg}

            if result.get("build_id"):
                server.steam_build_id = result["build_id"]
            server.steam_last_update = datetime.now(timezone.utc)
            await db.commit()

            if was_running:
                res = await server_manager.start_server(server_id)
                if not res["ok"]:
                    logger.warning(
                        f"Server {server.name} failed to restart after update: {res['error']}"
                    )

            msg = f"Server {server.name} updated to build {result.get('build_id', 'unknown')}"
            logger.info(msg)
            await notification_service.notify(
                "update", f"Server Updated: {server.name}", msg, color=0x22C55E
            )
            return {"ok": True, "message": msg}

        # Minecraft: Re-download JAR (removes existing server.jar first)
        dest_dir = Path(server.path)
        jar_path = dest_dir / server.executable
        if jar_path.exists():
            jar_path.unlink()

        loader = server.loader or "vanilla"
        success = await download_server_jar(
            mc_version=server.mc_version or "",
            loader=loader,
            dest_dir=dest_dir,
        )

        if not success:
            msg = f"Failed to download updated JAR for {server.name}"
            logger.error(msg)
            await notification_service.notify(
                "update", f"Update Failed: {server.name}", msg, color=0xEF4444
            )
            if was_running:
                await server_manager.start_server(server_id)
            return {"ok": False, "message": msg}

        # Update version tracking
        latest = await get_latest_version(loader, server.mc_version)
        if latest:
            server.latest_known_version = latest
        server.last_server_update = datetime.now(timezone.utc)
        await db.commit()

        if was_running:
            result = await server_manager.start_server(server_id)
            if not result["ok"]:
                logger.warning(
                    f"Server {server.name} failed to restart after update: {result['error']}"
                )

        msg = f"Server {server.name} updated successfully"
        logger.info(msg)
        await notification_service.notify(
            "update",
            f"Server Updated: {server.name}",
            f"Latest version: {latest or 'unknown'}",
            color=0x22C55E,
        )
        return {"ok": True, "message": msg}

    async def check_all_servers(self):
        """Scheduled job: check all servers with auto_update_server enabled."""
        async with async_session() as db:
            result = await db.execute(
                select(Server).where(Server.auto_update_server.is_(True))
            )
            servers = result.scalars().all()
            server_ids = [s.id for s in servers]
        for sid in server_ids:
            try:
                async with async_session() as db:
                    server = await db.get(Server, sid)
                    if not server:
                        continue
                    update_info = await self.check_update(server, db)
                    if update_info and update_info.get("update_available"):
                        server.latest_known_version = update_info.get("latest")
                        await db.commit()
                        await self.update_server(sid, db)
            except Exception as e:
                logger.error(f"Error checking updates for server {sid}: {e}")


server_updater = ServerUpdater()
