"""External backup storage: copy backups to a configured external path."""

import asyncio
import logging
import shutil
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class BackupStorage:
    async def copy_to_external(self, local_path: str, server_name: str) -> bool:
        """Copy a backup file to the configured external backup path."""
        if not settings.backup_external_path:
            return False
        try:
            dest_dir = Path(settings.backup_external_path) / server_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / Path(local_path).name
            await asyncio.to_thread(shutil.copy2, local_path, str(dest_file))
            logger.info(f"Copied backup to external storage: {dest_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to copy backup to external storage: {e}")
            return False


backup_storage = BackupStorage()
