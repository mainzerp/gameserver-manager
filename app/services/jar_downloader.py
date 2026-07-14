"""
Download Minecraft server JARs from official sources.
Supports: Vanilla, Fabric, Paper, Quilt, Forge, NeoForge.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PAPER_API = "https://api.papermc.io/v2"


async def _get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=120.0,
        headers={"User-Agent": "GameServerManager/1.0"},
        follow_redirects=True,
    )


async def download_vanilla_jar(version: str, dest: Path) -> bool:
    """Download vanilla server.jar from Mojang."""
    async with await _get_client() as client:
        try:
            # Get version manifest
            resp = await client.get(
                "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
            )
            resp.raise_for_status()
            manifest = resp.json()

            version_entry = next(
                (v for v in manifest["versions"] if v["id"] == version), None
            )
            if not version_entry:
                logger.error(f"Vanilla version {version} not found")
                return False

            # Get version details
            resp = await client.get(version_entry["url"])
            resp.raise_for_status()
            version_data = resp.json()

            server_dl = version_data.get("downloads", {}).get("server")
            if not server_dl:
                logger.error(f"No server download for version {version}")
                return False

            # Download the JAR
            logger.info(f"Downloading vanilla server {version}...")
            return await _download_file(client, server_dl["url"], dest)

        except Exception as e:
            logger.error(f"Failed to download vanilla jar: {e}")
            return False


async def download_fabric_jar(
    mc_version: str, dest: Path, loader_version: str | None = None
) -> bool:
    """Download Fabric server JAR (launcher that includes the installer)."""
    async with await _get_client() as client:
        try:
            if loader_version:
                lv = loader_version
            else:
                # Get latest loader version
                resp = await client.get("https://meta.fabricmc.net/v2/versions/loader")
                resp.raise_for_status()
                loaders = resp.json()
                if not loaders:
                    return False
                lv = loaders[0]["version"]

            # Get latest installer version
            resp = await client.get("https://meta.fabricmc.net/v2/versions/installer")
            resp.raise_for_status()
            installers = resp.json()
            if not installers:
                return False
            installer_version = installers[0]["version"]

            url = (
                f"https://meta.fabricmc.net/v2/versions/loader/"
                f"{mc_version}/{lv}/{installer_version}/server/jar"
            )

            logger.info(f"Downloading Fabric server for MC {mc_version}...")
            return await _download_file(client, url, dest)

        except Exception as e:
            logger.error(f"Failed to download Fabric jar: {e}")
            return False


async def download_paper_jar(
    mc_version: str, dest: Path, loader_version: str | None = None
) -> bool:
    """Download Paper server JAR from PaperMC API."""
    async with await _get_client() as client:
        try:
            if loader_version:
                build_num = int(loader_version)
                resp = await client.get(
                    f"{PAPER_API}/projects/paper/versions/{mc_version}/builds/{build_num}"
                )
                resp.raise_for_status()
                data = resp.json()
                download = data["downloads"]["application"]
                filename = download["name"]
            else:
                # Get latest build
                resp = await client.get(
                    f"{PAPER_API}/projects/paper/versions/{mc_version}/builds"
                )
                resp.raise_for_status()
                data = resp.json()

                builds = data.get("builds", [])
                if not builds:
                    logger.error(f"No Paper builds found for {mc_version}")
                    return False

                latest = builds[-1]
                build_num = latest["build"]
                download = latest["downloads"]["application"]
                filename = download["name"]

            url = f"{PAPER_API}/projects/paper/versions/{mc_version}/builds/{build_num}/downloads/{filename}"

            logger.info(
                f"Downloading Paper server for MC {mc_version} (build {build_num})..."
            )
            return await _download_file(client, url, dest)

        except Exception as e:
            logger.error(f"Failed to download Paper jar: {e}")
            return False


async def download_quilt_jar(
    mc_version: str, dest: Path, loader_version: str | None = None
) -> bool:
    """Download Quilt server JAR (same pattern as Fabric)."""
    async with await _get_client() as client:
        try:
            if loader_version:
                lv = loader_version
            else:
                resp = await client.get("https://meta.quiltmc.org/v3/versions/loader")
                resp.raise_for_status()
                loaders = resp.json()
                if not loaders:
                    return False
                lv = loaders[0]["version"]

            resp = await client.get("https://meta.quiltmc.org/v3/versions/installer")
            resp.raise_for_status()
            installers = resp.json()
            if not installers:
                return False
            installer_version = installers[0]["version"]

            url = (
                f"https://meta.quiltmc.org/v3/versions/loader/"
                f"{mc_version}/{lv}/{installer_version}/server/jar"
            )

            logger.info(f"Downloading Quilt server for MC {mc_version}...")
            return await _download_file(client, url, dest)

        except Exception as e:
            logger.error(f"Failed to download Quilt jar: {e}")
            return False


async def download_forge_jar(
    mc_version: str, dest_dir: Path, loader_version: str | None = None
) -> bool:
    """Download and run Forge installer."""
    async with await _get_client() as client:
        try:
            if loader_version:
                forge_version = loader_version
            else:
                resp = await client.get(
                    "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
                )
                resp.raise_for_status()
                promos = resp.json().get("promos", {})

                forge_version = promos.get(f"{mc_version}-recommended") or promos.get(
                    f"{mc_version}-latest"
                )
                if not forge_version:
                    logger.error(f"No Forge version found for MC {mc_version}")
                    return False

            installer_url = (
                f"https://maven.minecraftforge.net/net/minecraftforge/forge/"
                f"{mc_version}-{forge_version}/forge-{mc_version}-{forge_version}-installer.jar"
            )

            installer_path = (
                dest_dir / f"forge-{mc_version}-{forge_version}-installer.jar"
            )
            logger.info(f"Downloading Forge installer for MC {mc_version}...")
            if not await _download_file(client, installer_url, installer_path):
                return False

            logger.info("Running Forge installer...")
            proc = await asyncio.create_subprocess_exec(
                "java",
                "-jar",
                str(installer_path),
                "--installServer",
                cwd=str(dest_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                logger.error("Forge installer timed out")
                return False

            try:
                installer_path.unlink()
            except OSError:
                pass

            for f in dest_dir.iterdir():
                if (
                    f.name.startswith("forge-")
                    and f.name.endswith(".jar")
                    and "installer" not in f.name
                ):
                    server_jar = dest_dir / "server.jar"
                    if server_jar.exists():
                        server_jar.unlink()
                    f.rename(server_jar)
                    break

            logger.info(f"Forge installed for MC {mc_version}")
            return True

        except Exception as e:
            logger.error(f"Failed to download/install Forge: {e}")
            return False


def _pick_neoforge_version(mc_version: str, versions: list[str]) -> str | None:
    """Choose the latest NeoForge version matching the Minecraft version."""
    mc_parts = mc_version.split(".")
    if len(mc_parts) >= 2:
        nf_prefix = f"{mc_parts[1]}."
        if len(mc_parts) >= 3:
            nf_prefix = f"{mc_parts[1]}.{mc_parts[2]}."
    else:
        nf_prefix = ""

    matching = [v for v in versions if v.startswith(nf_prefix)]
    if not matching:
        matching = [v for v in versions if v.startswith(f"{mc_parts[1]}.")]
    return matching[-1] if matching else None


async def _install_neoforge_server(dest_dir: Path, installer_path: Path) -> bool:
    """Run the NeoForge installer and rename the produced JAR to server.jar."""
    logger.info("Running NeoForge installer...")
    proc = await asyncio.create_subprocess_exec(
        "java",
        "-jar",
        str(installer_path),
        "--installServer",
        cwd=str(dest_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        logger.error("NeoForge installer timed out")
        return False

    try:
        installer_path.unlink()
    except OSError:
        pass

    # Ensure a server.jar exists for NeoForge
    for f in dest_dir.iterdir():
        if f.name.endswith(".jar") and "installer" not in f.name:
            server_jar = dest_dir / "server.jar"
            if server_jar.exists():
                server_jar.unlink()
            f.rename(server_jar)
            break
    else:
        logger.warning(
            "NeoForge installer did not produce a server JAR. "
            "The server may need to be started via the generated run script."
        )

    return True


async def download_neoforge_jar(
    mc_version: str, dest_dir: Path, loader_version: str | None = None
) -> bool:
    """Download and run NeoForge installer."""
    async with await _get_client() as client:
        try:
            if loader_version:
                nf_version = loader_version
            else:
                resp = await client.get(
                    "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
                )
                resp.raise_for_status()
                versions = resp.json().get("versions", [])
                nf_version = _pick_neoforge_version(mc_version, versions)
                if not nf_version:
                    logger.error(f"No NeoForge version found for MC {mc_version}")
                    return False

            installer_url = (
                f"https://maven.neoforged.net/releases/net/neoforged/neoforge/"
                f"{nf_version}/neoforge-{nf_version}-installer.jar"
            )

            installer_path = dest_dir / f"neoforge-{nf_version}-installer.jar"
            logger.info(f"Downloading NeoForge installer {nf_version}...")
            if not await _download_file(client, installer_url, installer_path):
                return False

            if not await _install_neoforge_server(dest_dir, installer_path):
                return False

            logger.info(f"NeoForge installed for MC {mc_version}")
            return True

        except Exception as e:
            logger.error(f"Failed to download/install NeoForge: {e}")
            return False


async def get_latest_bedrock_version() -> str:
    """Get the latest BDS version. Falls back to a known recent version."""
    async with await _get_client() as client:
        try:
            # Try to scrape the download page for the latest version
            resp = await client.get(
                "https://www.minecraft.net/en-us/download/server/bedrock"
            )
            if resp.status_code == 200:
                import re

                match = re.search(
                    r"bedrock-server-(\d+\.\d+\.\d+\.\d+)\.zip", resp.text
                )
                if match:
                    return match.group(1)
        except Exception as e:
            logger.warning(f"Failed to fetch latest Bedrock version: {e}")
    # Fallback to a known recent version
    return "1.21.62.01"


async def download_bedrock_server(version: str | None, dest: Path) -> bool:
    """Download Bedrock Dedicated Server zip and extract it."""
    import io
    import zipfile

    async with await _get_client() as client:
        try:
            if not version:
                version = await get_latest_bedrock_version()

            os_suffix = "win" if sys.platform == "win32" else "linux"
            url = f"https://minecraft.azureedge.net/bin-{os_suffix}/bedrock-server-{version}.zip"

            logger.info(f"Downloading Bedrock Dedicated Server {version} from {url}...")
            resp = await client.get(url)
            resp.raise_for_status()

            dest.mkdir(parents=True, exist_ok=True)
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            zf.extractall(str(dest))
            logger.info(f"Bedrock server {version} extracted to {dest}")

            # Set executable permission on Linux
            if sys.platform != "win32":
                exe_path = dest / "bedrock_server"
                if exe_path.exists():
                    exe_path.chmod(0o755)

            return True

        except Exception as e:
            logger.error(f"Failed to download Bedrock server: {e}")
            return False


async def download_server_jar(
    mc_version: str,
    loader: str | None,
    dest_dir: Path,
    loader_version: str | None = None,
) -> bool:
    """
    Download the appropriate server JAR based on the loader type.
    Returns True if successful.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "server.jar"

    if dest.exists():
        logger.info(f"server.jar already exists at {dest}, skipping download")
        return True

    if not loader or loader == "vanilla":
        return await download_vanilla_jar(mc_version, dest)
    elif loader == "fabric":
        return await download_fabric_jar(
            mc_version, dest, loader_version=loader_version
        )
    elif loader == "paper":
        return await download_paper_jar(mc_version, dest, loader_version=loader_version)
    elif loader == "quilt":
        return await download_quilt_jar(mc_version, dest, loader_version=loader_version)
    elif loader == "forge":
        return await download_forge_jar(
            mc_version, dest_dir, loader_version=loader_version
        )
    elif loader == "neoforge":
        return await download_neoforge_jar(
            mc_version, dest_dir, loader_version=loader_version
        )
    else:
        logger.warning(
            f"Automatic download for loader '{loader}' is not supported. "
            f"Please place server.jar manually in {dest_dir}"
        )
        return False


