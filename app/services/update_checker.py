import logging
from datetime import datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class UpdateChecker:
    def __init__(self):
        self.latest_version: str | None = None
        self.current_version: str = self._read_current_version()
        self.update_available: bool = False
        self.release_url: str | None = None
        self.last_checked: datetime | None = None

    def _read_current_version(self) -> str:
        try:
            with open("VERSION.md", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("## ") and any(c.isdigit() for c in line):
                        ver = line.lstrip("# ").strip()
                        if ver and ver[0].isdigit():
                            return ver.split()[0]
        except Exception:
            pass
        return "1.0.0"

    async def check_for_updates(self):
        if not settings.update_check_enabled or not settings.update_repo:
            return
        try:
            url = f"https://api.github.com/repos/{settings.update_repo}/releases/latest"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url, headers={"Accept": "application/vnd.github+json"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tag = data.get("tag_name", "").lstrip("v")
                    self.latest_version = tag
                    self.release_url = data.get("html_url")
                    self.update_available = self._compare_versions(
                        tag, self.current_version
                    )
                    self.last_checked = datetime.now(timezone.utc)
        except Exception as e:
            logger.warning(f"Update check failed: {e}")

    def _compare_versions(self, latest: str, current: str) -> bool:
        try:
            latest_parts = tuple(int(x) for x in latest.split(".")[:3])
            current_parts = tuple(int(x) for x in current.split(".")[:3])
            return latest_parts > current_parts
        except (ValueError, IndexError):
            return False

    def get_status(self) -> dict:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "release_url": self.release_url,
            "last_checked": self.last_checked.isoformat()
            if self.last_checked
            else None,
        }


update_checker = UpdateChecker()
