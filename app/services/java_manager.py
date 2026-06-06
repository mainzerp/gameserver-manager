"""
Maps Minecraft versions to the required Java version.
Handles auto-detection of the correct Java binary path.
"""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import sys
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# MC version -> minimum Java major version required
# Based on: https://minecraft.wiki/w/Tutorials/Update_Java
_JAVA_VERSION_BOUNDARIES = [
    # (mc_version_tuple, java_major_version)
    ((25, 0, 0), 25),  # 25.x+ (new versioning) requires Java 25
    ((1, 20, 5), 21),  # 1.20.5+ requires Java 21
    ((1, 18, 0), 17),  # 1.18+ requires Java 17
    ((1, 17, 0), 16),  # 1.17+ requires Java 16
    ((1, 0, 0), 8),  # 1.0 - 1.16.x works with Java 8
]

# Well-known Java install paths per platform
_JAVA_SEARCH_PATHS_LINUX = {
    25: [
        "/opt/java/jdk-25/bin/java",
        "/usr/lib/jvm/java-25-openjdk-amd64/bin/java",
        "/usr/lib/jvm/java-25-openjdk/bin/java",
        "/usr/lib/jvm/java-25/bin/java",
    ],
    21: [
        "/usr/lib/jvm/java-21-openjdk-amd64/bin/java",
        "/usr/lib/jvm/java-21-openjdk/bin/java",
        "/usr/lib/jvm/java-21/bin/java",
    ],
    17: [
        "/usr/lib/jvm/java-17-openjdk-amd64/bin/java",
        "/usr/lib/jvm/java-17-openjdk/bin/java",
        "/usr/lib/jvm/java-17/bin/java",
    ],
    16: [
        "/usr/lib/jvm/java-16-openjdk-amd64/bin/java",
        "/usr/lib/jvm/java-16-openjdk/bin/java",
    ],
    8: [
        "/usr/lib/jvm/java-8-openjdk-amd64/bin/java",
        "/usr/lib/jvm/java-8-openjdk/bin/java",
        "/usr/lib/jvm/java-1.8.0/bin/java",
    ],
}

_JAVA_SEARCH_PATHS_WINDOWS = {
    25: [
        r"C:\Program Files\Java\jdk-25\bin\java.exe",
        r"C:\Program Files\Eclipse Adoptium\jdk-25\bin\java.exe",
        r"C:\Program Files\Microsoft\jdk-25\bin\java.exe",
    ],
    21: [
        r"C:\Program Files\Java\jdk-21\bin\java.exe",
        r"C:\Program Files\Eclipse Adoptium\jdk-21\bin\java.exe",
        r"C:\Program Files\Microsoft\jdk-21\bin\java.exe",
    ],
    17: [
        r"C:\Program Files\Java\jdk-17\bin\java.exe",
        r"C:\Program Files\Eclipse Adoptium\jdk-17\bin\java.exe",
        r"C:\Program Files\Microsoft\jdk-17\bin\java.exe",
    ],
    16: [
        r"C:\Program Files\Java\jdk-16\bin\java.exe",
        r"C:\Program Files\Eclipse Adoptium\jdk-16\bin\java.exe",
    ],
    8: [
        r"C:\Program Files\Java\jre1.8.0_*\bin\java.exe",
        r"C:\Program Files\Java\jdk1.8.0_*\bin\java.exe",
        r"C:\Program Files\Java\jre-1.8\bin\java.exe",
    ],
}


def parse_mc_version(version_str: str) -> tuple[int, ...]:
    """Parse '1.20.5' into (1, 20, 5). Handles snapshots gracefully."""
    match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", version_str)
    if not match:
        return (1, 0, 0)
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3)) if match.group(3) else 0
    return (major, minor, patch)


def get_required_java_version(mc_version: str) -> int:
    """Return the minimum Java major version required for a given MC version."""
    mc_tuple = parse_mc_version(mc_version)
    for boundary, java_ver in _JAVA_VERSION_BOUNDARIES:
        if mc_tuple >= boundary:
            return java_ver
    return 8