async def get_available_mc_versions() -> list[str]:
    """Fetch available Minecraft release versions from Mojang."""
    async with await _get_client() as client:
        try:
            resp = await client.get(
                "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
            )
            resp.raise_for_status()
            manifest = resp.json()
            return [v["id"] for v in manifest["versions"] if v["type"] == "release"]
        except Exception:
            return []


async def get_latest_mc_version() -> str | None:
    """Fetch the latest Minecraft release version."""
    async with await _get_client() as client:
        try:
            resp = await client.get(
                "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
            )
            resp.raise_for_status()
            manifest = resp.json()
            return manifest.get("latest", {}).get("release")
        except Exception:
            return None


async def get_latest_paper_build(mc_version: str) -> int | None:
    """Get the latest Paper build number for a given MC version."""
    async with await _get_client() as client:
        try:
            resp = await client.get(
                f"{PAPER_API}/projects/paper/versions/{mc_version}/builds"
            )
            resp.raise_for_status()
            builds = resp.json().get("builds", [])
            if builds:
                return builds[-1]["build"]
        except Exception:
            pass
    return None


async def _latest_loader_version(url: str) -> str | None:
    """Return the latest loader version from a simple JSON list endpoint."""
    async with await _get_client() as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            loaders = resp.json()
            return loaders[0]["version"] if loaders else None
        except Exception:
            return None


