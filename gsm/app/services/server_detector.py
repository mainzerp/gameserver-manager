import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_properties(filepath: str) -> dict[str, str]:
    props = {}
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    props[key.strip()] = value.strip()
    except Exception as e:
        logger.warning(f"Failed to parse properties file {filepath}: {e}")
    return props


def detect_server_info(path: str) -> dict:
    p = Path(path)
    result = {
        "name": p.name,
        "server_type": None,
        "loader": None,
        "mc_version": None,
        "port": None,
        "executable": None,
    }

    if not p.is_dir():
        return result

    props_file = p / "server.properties"
    if props_file.exists():
        result["server_type"] = "minecraft_java"
        props = _parse_properties(str(props_file))
        result["port"] = int(props.get("server-port", 25565))
        if "motd" in props:
            pass

    bedrock_exe = "bedrock_server.exe" if os.name == "nt" else "bedrock_server"
    if (p / bedrock_exe).exists():
        result["server_type"] = "minecraft_bedrock"
        result["executable"] = bedrock_exe
        result["port"] = result["port"] or 19132
        return result

    if (p / "steamapps").is_dir():
        result["server_type"] = "steam"
        return result

    # Loader detection
    for f in p.iterdir():
        if not f.is_file():
            continue
        name_lower = f.name.lower()
        if (
            "fabric" in name_lower
            and name_lower.endswith(".jar")
            and ("launcher" in name_lower or "launch" in name_lower)
        ):
            result["loader"] = "fabric"
            result["executable"] = f.name
        elif name_lower.startswith("paper") and name_lower.endswith(".jar"):
            result["loader"] = "paper"
            result["executable"] = f.name
        elif "neoforge" in name_lower and name_lower.endswith(".jar"):
            result["loader"] = "neoforge"
            result["executable"] = f.name
        elif (
            "forge" in name_lower
            and name_lower.endswith(".jar")
            and "installer" not in name_lower
        ):
            result["loader"] = "forge"
            result["executable"] = f.name
        elif "quilt-server-launch" in name_lower:
            result["loader"] = "quilt"
            result["executable"] = f.name

    if not result["executable"]:
        server_jar = p / "server.jar"
        if server_jar.exists():
            result["executable"] = "server.jar"

    if result["server_type"] is None and result["executable"]:
        result["server_type"] = "minecraft_java"

    if not result["port"]:
        result["port"] = 25565 if result["server_type"] == "minecraft_java" else 27015

    return result