def _find_java_binary(java_major: int) -> str | None:
    """Search for a Java binary matching the required major version on disk."""
    if sys.platform == "win32":
        search_paths = _JAVA_SEARCH_PATHS_WINDOWS
    else:
        search_paths = _JAVA_SEARCH_PATHS_LINUX

    candidates = search_paths.get(java_major, [])
    for path in candidates:
        if "*" in path:
            # Glob expansion
            import glob

            matches = sorted(glob.glob(path), reverse=True)
            if matches:
                return matches[0]
        elif os.path.isfile(path):
            return path

    return None


async def detect_java_version(java_binary: str) -> int | None:
    """Run `java -version` and parse the major version from output."""
    try:
        proc = await asyncio.create_subprocess_exec(
            java_binary,
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        output = stdout.decode("utf-8", errors="replace")
        # Parse: openjdk version "21.0.1" or "1.8.0_392"
        m = re.search(r'version "(\d+)(?:\.(\d+))?', output)
        if not m:
            return None
        major = int(m.group(1))
        if major == 1 and m.group(2):
            # Old naming: 1.8.x -> Java 8
            major = int(m.group(2))
        return major
    except Exception:
        return None


async def find_java_for_mc(mc_version: str) -> dict:
    """
    Find the best Java binary for the given MC version.

    Returns: {
        "java_path": str,           # path to java binary
        "java_major": int,          # detected Java major version
        "required_major": int,      # minimum required version
        "compatible": bool,         # whether it meets the requirement
        "auto_detected": bool,      # whether path was auto-detected
    }
    """
    required = get_required_java_version(mc_version)

    # 1. Try well-known paths for the exact required version
    exact_path = _find_java_binary(required)
    if exact_path:
        detected = await detect_java_version(exact_path)
        if detected and detected >= required:
            return {
                "java_path": exact_path,
                "java_major": detected,
                "required_major": required,
                "compatible": True,
                "auto_detected": True,
            }

    # 1b. Try managed Java installation
    managed_path = get_managed_java_path(required)
    if managed_path:
        detected = await detect_java_version(managed_path)
        if detected and detected >= required:
            return {
                "java_path": managed_path,
                "java_major": detected,
                "required_major": required,
                "compatible": True,
                "auto_detected": True,
            }

    # 2. Try the system default "java"
    system_ver = await detect_java_version("java")
    if system_ver and system_ver >= required:
        return {
            "java_path": "java",
            "java_major": system_ver,
            "required_major": required,
            "compatible": True,
            "auto_detected": True,
        }

    # 3. Try higher versions as fallback (Java is forward-compatible for MC)
    for fallback_ver in sorted(
        _JAVA_SEARCH_PATHS_LINUX.keys()
        if sys.platform != "win32"
        else _JAVA_SEARCH_PATHS_WINDOWS.keys(),
        reverse=True,
    ):
        if fallback_ver >= required:
            fb_path = _find_java_binary(fallback_ver)
            if fb_path:
                detected = await detect_java_version(fb_path)
                if detected and detected >= required:
                    return {
                        "java_path": fb_path,
                        "java_major": detected,
                        "required_major": required,
                        "compatible": True,
                        "auto_detected": True,
                    }

    # 4. Nothing found — return "java" with a warning
    return {
        "java_path": "java",
        "java_major": system_ver,
        "required_major": required,
        "compatible": False,
        "auto_detected": False,
    }


def get_java_version_info(mc_version: str) -> str:
    """Human-readable info about Java requirements for a MC version."""
    required = get_required_java_version(mc_version)
    return f"Java {required}+"


def _get_managed_java_dir() -> str:
    """Return the directory where managed Java installs are stored."""
    from app.config import settings as _settings

    return str(Path(_settings.servers_dir).parent / "data" / "java")


def get_managed_java_path(major_version: int) -> str | None:
    """Check if a managed Java installation exists for the given major version."""
    base = Path(_get_managed_java_dir()) / f"jdk-{major_version}"
    if not base.exists():
        return None
    java_name = "java.exe" if sys.platform == "win32" else "java"
    for root, _dirs, files in os.walk(base):
        if java_name in files:
            return str(Path(root) / java_name)
    return None


async def download_java(major_version: int) -> str | None:
    """Download Temurin JRE from Adoptium API. Returns path to java binary."""
    import io
    import platform
    import tarfile
    import zipfile as zf_mod

    dest_dir = _get_managed_java_dir()
    Path(dest_dir).mkdir(parents=True, exist_ok=True)

    # Check if already installed
    existing = get_managed_java_path(major_version)
    if existing:
        detected = await detect_java_version(existing)
        if detected and detected >= major_version:
            logger.info(f"Java {major_version} already installed at {existing}")
            return existing

    os_name = "windows" if sys.platform == "win32" else "linux"
    arch = "x64"
    if platform.machine().lower() in ("aarch64", "arm64"):
        arch = "aarch64"

    url = (
        f"https://api.adoptium.net/v3/assets/latest/{major_version}/hotspot"
        f"?os={os_name}&architecture={arch}&image_type=jre"
    )

    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            assets = resp.json()
            if not assets:
                logger.error(f"No Temurin JRE found for Java {major_version}")
                return None

            binary = assets[0]["binary"]
            dl_url = binary["package"]["link"]
            name = binary["package"]["name"]
            expected_checksum = binary["package"].get("checksum")

            logger.info(f"Downloading Java {major_version} from {dl_url}")
            resp = await client.get(dl_url)
            resp.raise_for_status()
            archive_data = resp.content

            # Verify checksum
            if expected_checksum:
                actual_checksum = hashlib.sha256(archive_data).hexdigest()
                if actual_checksum.lower() != expected_checksum.lower():
                    logger.error(
                        f"Checksum mismatch for Java {major_version}: "
                        f"expected {expected_checksum}, got {actual_checksum}"
                    )
                    return None
                logger.info(f"Checksum verified for Java {major_version}")
            else:
                logger.warning(
                    f"No checksum available for Java {major_version} download"
                )

            extract_dir = Path(dest_dir) / f"jdk-{major_version}"
            if extract_dir.exists():
                shutil.rmtree(str(extract_dir))
            extract_dir.mkdir(parents=True, exist_ok=True)

            MAX_EXTRACT_SIZE = 500 * 1024 * 1024  # 500 MB

            def _safe_member_path(member_name: str) -> Path | None:
                member_name = member_name.replace("\\", "/")
                parts = member_name.split("/")
                if ".." in parts or any(p.startswith("/") for p in parts):
                    return None
                target = (extract_dir / member_name).resolve()
                if not target.is_relative_to(extract_dir.resolve()):
                    return None
                return target

            if name.endswith(".zip"):
                archive = zf_mod.ZipFile(io.BytesIO(archive_data))
                extracted_size = 0
                for zipinfo in archive.infolist():
                    if zipinfo.is_dir():
                        continue
                    target = _safe_member_path(zipinfo.filename)
                    if target is None:
                        logger.warning(
                            f"Skipping dangerous zip member: {zipinfo.filename}"
                        )
                        continue
                    extracted_size += zipinfo.file_size
                    if extracted_size > MAX_EXTRACT_SIZE:
                        logger.error(
                            f"Java archive extraction exceeds {MAX_EXTRACT_SIZE} bytes"
                        )
                        return None
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(zipinfo) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            elif name.endswith(".tar.gz"):
                archive = tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz")
                extracted_size = 0
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    target = _safe_member_path(member.name)
                    if target is None:
                        logger.warning(f"Skipping dangerous tar member: {member.name}")
                        continue
                    extracted_size += member.size
                    if extracted_size > MAX_EXTRACT_SIZE:
                        logger.error(
                            f"Java archive extraction exceeds {MAX_EXTRACT_SIZE} bytes"
                        )
                        return None
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            else:
                logger.error(f"Unsupported archive format: {name}")
                return None

            # Find java binary
            java_name = "java.exe" if sys.platform == "win32" else "java"
            for root, _dirs, files in os.walk(extract_dir):
                if java_name in files:
                    java_path = str(Path(root) / java_name)
                    logger.info(f"Java {major_version} installed at {java_path}")
                    return java_path

            logger.error("Java binary not found after extraction")
            return None

    except Exception as e:
        logger.error(f"Failed to download Java {major_version}: {e}")
        return None


def list_managed_javas() -> list[dict]:
    """List all managed Java installations."""
    base = Path(_get_managed_java_dir())
    if not base.exists():
        return []
    results = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and child.name.startswith("jdk-"):
            try:
                version = int(child.name.split("-")[1])
            except (ValueError, IndexError):
                continue
            java_path = get_managed_java_path(version)
            if java_path:
                results.append({"version": version, "path": java_path})
    return results
