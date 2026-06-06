"""API endpoints for Minecraft and loader version lookups."""

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.services.auth import get_current_user_flexible
from app.services.version_cache import version_cache
from app.services.java_manager import get_required_java_version

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/versions", dependencies=[Depends(get_current_user_flexible)]
)

_MOJANG_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"


async def _http_get(url: str, timeout: float = 15.0) -> Any:
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": "GameServerManager/1.0"},
        follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


@router.get("/minecraft", summary="List Minecraft release versions")
async def get_minecraft_versions():
    cached = version_cache.get("mc_versions")
    if cached:
        return JSONResponse({"ok": True, "data": cached})

    try:
        manifest = await _http_get(_MOJANG_MANIFEST)
        releases = [v["id"] for v in manifest["versions"] if v["type"] == "release"]
        latest = manifest.get("latest", {}).get(
            "release", releases[0] if releases else None
        )
        data = {"versions": releases, "latest": latest}
        version_cache.set("mc_versions", data)
        return JSONResponse({"ok": True, "data": data})
    except Exception as e:
        logger.warning(f"Failed to fetch Minecraft versions: {e}")
        return JSONResponse({"ok": True, "data": {"versions": [], "latest": None}})


@router.get("/loader", summary="List loader versions for a given MC version")
async def get_loader_versions(
    loader: str = Query(..., description="Loader name"),
    mc_version: str = Query(..., description="Minecraft version"),
):
    loader = loader.lower().strip()
    cache_key = f"loader:{loader}:{mc_version}"
    cached = version_cache.get(cache_key)
    if cached:
        return JSONResponse({"ok": True, "data": cached})

    try:
        data = await _fetch_loader_versions(loader, mc_version)
    except Exception as e:
        logger.warning(f"Failed to fetch {loader} versions for MC {mc_version}: {e}")
        data = {"versions": [], "latest": None}

    version_cache.set(cache_key, data)
    return JSONResponse({"ok": True, "data": data})


async def _fetch_loader_versions(loader: str, mc_version: str) -> dict:
    if loader in ("", "vanilla"):
        return {"versions": [], "latest": None}

    if loader == "fabric":
        return await _fetch_fabric_versions(mc_version)
    elif loader == "paper":
        return await _fetch_paper_versions(mc_version)
    elif loader == "forge":
        return await _fetch_forge_versions(mc_version)
    elif loader == "neoforge":
        return await _fetch_neoforge_versions(mc_version)
    elif loader == "quilt":
        return await _fetch_quilt_versions(mc_version)

    return {"versions": [], "latest": None}


async def _fetch_fabric_versions(mc_version: str) -> dict:
    url = f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}"
    data = await _http_get(url)
    if not data:
        return {"versions": [], "latest": None}
    versions = [entry["loader"]["version"] for entry in data if "loader" in entry]
    return {"versions": versions, "latest": versions[0] if versions else None}


async def _fetch_paper_versions(mc_version: str) -> dict:
    url = f"https://api.papermc.io/v2/projects/paper/versions/{mc_version}/builds"
    data = await _http_get(url)
    builds = data.get("builds", []) if data else []
    versions = [str(b["build"]) for b in reversed(builds)]
    return {"versions": versions, "latest": versions[0] if versions else None}


async def _fetch_forge_versions(mc_version: str) -> dict:
    url = (
        "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
    )
    data = await _http_get(url)
    promos = data.get("promos", {}) if data else {}
    recommended = promos.get(f"{mc_version}-recommended")
    latest = promos.get(f"{mc_version}-latest")
    versions = []
    if recommended:
        versions.append(recommended)
    if latest and latest != recommended:
        versions.append(latest)
    return {"versions": versions, "latest": recommended or latest or None}


async def _fetch_neoforge_versions(mc_version: str) -> dict:
    url = (
        "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
    )
    data = await _http_get(url)
    all_versions = data.get("versions", []) if data else []

    mc_parts = mc_version.split(".")
    if len(mc_parts) >= 3:
        nf_prefix = f"{mc_parts[1]}.{mc_parts[2]}."
    elif len(mc_parts) >= 2:
        nf_prefix = f"{mc_parts[1]}."
    else:
        return {"versions": [], "latest": None}

    matching = [v for v in all_versions if v.startswith(nf_prefix)]
    if not matching and len(mc_parts) >= 2:
        matching = [v for v in all_versions if v.startswith(f"{mc_parts[1]}.")]
    matching.reverse()
    return {"versions": matching, "latest": matching[0] if matching else None}


async def _fetch_quilt_versions(mc_version: str) -> dict:
    url = f"https://meta.quiltmc.org/v3/versions/loader/{mc_version}"
    data = await _http_get(url)
    if not data:
        return {"versions": [], "latest": None}
    versions = [entry["loader"]["version"] for entry in data if "loader" in entry]
    return {"versions": versions, "latest": versions[0] if versions else None}


@router.get("/java-info", summary="Get required Java version for a MC version")
async def get_java_info(
    mc_version: str = Query(..., description="Minecraft version"),
):
    try:
        java_ver = get_required_java_version(mc_version)
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "java_version": java_ver,
                    "label": f"Java {java_ver}+",
                },
            }
        )
    except Exception as e:
        logger.warning(f"Failed to get Java info for MC {mc_version}: {e}")
        return JSONResponse(
            {"ok": True, "data": {"java_version": None, "label": "Unknown"}}
        )
