import html
import logging
import re
from datetime import datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


class SteamWorkshopService:
    async def fetch_metadata(
        self, workshop_id: str, steam_api_key: str | None = None
    ) -> dict | None:
        api_key = (steam_api_key or settings.steam_api_key or "").strip()
        if not api_key:
            return None

        url = "https://api.steampowered.com/IPublishedFileService/GetDetails/v1/"
        params = {
            "key": api_key,
            "itemcount": 1,
            "publishedfileids[0]": workshop_id,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
        except Exception as exc:
            logger.warning(
                "Failed to fetch Steam Workshop metadata for %s: %s", workshop_id, exc
            )
            return None

        try:
            payload = response.json()
        except ValueError:
            logger.warning(
                "Steam Workshop metadata response for %s was not valid JSON",
                workshop_id,
            )
            return None

        details = payload.get("response", {}).get("publishedfiledetails") or []
        if not details:
            return None
        detail = details[0] or {}
        if int(detail.get("result", 0)) not in (1, 9):
            return None

        return self._normalize_detail(detail)

    def _normalize_detail(self, detail: dict) -> dict:
        title = (detail.get("title") or "").strip() or None
        description = self._normalize_description(detail.get("file_description") or "")
        file_size = self._coerce_int(detail.get("file_size"))
        time_updated = self._coerce_timestamp(detail.get("time_updated"))
        time_created = self._coerce_timestamp(detail.get("time_created"))
        preview_url = (detail.get("preview_url") or "").strip() or None
        subscriptions = self._coerce_int(detail.get("subscriptions"))
        tags = self._normalize_tags(detail.get("tags") or [])

        return {
            "name": title,
            "description": description,
            "file_size": file_size,
            "last_updated": time_updated,
            "created_at": time_created,
            "preview_url": preview_url,
            "subscriptions": subscriptions,
            "tags": tags,
        }

    def _normalize_description(self, value: str) -> str | None:
        if not value:
            return None
        unescaped = html.unescape(value)
        without_tags = _TAG_RE.sub(" ", unescaped)
        normalized = re.sub(r"\s+", " ", without_tags).strip()
        return normalized or None

    def _coerce_int(self, value) -> int | None:
        try:
            if value in (None, ""):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _coerce_timestamp(self, value) -> datetime | None:
        parsed = self._coerce_int(value)
        if not parsed:
            return None
        return datetime.fromtimestamp(parsed, tz=timezone.utc)

    def _normalize_tags(self, values: list[dict]) -> list[str]:
        tags: list[str] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            tag = (value.get("tag") or "").strip()
            if tag:
                tags.append(tag)
        return tags


steam_workshop_service = SteamWorkshopService()
