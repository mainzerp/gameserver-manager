"""Import Modrinth modpacks (.mrpack files)."""

import hashlib
import json
import logging
import os
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.database import async_session
from app.models.mod import Mod
from app.models.server import Server
from app.utils.security import is_internal_url

logger = logging.getLogger(__name__)

_ALLOWED_MRPACK_DOMAINS = {
    "cdn.modrinth.com",
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "cdn.jsdelivr.net",
}


def _is_allowed_mrpack_url(url: str) -> bool:
    """Validate that a modpack download URL points to an allowed domain."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        if is_internal_url(url):
            return False
        return any(
            hostname == allowed or hostname.endswith("." + allowed)
            for allowed in _ALLOWED_MRPACK_DOMAINS
        )
    except Exception:
        return False


async def import_mrpack(server_id: int, mrpack_path: str) -> dict:
    """Import a .mrpack file into a server.

    Returns: {"mods_installed": int, "overrides_applied": int, "errors": []}
    """
    errors = []
    mods_installed = 0
    overrides_applied = 0

    try:
        with zipfile.ZipFile(mrpack_path, "r") as zf:
            if "modrinth.index.json" not in zf.namelist():
                return {
                    "mods_installed": 0,
                    "overrides_applied": 0,
                    "errors": ["Invalid .mrpack: missing modrinth.index.json"],
                }

            manifest = json.loads(zf.read("modrinth.index.json"))

            async with async_session() as session:
                server = await session.get(Server, server_id)
                if not server:
                    return {
                        "mods_installed": 0,
                        "overrides_applied": 0,
                        "errors": ["Server not found"],
                    }

                server_path = Path(server.path)
                mods_dir = server_path / "mods"
                mods_dir.mkdir(exist_ok=True)

                # Download mods from manifest
                async with httpx.AsyncClient(
                    timeout=60, follow_redirects=True
                ) as client:
                    for file_entry in manifest.get("files", []):
                        file_path = file_entry.get("path", "")
                        downloads = file_entry.get("downloads", [])
                        expected_hash = file_entry.get("hashes", {}).get("sha1")

                        if not downloads:
                            errors.append(f"No download URL for {file_path}")
                            continue

                        # Validate path to prevent directory traversal
                        normalized = os.path.normpath(file_path)
                        if normalized.startswith("..") or os.path.isabs(normalized):
                            errors.append(f"Invalid file path: {file_path}")
                            continue

                        dest = server_path / normalized
                        dest.parent.mkdir(parents=True, exist_ok=True)

                        url = downloads[0]
                        if not _is_allowed_mrpack_url(url):
                            errors.append(
                                f"Blocked download URL for {file_path}: {url}"
                            )
                            continue

                        try:
                            resp = await client.get(url)
                            resp.raise_for_status()
                            content = resp.content

                            if expected_hash:
                                actual = hashlib.sha1(content).hexdigest()
                                if actual != expected_hash:
                                    errors.append(f"Hash mismatch for {file_path}")
                                    continue

                            dest.write_bytes(content)
                            mods_installed += 1

                            # Register mod in DB if it's in mods/ folder
                            if normalized.startswith(
                                "mods" + os.sep
                            ) or normalized.startswith("mods/"):
                                mod = Mod(
                                    server_id=server_id,
                                    name=Path(file_path).stem,
                                    source="modrinth",
                                    project_id="imported",
                                    file_name=Path(file_path).name,
                                    auto_update=False,
                                )
                                session.add(mod)
                        except Exception as e:
                            errors.append(f"Failed to download {file_path}: {e}")

                # Apply overrides
                for name in zf.namelist():
                    if name.startswith("overrides/"):
                        rel = name[len("overrides/") :]
                        if not rel:
                            continue
                        # Validate path to prevent directory traversal
                        normalized_rel = os.path.normpath(rel)
                        if normalized_rel.startswith("..") or os.path.isabs(
                            normalized_rel
                        ):
                            continue
                        dest = server_path / normalized_rel
                        if name.endswith("/"):
                            dest.mkdir(parents=True, exist_ok=True)
                        else:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(zf.read(name))
                            overrides_applied += 1

                # Update loader info from manifest dependencies
                deps = manifest.get("dependencies", {})
                if "fabric-loader" in deps:
                    server.loader = "fabric"
                    server.loader_version = deps["fabric-loader"]
                elif "forge" in deps:
                    server.loader = "forge"
                    server.loader_version = deps["forge"]
                elif "neoforge" in deps:
                    server.loader = "neoforge"
                    server.loader_version = deps["neoforge"]
                elif "quilt-loader" in deps:
                    server.loader = "quilt"
                    server.loader_version = deps["quilt-loader"]
                if "minecraft" in deps:
                    server.mc_version = deps["minecraft"]

                await session.commit()

    except zipfile.BadZipFile:
        return {
            "mods_installed": 0,
            "overrides_applied": 0,
            "errors": ["File is not a valid ZIP/mrpack archive"],
        }
    except Exception as e:
        logger.error(f"Modpack import failed: {e}")
        return {"mods_installed": 0, "overrides_applied": 0, "errors": [str(e)]}

    return {
        "mods_installed": mods_installed,
        "overrides_applied": overrides_applied,
        "errors": errors,
    }


async def import_modrinth_modpack(server_id: int, project_id_or_url: str) -> dict:
    """Import a modpack from a Modrinth project URL or slug.

    Fetches the latest .mrpack from the Modrinth API, downloads it, and imports it.
    """
    from app.config import settings

    # Extract slug/id from URL if needed
    slug = project_id_or_url.strip().rstrip("/")
    if "modrinth.com" in slug:
        # URL like https://modrinth.com/modpack/slug
        parts = slug.split("/")
        slug = parts[-1] if parts else slug

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            # Get project versions
            resp = await client.get(
                f"{settings.modrinth_api_url}/project/{slug}/version",
                params={"loaders": '["fabric","forge","neoforge","quilt"]'},
            )
            resp.raise_for_status()
            versions = resp.json()

            if not versions:
                return {
                    "mods_installed": 0,
                    "overrides_applied": 0,
                    "errors": ["No versions found for this modpack"],
                }

            latest = versions[0]
            mrpack_file = None
            for f in latest.get("files", []):
                if f["filename"].endswith(".mrpack"):
                    mrpack_file = f
                    break

            if not mrpack_file:
                mrpack_file = latest["files"][0] if latest.get("files") else None

            if not mrpack_file:
                return {
                    "mods_installed": 0,
                    "overrides_applied": 0,
                    "errors": ["No .mrpack file found in latest version"],
                }

            # Download the .mrpack file
            resp = await client.get(mrpack_file["url"])
            resp.raise_for_status()

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mrpack")
            tmp.write(resp.content)
            tmp.close()

            try:
                result = await import_mrpack(server_id, tmp.name)
            finally:
                os.unlink(tmp.name)

            return result

    except httpx.HTTPStatusError as e:
        return {
            "mods_installed": 0,
            "overrides_applied": 0,
            "errors": [f"Modrinth API error: {e.response.status_code}"],
        }
    except Exception as e:
        logger.error(f"Modrinth modpack import failed: {e}")
        return {"mods_installed": 0, "overrides_applied": 0, "errors": [str(e)]}