async def _latest_forge_version(mc_version: str) -> str | None:
    async with await _get_client() as client:
        try:
            resp = await client.get(
                "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
            )
            resp.raise_for_status()
            promos = resp.json().get("promos", {})
            return promos.get(f"{mc_version}-recommended") or promos.get(
                f"{mc_version}-latest"
            )
        except Exception:
            return None


async def _latest_neoforge_version(mc_version: str) -> str | None:
    async with await _get_client() as client:
        try:
            resp = await client.get(
                "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
            )
            resp.raise_for_status()
            versions = resp.json().get("versions", [])
            mc_parts = mc_version.split(".")
            prefix = f"{mc_parts[1]}." if len(mc_parts) >= 2 else ""
            matching = [v for v in versions if v.startswith(prefix)]
            return matching[-1] if matching else None
        except Exception:
            return None


async def get_latest_version(
    loader: str | None, mc_version: str | None = None
) -> str | None:
    """Get the latest available version string for a given loader.

    For vanilla: returns latest MC release version.
    For paper: returns 'mc_version-build_num'.
    For fabric/quilt: returns latest loader version.
    For forge/neoforge: returns latest forge version for given MC version.
    """
    if not loader or loader == "vanilla":
        return await get_latest_mc_version()

    if loader == "paper" and mc_version:
        build = await get_latest_paper_build(mc_version)
        return f"{mc_version}-{build}" if build else None

    if loader == "fabric":
        return await _latest_loader_version(
            "https://meta.fabricmc.net/v2/versions/loader"
        )

    if loader == "quilt":
        return await _latest_loader_version(
            "https://meta.quiltmc.org/v3/versions/loader"
        )

    if loader == "forge" and mc_version:
        return await _latest_forge_version(mc_version)

    if loader == "neoforge" and mc_version:
        return await _latest_neoforge_version(mc_version)

    return None


async def _download_file(client: httpx.AsyncClient, url: str, dest: Path) -> bool:
    tmp_dest = dest.with_suffix(dest.suffix + ".tmp")
    try:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            downloaded = 0
            with open(tmp_dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
            logger.info(f"Downloaded {downloaded / 1024 / 1024:.1f} MB to {dest}")
        os.replace(str(tmp_dest), str(dest))
        return True
    except Exception as e:
        logger.error(f"Download failed: {e}")
        if tmp_dest.exists():
            tmp_dest.unlink()
        return False
