import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.backup import Backup
from app.models.server import Server
from app.services.notification_service import notification_service
from app.services.task_registry import task_registry

logger = logging.getLogger(__name__)

CONFIG_EXTENSIONS = {".properties", ".yml", ".yaml", ".json", ".toml", ".cfg"}


class BackupManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _get_backup_dir(self, server_name: str) -> Path:
        backup_dir = Path(settings.backup_dir) / server_name
        backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir

    def _parse_exclude_patterns(self, server: Server) -> list[str]:
        if server.backup_exclude_patterns:
            try:
                return json.loads(server.backup_exclude_patterns)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    async def create_backup(
        self,
        server_id: int,
        note: str | None = None,
        backup_type: str = "full",
        compressed: bool = True,
        retention_days: int | None = None,
        db: AsyncSession | None = None,
    ) -> Backup:
        if db is None:
            async with async_session() as db:
                return await self._create_backup_impl(
                    server_id, note, backup_type, compressed, retention_days, db
                )
        return await self._create_backup_impl(
            server_id, note, backup_type, compressed, retention_days, db
        )

    async def _create_backup_impl(
        self,
        server_id: int,
        note: str | None,
        backup_type: str,
        compressed: bool,
        retention_days: int | None,
        db: AsyncSession,
    ) -> Backup:
        from app.services.server_manager import server_manager

        if server_manager.is_running(server_id, db=db):
            raise ValueError("Server is running; stop it before creating a backup")

        server = await db.get(Server, server_id)
        if not server:
            raise ValueError("Server not found")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        suffix = f"_{backup_type}" if backup_type != "full" else ""
        file_name = f"{server.name}_{timestamp}{suffix}.zip"
        backup_dir = self._get_backup_dir(server.name)
        target_path = str(backup_dir / file_name)
        exclude_patterns = self._parse_exclude_patterns(server)

        if backup_type == "config_only":
            file_count = await asyncio.to_thread(
                self._create_config_zip,
                server.path,
                target_path,
                exclude_patterns,
                compressed,
            )
        elif backup_type == "incremental":
            file_count = await asyncio.to_thread(
                self._create_incremental_zip,
                server_id,
                server.path,
                target_path,
                exclude_patterns,
                compressed,
            )
        else:
            file_count = await asyncio.to_thread(
                self._create_zip,
                server.path,
                target_path,
                exclude_patterns,
                compressed,
            )

        size_bytes = os.path.getsize(target_path)

        backup = Backup(
            server_id=server_id,
            file_name=file_name,
            file_path=target_path,
            size_bytes=size_bytes,
            note=note,
            backup_type=backup_type,
            file_count=file_count,
            compressed=compressed,
            retention_days=retention_days,
        )
        db.add(backup)
        await db.commit()
        await db.refresh(backup)

        await self._enforce_max_backups(server_id, db)
        await self._enforce_retention(server_id, db)

        logger.info(
            f"Created {backup_type} backup {file_name} for server {server.name}"
            f" ({size_bytes} bytes, {file_count} files)"
        )
        task_registry.spawn(
            notification_service.notify(
                "backup",
                f"Backup Created: {server.name}",
                f"Type: {backup_type}, File: {file_name}, Size: {size_bytes} bytes",
                color=0x6366F1,
                server_id=server_id,
            )
        )

        # Copy to external storage if configured
        if settings.backup_external_path:
            from app.services.backup_storage import backup_storage

            task_registry.spawn(
                backup_storage.copy_to_external(target_path, server.name)
            )

        return backup

    def _create_zip(
        self,
        source: str,
        target: str,
        exclude_patterns: list[str] | None = None,
        compressed: bool = True,
    ) -> int:
        source_path = Path(source)
        compression = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
        file_count = 0
        with zipfile.ZipFile(target, "w", compression) as zf:
            for root, dirs, files in os.walk(source_path, followlinks=False):
                rel_root = Path(root).relative_to(source_path)
                if rel_root.parts and rel_root.parts[0] == "logs":
                    continue
                for f in files:
                    rel_path = str(rel_root / f)
                    if exclude_patterns:
                        if any(
                            fnmatch.fnmatch(rel_path, pat) for pat in exclude_patterns
                        ):
                            continue
                    file_path = Path(root) / f
                    zf.write(file_path, rel_path)
                    file_count += 1
        return file_count

    def _create_config_zip(
        self,
        source: str,
        target: str,
        exclude_patterns: list[str] | None = None,
        compressed: bool = True,
    ) -> int:
        source_path = Path(source)
        compression = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
        file_count = 0
        with zipfile.ZipFile(target, "w", compression) as zf:
            for root, dirs, files in os.walk(source_path, followlinks=False):
                rel_root = Path(root).relative_to(source_path)
                if rel_root.parts and rel_root.parts[0] == "logs":
                    continue
                for f in files:
                    if Path(f).suffix.lower() not in CONFIG_EXTENSIONS:
                        continue
                    rel_path = str(rel_root / f)
                    if exclude_patterns:
                        if any(
                            fnmatch.fnmatch(rel_path, pat) for pat in exclude_patterns
                        ):
                            continue
                    file_path = Path(root) / f
                    zf.write(file_path, rel_path)
                    file_count += 1
        return file_count

    def _get_manifest_path(self, server_id: int) -> Path:
        return Path(settings.backup_dir) / f".manifest_{server_id}.json"

    def _build_file_manifest(
        self, source: str, exclude_patterns: list[str] | None = None
    ) -> dict[str, str]:
        manifest = {}
        source_path = Path(source)
        for root, dirs, files in os.walk(source_path, followlinks=False):
            rel_root = Path(root).relative_to(source_path)
            if rel_root.parts and rel_root.parts[0] == "logs":
                continue
            for f in files:
                rel_path = str(rel_root / f)
                if exclude_patterns:
                    if any(fnmatch.fnmatch(rel_path, pat) for pat in exclude_patterns):
                        continue
                file_path = Path(root) / f
                try:
                    h = hashlib.md5()
                    with open(file_path, "rb") as fh:
                        for chunk in iter(lambda: fh.read(8192), b""):
                            h.update(chunk)
                    manifest[rel_path] = h.hexdigest()
                except OSError as e:
                    logger.warning(
                        f"Backup manifest: could not read '{file_path}', "
                        f"skipping (will be included in next backup): {e}"
                    )
        return manifest

    def _create_incremental_zip(
        self,
        server_id: int,
        source: str,
        target: str,
        exclude_patterns: list[str] | None = None,
        compressed: bool = True,
    ) -> int:
        manifest_path = self._get_manifest_path(server_id)
        old_manifest: dict[str, str] = {}
        if manifest_path.exists():
            try:
                old_manifest = json.loads(manifest_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        new_manifest = self._build_file_manifest(source, exclude_patterns)
        changed_files = [
            rel_path
            for rel_path, h in new_manifest.items()
            if old_manifest.get(rel_path) != h
        ]

        source_path = Path(source)
        compression = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
        file_count = 0
        with zipfile.ZipFile(target, "w", compression) as zf:
            for rel_path in changed_files:
                file_path = source_path / rel_path
                if file_path.exists():
                    zf.write(file_path, rel_path)
                    file_count += 1

        # Save updated manifest
        try:
            manifest_path.write_text(json.dumps(new_manifest))
        except OSError as e:
            logger.warning(f"Failed to write backup manifest: {e}")

        return file_count

    async def estimate_backup_size(self, server_id: int) -> dict:
        async with async_session() as session:
            server = await session.get(Server, server_id)
            if not server:
                raise ValueError("Server not found")
            exclude_patterns = self._parse_exclude_patterns(server)

            total_size = 0
            file_count = 0
            source_path = Path(server.path)
            for root, dirs, files in os.walk(source_path):
                rel_root = Path(root).relative_to(source_path)
                if rel_root.parts and rel_root.parts[0] == "logs":
                    continue
                for f in files:
                    rel_path = str(rel_root / f)
                    if exclude_patterns:
                        if any(
                            fnmatch.fnmatch(rel_path, pat) for pat in exclude_patterns
                        ):
                            continue
                    try:
                        total_size += (Path(root) / f).stat().st_size
                    except OSError:
                        pass
                    file_count += 1
            return {"size_bytes": total_size, "file_count": file_count}

    async def restore_backup(
        self, backup_id: int, db: AsyncSession | None = None
    ) -> dict:
        if db is None:
            async with async_session() as db:
                return await self._restore_backup_impl(backup_id, db)
        return await self._restore_backup_impl(backup_id, db)

    async def _restore_backup_impl(
        self, backup_id: int, db: AsyncSession
    ) -> dict:
        from app.services.server_manager import server_manager

        backup = await db.get(Backup, backup_id)
        if not backup:
            return {"ok": False, "error": "Backup not found"}

        server = await db.get(Server, backup.server_id)
        if not server:
            return {"ok": False, "error": "Server not found"}

        if settings.docker_isolation_enabled and server.container_id:
            from app.services.docker_manager import docker_manager

            if await docker_manager.is_running(server.container_id):
                return {
                    "ok": False,
                    "error": "Server must be stopped before restoring a backup",
                }

        if server_manager.is_running(server.id, db=db):
            return {
                "ok": False,
                "error": "Server must be stopped before restoring a backup",
            }

        if not os.path.exists(backup.file_path):
            return {"ok": False, "error": "Backup file not found on disk"}

        await asyncio.to_thread(self._extract_zip, backup.file_path, server.path)
        logger.info(f"Restored backup {backup.file_name} to {server.path}")
        return {"ok": True, "error": None}

    def _extract_zip(self, zip_path: str, target_dir: str):
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                target = (Path(target_dir) / member).resolve()
                if not str(target).startswith(str(Path(target_dir).resolve())):
                    raise ValueError(f"Unsafe path in zip: {member}")
            for member in zf.namelist():
                zf.extract(member, target_dir)

    async def delete_backup(
        self, backup_id: int, db: AsyncSession | None = None
    ) -> dict:
        if db is None:
            async with async_session() as db:
                return await self._delete_backup_impl(backup_id, db)
        return await self._delete_backup_impl(backup_id, db)

    async def _delete_backup_impl(
        self, backup_id: int, db: AsyncSession
    ) -> dict:
        backup = await db.get(Backup, backup_id)
        if not backup:
            return {"ok": False, "error": "Backup not found"}

        if os.path.exists(backup.file_path):
            os.remove(backup.file_path)

        await db.delete(backup)
        await db.commit()
        logger.info(f"Deleted backup {backup.file_name}")
        return {"ok": True, "error": None}

    async def list_backups(self, server_id: int) -> list[Backup]:
        async with async_session() as session:
            result = await session.execute(
                select(Backup)
                .where(Backup.server_id == server_id)
                .order_by(Backup.created_at.desc())
            )
            return list(result.scalars().all())

    async def _enforce_max_backups(self, server_id: int, db: AsyncSession):
        server = await db.get(Server, server_id)
        max_count = settings.max_backups_per_server
        if server and server.max_backups is not None:
            max_count = server.max_backups

        result = await db.execute(
            select(Backup)
            .where(Backup.server_id == server_id)
            .order_by(Backup.created_at.desc())
        )
        backups = list(result.scalars().all())
        if len(backups) > max_count:
            for old_backup in backups[max_count:]:
                if os.path.exists(old_backup.file_path):
                    os.remove(old_backup.file_path)
                await db.delete(old_backup)
            await db.commit()

    async def _enforce_retention(self, server_id: int, db: AsyncSession):
        result = await db.execute(
            select(Backup)
            .where(Backup.server_id == server_id)
            .order_by(Backup.created_at.asc())
        )
        backups = list(result.scalars().all())
        now = datetime.now(timezone.utc)
        deleted = 0
        for backup in backups:
            if backup.retention_days and backup.created_at:
                expiry = backup.created_at + timedelta(days=backup.retention_days)
                if now > expiry:
                    if os.path.exists(backup.file_path):
                        os.remove(backup.file_path)
                    await db.delete(backup)
                    deleted += 1
        if deleted:
            await db.commit()
            logger.info(
                f"Retention policy: deleted {deleted} expired backup(s) for server {server_id}"
            )


backup_manager = BackupManager()
