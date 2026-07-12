import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.mod import Mod
from app.models.server import Server

logger = logging.getLogger(__name__)


class ModUpdater:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._conflict_cache: dict[int, tuple[datetime, list[dict]]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"User-Agent": "GameServerManager/1.0 (contact@example.com)"},
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Modrinth API ──────────────────────────────────────────────

    async def search_modrinth(
        self, query: str, game_version: str | None = None, loader: str | None = None
    ) -> list[dict]:
        client = await self._get_client()
        params = {"query": query, "limit": 20, "facets": []}

        facets = []
        if game_version:
            facets.append(f'["versions:{game_version}"]')
        if loader:
            facets.append(f'["categories:{loader}"]')
        if facets:
            params["facets"] = "[" + ",".join(facets) + "]"

        resp = await client.get(f"{settings.modrinth_api_url}/search", params=params)
        resp.raise_for_status()
        return resp.json().get("hits", [])

    async def get_modrinth_versions(
        self,
        project_id: str,
        game_version: str | None = None,
        loader: str | None = None,
    ) -> list[dict]:
        client = await self._get_client()
        params = {}
        if game_version:
            params["game_versions"] = f'["{game_version}"]'
        if loader:
            params["loaders"] = f'["{loader}"]'

        resp = await client.get(
            f"{settings.modrinth_api_url}/project/{project_id}/version",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_modrinth_project(self, project_id: str) -> dict:
        client = await self._get_client()
        resp = await client.get(f"{settings.modrinth_api_url}/project/{project_id}")
        resp.raise_for_status()
        return resp.json()

    # ── Conflict Detection ─────────────────────────────────────

    async def check_conflicts(self, server_id: int, db: AsyncSession) -> list[dict]:
        now = datetime.now(timezone.utc)
        if server_id in self._conflict_cache:
            cached_time, cached_data = self._conflict_cache[server_id]
            if (now - cached_time).total_seconds() < 3600:
                return cached_data

        result = await db.execute(select(Mod).where(Mod.server_id == server_id))
        mods = result.scalars().all()

        modrinth_mods = [m for m in mods if m.source == "modrinth" and m.version_id]
        if not modrinth_mods:
            self._conflict_cache[server_id] = (now, [])
            return []

        installed_ids = {m.project_id for m in modrinth_mods}
        conflicts = []

        try:
            client = await self._get_client()
            version_ids = [m.version_id for m in modrinth_mods if m.version_id]
            resp = await client.get(
                f"{settings.modrinth_api_url}/versions",
                params={"ids": json.dumps(version_ids)},
            )
            if resp.status_code != 200:
                self._conflict_cache[server_id] = (now, [])
                return []
            all_versions = {v["id"]: v for v in resp.json()}
        except Exception as e:
            logger.debug(f"Error fetching versions from Modrinth: {e}")
            self._conflict_cache[server_id] = (now, [])
            return []

        for mod in modrinth_mods:
            version_data = all_versions.get(mod.version_id, {})
            for dep in version_data.get("dependencies", []):
                if dep.get("dependency_type") == "incompatible":
                    dep_project = dep.get("project_id")
                    if dep_project and dep_project in installed_ids:
                        conflict_mod = next(
                            (m for m in modrinth_mods if m.project_id == dep_project),
                            None,
                        )
                        if conflict_mod:
                            conflicts.append(
                                {
                                    "mod_name": mod.name,
                                    "conflicts_with": conflict_mod.name,
                                    "reason": "Marked as incompatible on Modrinth",
                                }
                            )

        self._conflict_cache[server_id] = (now, conflicts)
        return conflicts

    # ── Dependency Resolution ────────────────────────────────────

    async def _resolve_dependencies(
        self,
        project_id: str,
        mc_version: str,
        loader: str,
        installed_project_ids: set[str],
        resolved: set[str],
        depth: int = 0,
    ) -> list[dict]:
        if depth > 10:
            return []
        if project_id in resolved or project_id in installed_project_ids:
            return []

        resolved.add(project_id)
        install_list = []

        try:
            versions = await self.get_modrinth_versions(project_id, mc_version, loader)
            if not versions:
                return []
            latest = versions[0]
            for dep in latest.get("dependencies", []):
                if dep.get("dependency_type") != "required":
                    continue
                dep_project = dep.get("project_id")
                if not dep_project:
                    continue
                sub_deps = await self._resolve_dependencies(
                    dep_project,
                    mc_version,
                    loader,
                    installed_project_ids,
                    resolved,
                    depth + 1,
                )
                install_list.extend(sub_deps)

            project = await self.get_modrinth_project(project_id)
            install_list.append(
                {
                    "project_id": project_id,
                    "version_id": latest["id"],
                    "name": project.get("title", project_id),
                }
            )
        except Exception as e:
            logger.debug(f"Error resolving dependencies for {project_id}: {e}")

        return install_list

    # ── Install & Update ──────────────────────────────────────────

    async def install_mod(
        self, server_id: int, project_id: str, source: str = "modrinth"
    ) -> Mod | None:
        async with async_session() as session:
            server = await session.get(Server, server_id)
            if not server:
                return None

            if source == "modrinth":
                # Resolve dependencies first
                result = await session.execute(
                    select(Mod).where(Mod.server_id == server_id)
                )
                installed_mods = result.scalars().all()
                installed_ids = {m.project_id for m in installed_mods}

                if server.mc_version and server.loader:
                    deps = await self._resolve_dependencies(
                        project_id,
                        server.mc_version,
                        server.loader,
                        installed_ids,
                        set(),
                    )
                    # Install dependencies (skip the target mod itself, which is last)
                    for dep in deps[:-1]:
                        if dep["project_id"] not in installed_ids:
                            dep_mod = await self._install_modrinth_mod(
                                session,
                                server,
                                dep["project_id"],
                                is_dependency=True,
                            )
                            if dep_mod:
                                installed_ids.add(dep["project_id"])
                                logger.info(f"Auto-installed dependency: {dep['name']}")

                return await self._install_modrinth_mod(session, server, project_id)
            else:
                logger.error(f"Unknown mod source: {source}")
                return None

    async def _install_modrinth_mod(
        self,
        session: AsyncSession,
        server: Server,
        project_id: str,
        is_dependency: bool = False,
    ) -> Mod | None:
        try:
            project = await self.get_modrinth_project(project_id)
            versions = await self.get_modrinth_versions(
                project_id,
                game_version=server.mc_version,
                loader=server.loader,
            )

            if not versions:
                logger.warning(f"No compatible versions found for {project_id}")
                return None

            latest = versions[0]
            primary_file = next(
                (f for f in latest["files"] if f.get("primary", False)),
                latest["files"][0] if latest["files"] else None,
            )
            if not primary_file:
                return None

            mods_dir = Path(server.path) / "mods"
            mods_dir.mkdir(parents=True, exist_ok=True)

            file_path = mods_dir / primary_file["filename"]
            expected_hash = (primary_file.get("hashes") or {}).get("sha512")
            await self._download_file(
                primary_file["url"], file_path, expected_hash=expected_hash
            )

            mod = Mod(
                server_id=server.id,
                name=project["title"],
                slug=project.get("slug"),
                source="modrinth",
                project_id=project_id,
                version_id=latest["id"],
                installed_version=latest["version_number"],
                latest_version=latest["version_number"],
                file_name=primary_file["filename"],
                download_url=primary_file["url"],
                auto_update=True,
                update_available=False,
                is_dependency=is_dependency,
                last_checked=datetime.now(timezone.utc),
            )
            session.add(mod)
            await session.commit()
            await session.refresh(mod)
            logger.info(f"Installed mod {project['title']} v{latest['version_number']}")
            self._conflict_cache.pop(server.id, None)
            return mod

        except Exception as e:
            logger.error(f"Failed to install mod {project_id}: {e}")
            await session.rollback()
            return None

    async def check_updates(self, server_id: int | None = None):
        async with async_session() as session:
            query = select(Mod).where(Mod.auto_update.is_(True))
            if server_id:
                query = query.where(Mod.server_id == server_id)

            result = await session.execute(query)
            mods = result.scalars().all()

            for mod in mods:
                try:
                    await self._check_mod_update(session, mod)
                except Exception as e:
                    logger.error(f"Error checking update for mod {mod.name}: {e}")

            await session.commit()

    async def resolve_imported_mods(self, server_id: int) -> dict:
        """Resolve mods with project_id='imported' by looking up their SHA512 hash
        on the Modrinth API. Updates project_id, version_id, slug, name and enables
        auto_update once a real Modrinth project is found.

        Returns: {"resolved": int, "not_found": int, "errors": int}
        """
        stats = {"resolved": 0, "not_found": 0, "errors": 0}
        async with async_session() as session:
            server = await session.get(Server, server_id)
            if not server:
                return stats

            result = await session.execute(
                select(Mod).where(
                    Mod.server_id == server_id,
                    Mod.project_id == "imported",
                )
            )
            mods = result.scalars().all()
            if not mods:
                return stats

            mods_dir = Path(server.path) / "mods"
            client = await self._get_client()

            # Build hash -> mod mapping in chunks of 100
            CHUNK = 100
            mod_list = list(mods)
            for i in range(0, len(mod_list), CHUNK):
                chunk = mod_list[i : i + CHUNK]
                hashes: dict[str, Mod] = {}
                for mod in chunk:
                    if not mod.file_name:
                        stats["not_found"] += 1
                        continue
                    jar_path = mods_dir / mod.file_name
                    if not jar_path.exists():
                        stats["not_found"] += 1
                        continue
                    try:
                        h = hashlib.sha512(jar_path.read_bytes()).hexdigest()
                        hashes[h] = mod
                    except Exception as e:
                        logger.debug(f"Hash error for {mod.file_name}: {e}")
                        stats["errors"] += 1

                if not hashes:
                    continue

                try:
                    resp = await client.post(
                        f"{settings.modrinth_api_url}/version_files",
                        json={"hashes": list(hashes.keys()), "algorithm": "sha512"},
                    )
                    if resp.status_code != 200:
                        stats["errors"] += len(hashes)
                        continue
                    data: dict = resp.json()  # {hash: version_object}
                except Exception as e:
                    logger.error(f"Modrinth version_files lookup failed: {e}")
                    stats["errors"] += len(hashes)
                    continue

                for h, mod in hashes.items():
                    version_obj = data.get(h)
                    if not version_obj:
                        stats["not_found"] += 1
                        continue
                    try:
                        project_id = version_obj["project_id"]
                        version_id = version_obj["id"]
                        version_number = version_obj.get("version_number", "")
                        # Fetch project details for name + slug
                        proj_resp = await client.get(
                            f"{settings.modrinth_api_url}/project/{project_id}"
                        )
                        project = (
                            proj_resp.json() if proj_resp.status_code == 200 else {}
                        )

                        mod.project_id = project_id
                        mod.version_id = version_id
                        mod.installed_version = version_number
                        mod.latest_version = version_number
                        mod.name = project.get("title") or mod.name
                        mod.slug = project.get("slug")
                        mod.auto_update = True
                        mod.last_checked = datetime.now(timezone.utc)
                        stats["resolved"] += 1
                        logger.info(
                            f"Resolved imported mod '{mod.file_name}' ->"
                            f" {project.get('title', project_id)} v{version_number}"
                        )
                    except Exception as e:
                        logger.debug(f"Error resolving mod hash {h[:16]}: {e}")
                        stats["errors"] += 1

            await session.commit()

        # Run update check for freshly resolved mods
        if stats["resolved"] > 0:
            await self.check_updates(server_id)

        return stats

    async def _check_mod_update(self, session: AsyncSession, mod: Mod):
        if mod.source == "modrinth":
            await self._check_modrinth_update(session, mod)

    async def _check_modrinth_update(self, session: AsyncSession, mod: Mod):
        server = await session.get(Server, mod.server_id)
        if not server:
            return

        versions = await self.get_modrinth_versions(
            mod.project_id,
            game_version=server.mc_version,
            loader=server.loader,
        )

        if not versions:
            return

        latest = versions[0]
        mod.latest_version = latest["version_number"]
        mod.last_checked = datetime.now(timezone.utc)

        if latest["id"] != mod.version_id:
            mod.update_available = True
            logger.info(
                f"Update available for {mod.name}: {mod.installed_version} -> {latest['version_number']}"
            )

    async def update_mod(self, mod_id: int) -> bool:
        async with async_session() as session:
            mod = await session.get(Mod, mod_id)
            if not mod or not mod.update_available:
                return False

            server = await session.get(Server, mod.server_id)
            if not server:
                return False

            try:
                if mod.source == "modrinth":
                    return await self._update_modrinth_mod(session, mod, server)
                else:
                    logger.error(f"Unknown mod source for update: {mod.source}")
                    return False
            except Exception as e:
                logger.error(f"Failed to update mod {mod.name}: {e}")
                await session.rollback()
                return False

    async def _update_modrinth_mod(
        self, session: AsyncSession, mod: Mod, server: Server
    ) -> bool:
        versions = await self.get_modrinth_versions(
            mod.project_id,
            game_version=server.mc_version,
            loader=server.loader,
        )
        if not versions:
            return False

        latest = versions[0]
        primary_file = next(
            (f for f in latest["files"] if f.get("primary", False)),
            latest["files"][0] if latest["files"] else None,
        )
        if not primary_file:
            return False

        mods_dir = Path(server.path) / "mods"

        # Remove old file
        if mod.file_name:
            old_file = mods_dir / mod.file_name
            if old_file.exists():
                old_file.unlink()

        # Download new file
        new_path = mods_dir / primary_file["filename"]
        expected_hash = (primary_file.get("hashes") or {}).get("sha512")
        await self._download_file(
            primary_file["url"], new_path, expected_hash=expected_hash
        )

        mod.version_id = latest["id"]
        mod.installed_version = latest["version_number"]
        mod.latest_version = latest["version_number"]
        mod.file_name = primary_file["filename"]
        mod.download_url = primary_file["url"]
        mod.update_available = False
        mod.last_checked = datetime.now(timezone.utc)

        await session.commit()
        logger.info(f"Updated mod {mod.name} to v{latest['version_number']}")
        return True

    async def update_all_mods(self, server_id: int) -> dict:
        results = {"updated": 0, "failed": 0, "skipped": 0}
        async with async_session() as session:
            result = await session.execute(
                select(Mod).where(
                    Mod.server_id == server_id,
                    Mod.auto_update.is_(True),
                    Mod.update_available.is_(True),
                )
            )
            mods = result.scalars().all()

        for mod in mods:
            success = await self.update_mod(mod.id)
            if success:
                results["updated"] += 1
            else:
                results["failed"] += 1

        return results

    async def remove_mod(self, mod_id: int) -> bool:
        async with async_session() as session:
            mod = await session.get(Mod, mod_id)
            if not mod:
                return False

            server = await session.get(Server, mod.server_id)
            if server and mod.file_name:
                file_path = Path(server.path) / "mods" / mod.file_name
                if file_path.exists():
                    file_path.unlink()

            await session.delete(mod)
            await session.commit()
            self._conflict_cache.pop(mod.server_id, None)
            return True

    async def _download_file(
        self,
        url: str,
        dest: Path,
        expected_hash: str | None = None,
        hash_algo: str = "sha512",
    ):
        client = await self._get_client()
        tmp_dest = dest.with_suffix(dest.suffix + ".tmp")
        try:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(tmp_dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        f.write(chunk)

            if expected_hash:
                hasher = hashlib.new(hash_algo)
                with open(tmp_dest, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        hasher.update(chunk)
                actual_hash = hasher.hexdigest()
                if actual_hash != expected_hash:
                    raise ValueError(
                        f"Hash mismatch for {dest.name}: expected {expected_hash}, got {actual_hash}"
                    )

            os.replace(str(tmp_dest), str(dest))
        except Exception:
            if tmp_dest.exists():
                tmp_dest.unlink()
            raise

    async def check_mod_compatibility(
        self, project_id: str, mc_version: str, loader: str
    ) -> str:
        """Check compatibility of a mod with MC version and loader.

        Returns: 'compatible', 'untested', or 'incompatible'.
        """
        try:
            versions = await self.get_modrinth_versions(project_id, mc_version, loader)
            if versions:
                return "compatible"
            # Check if mod has any versions at all for this MC version (any loader)
            versions_any = await self.get_modrinth_versions(
                project_id, mc_version, None
            )
            if versions_any:
                return "untested"
            return "incompatible"
        except Exception:
            return "untested"

    async def batch_check_compatibility(self, server_id: int) -> dict[int, str]:
        """Check compatibility for all mods on a server. Returns {mod_id: status}."""
        async with async_session() as session:
            server = await session.get(Server, server_id)
            if not server or not server.mc_version:
                return {}

            result = await session.execute(
                select(Mod).where(Mod.server_id == server_id)
            )
            mods = result.scalars().all()

            compat = {}
            for mod in mods:
                if (
                    mod.source == "modrinth"
                    and mod.project_id
                    and mod.project_id != "imported"
                ):
                    status = await self.check_mod_compatibility(
                        mod.project_id, server.mc_version, server.loader or ""
                    )
                    compat[mod.id] = status
                else:
                    compat[mod.id] = "untested"
            return compat

    async def check_version_compatibility(
        self, server_id: int, target_version: str
    ) -> list[dict]:
        """Check whether all mods on a server are compatible with target_version.

        Returns a list of dicts per mod:
          {mod_id, name, file_name, status: compatible|incompatible|unknown, latest_for_version}
        """
        async with async_session() as session:
            server = await session.get(Server, server_id)
            if not server:
                return []
            result = await session.execute(
                select(Mod).where(Mod.server_id == server_id)
            )
            mods = result.scalars().all()

        loader = server.loader or None
        results = []
        for mod in mods:
            if not mod.project_id or mod.project_id == "imported":
                results.append(
                    {
                        "mod_id": mod.id,
                        "name": mod.name,
                        "file_name": mod.file_name,
                        "status": "unknown",
                        "latest_for_version": None,
                    }
                )
                continue
            try:
                versions = await self.get_modrinth_versions(
                    mod.project_id, game_version=target_version, loader=loader
                )
                if versions:
                    results.append(
                        {
                            "mod_id": mod.id,
                            "name": mod.name,
                            "file_name": mod.file_name,
                            "status": "compatible",
                            "latest_for_version": versions[0].get("version_number"),
                        }
                    )
                else:
                    results.append(
                        {
                            "mod_id": mod.id,
                            "name": mod.name,
                            "file_name": mod.file_name,
                            "status": "incompatible",
                            "latest_for_version": None,
                        }
                    )
            except Exception as e:
                logger.debug(f"Version compat check error for {mod.name}: {e}")
                results.append(
                    {
                        "mod_id": mod.id,
                        "name": mod.name,
                        "file_name": mod.file_name,
                        "status": "unknown",
                        "latest_for_version": None,
                    }
                )
        return results

    async def find_max_compatible_version(self, server_id: int) -> dict:
        """Find the highest MC version all resolved mods support (for the server's loader).

        Returns:
          {max_version, compatible_versions[],
           resolved_count, unresolved_count, mod_details[{name, top_versions[]}]}
        """
        async with async_session() as session:
            server = await session.get(Server, server_id)
            if not server:
                return {"error": "Server not found"}
            result = await session.execute(
                select(Mod).where(Mod.server_id == server_id)
            )
            mods = result.scalars().all()

        loader = server.loader or None
        resolved = [m for m in mods if m.project_id and m.project_id != "imported"]
        unresolved = [m for m in mods if not m.project_id or m.project_id == "imported"]

        if not resolved:
            return {
                "max_version": None,
                "compatible_versions": [],
                "resolved_count": 0,
                "unresolved_count": len(unresolved),
                "mod_details": [],
            }

        def _ver_tuple(v: str):
            try:
                return tuple(int(x) for x in v.split("."))
            except Exception:
                return (0,)

        mod_details = []
        all_sets: list[set] = []
        for mod in resolved:
            try:
                versions = await self.get_modrinth_versions(
                    mod.project_id, game_version=None, loader=loader
                )
                game_versions: set[str] = set()
                for v in versions:
                    for gv in v.get("game_versions", []):
                        # Accept stable releases: 1.x.x (legacy) and YY.N (2026+ format)
                        parts = gv.split(".")
                        if all(p.isdigit() for p in parts) and len(parts) >= 2:
                            game_versions.add(gv)
                top = sorted(game_versions, key=_ver_tuple, reverse=True)[:5]
                mod_details.append({"name": mod.name, "top_versions": top})
                all_sets.append(game_versions)
            except Exception as e:
                logger.debug(f"find_max error for {mod.name}: {e}")
                mod_details.append({"name": mod.name, "top_versions": []})
                all_sets.append(set())

        if not all_sets or any(len(s) == 0 for s in all_sets):
            # At least one mod has no known versions — cannot compute intersection
            common: set = set()
            for s in all_sets:
                if s:
                    common = s if not common else common & s
        else:
            common = all_sets[0].copy()
            for s in all_sets[1:]:
                common &= s

        compatible_versions = sorted(common, key=_ver_tuple, reverse=True)

        return {
            "max_version": compatible_versions[0] if compatible_versions else None,
            "compatible_versions": compatible_versions[:20],
            "resolved_count": len(resolved),
            "unresolved_count": len(unresolved),
            "mod_details": mod_details,
        }

    async def update_max_compatible_version(self, server_id: int) -> dict:
        """Check and store the max compatible MC version for a single server and each mod."""
        data = await self.find_max_compatible_version(server_id)
        max_ver = data.get("max_version")

        # Build name -> max version mapping from mod_details
        mod_max_map: dict[str, str | None] = {}
        for md in data.get("mod_details", []):
            top = md.get("top_versions", [])
            mod_max_map[md["name"]] = top[0] if top else None

        async with async_session() as session:
            server = await session.get(Server, server_id)
            if server:
                server.max_compatible_mc_version = max_ver
                server.max_compatible_checked_at = datetime.now(timezone.utc)

            # Update per-mod max compatible version
            result = await session.execute(
                select(Mod).where(Mod.server_id == server_id)
            )
            mods = result.scalars().all()
            for mod in mods:
                if mod.name in mod_max_map:
                    mod.max_compatible_mc_version = mod_max_map[mod.name]

            await session.commit()
        logger.info(f"Max compat version for server {server_id}: {max_ver}")
        return data

    async def update_all_max_compatible_versions(self):
        """Daily job: update max compatible MC version for all servers with mods."""
        async with async_session() as session:
            result = await session.execute(
                select(Server.id).join(Mod, Mod.server_id == Server.id).distinct()
            )
            server_ids = [row[0] for row in result.all()]

        logger.info(f"Max compat check: {len(server_ids)} servers with mods")
        for sid in server_ids:
            try:
                await self.update_max_compatible_version(sid)
            except Exception as e:
                logger.error(f"Max compat check failed for server {sid}: {e}")
            await asyncio.sleep(2)

    async def save_profile(self, server_id: int, name: str):
        """Save current mods as a reusable profile."""
        import json as _json

        from app.models.mod_profile import ModProfile

        async with async_session() as session:
            server = await session.get(Server, server_id)
            if not server:
                raise ValueError("Server not found")
            result = await session.execute(
                select(Mod).where(Mod.server_id == server_id)
            )
            mods = result.scalars().all()
            mods_data = [
                {
                    "name": m.name,
                    "project_id": m.project_id,
                    "source": m.source,
                    "version_id": m.version_id,
                }
                for m in mods
            ]
            profile = ModProfile(
                name=name,
                server_type=server.server_type.value,
                loader=server.loader,
                mc_version=server.mc_version,
                mods_json=_json.dumps(mods_data),
            )
            session.add(profile)
            await session.commit()

    async def apply_profile(self, server_id: int, profile_id: int) -> dict:
        """Apply a mod profile to a server."""
        import json as _json

        from app.models.mod_profile import ModProfile

        async with async_session() as session:
            profile = await session.get(ModProfile, profile_id)
            if not profile:
                raise ValueError("Profile not found")
            mods_data = _json.loads(profile.mods_json)

        installed = 0
        errors = []
        for mod_info in mods_data:
            try:
                result = await self.install_mod(
                    server_id,
                    mod_info["project_id"],
                    mod_info.get("source", "modrinth"),
                )
                if result:
                    installed += 1
                else:
                    errors.append(f"{mod_info['name']}: install returned None")
            except Exception as e:
                errors.append(f"{mod_info['name']}: {e}")
        return {"installed": installed, "errors": errors}


mod_updater = ModUpdater()
