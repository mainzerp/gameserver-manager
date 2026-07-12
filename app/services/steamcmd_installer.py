"""SteamCMD auto-download and bootstrap service."""

import asyncio
import logging
import os
import platform
import tarfile
import zipfile
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

STEAMCMD_LINUX_URL = (
    "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
)
STEAMCMD_WINDOWS_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"


class SteamCMDInstaller:
    def is_installed(self) -> bool:
        """Check if SteamCMD binary exists in the install directory."""
        install_dir = settings.steamcmd_install_dir
        if platform.system() == "Windows":
            return os.path.isfile(os.path.join(install_dir, "steamcmd.exe"))
        return os.path.isfile(os.path.join(install_dir, "steamcmd.sh"))

    async def install(self) -> bool:
        """Download and extract SteamCMD, then bootstrap it."""
        install_dir = settings.steamcmd_install_dir
        os.makedirs(install_dir, exist_ok=True)

        is_windows = platform.system() == "Windows"
        url = STEAMCMD_WINDOWS_URL if is_windows else STEAMCMD_LINUX_URL
        archive_name = "steamcmd.zip" if is_windows else "steamcmd_linux.tar.gz"
        archive_path = os.path.join(install_dir, archive_name)

        logger.info(f"Downloading SteamCMD from {url}...")
        try:
            async with httpx.AsyncClient(
                timeout=120.0, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                with open(archive_path, "wb") as f:
                    f.write(resp.content)
        except Exception as e:
            logger.error(f"Failed to download SteamCMD: {e}")
            return False

        logger.info("Extracting SteamCMD...")
        try:
            if is_windows:
                self._safe_extract_zip(archive_path, install_dir)
            else:
                with tarfile.open(archive_path, "r:gz") as tf:
                    tf.extractall(install_dir, filter="data")
        except Exception as e:
            logger.error(f"Failed to extract SteamCMD: {e}")
            return False
        finally:
            try:
                os.remove(archive_path)
            except OSError:
                pass

        # Bootstrap SteamCMD
        if is_windows:
            exe = os.path.join(install_dir, "steamcmd.exe")
        else:
            exe = os.path.join(install_dir, "steamcmd.sh")
            os.chmod(exe, 0o755)

        logger.info("Bootstrapping SteamCMD (first run)...")
        try:
            process = await asyncio.create_subprocess_exec(
                exe,
                "+quit",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            output = stdout.decode("utf-8", errors="replace")
            if process.returncode not in (0, 7):
                logger.warning(
                    f"SteamCMD bootstrap returned code {process.returncode}: {output[-500:]}"
                )
            else:
                logger.info("SteamCMD bootstrap completed successfully")
        except Exception as e:
            logger.error(f"SteamCMD bootstrap failed: {e}")
            return False

        return self.is_installed()

    @staticmethod
    def _safe_extract_zip(archive_path: str, install_dir: str) -> None:
        """Extract a zip archive with per-member path traversal validation."""
        dest = Path(install_dir).resolve()
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.infolist():
                member_path = (dest / member.filename).resolve()
                if not member_path.is_relative_to(dest):
                    raise ValueError(f"Unsafe path in zip entry: {member.filename}")
            zf.extractall(install_dir)


steamcmd_installer = SteamCMDInstaller()
