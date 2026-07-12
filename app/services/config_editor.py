"""Parse and edit server.properties files with typed field schemas."""

import logging

logger = logging.getLogger(__name__)

# Field schema: key -> {type, label, description, options (for enum), min/max (for int)}
MC_JAVA_FIELDS = {
    "server-port": {
        "type": "number",
        "label": "Server Port",
        "min": 1,
        "max": 65535,
        "default": 25565,
    },
    "gamemode": {
        "type": "enum",
        "label": "Game Mode",
        "options": ["survival", "creative", "adventure", "spectator"],
        "default": "survival",
    },
    "difficulty": {
        "type": "enum",
        "label": "Difficulty",
        "options": ["peaceful", "easy", "normal", "hard"],
        "default": "easy",
    },
    "max-players": {
        "type": "number",
        "label": "Max Players",
        "min": 1,
        "max": 1000,
        "default": 20,
    },
    "online-mode": {"type": "boolean", "label": "Online Mode", "default": True},
    "pvp": {"type": "boolean", "label": "PvP", "default": True},
    "view-distance": {
        "type": "number",
        "label": "View Distance",
        "min": 3,
        "max": 32,
        "default": 10,
    },
    "motd": {"type": "string", "label": "MOTD", "default": "A Minecraft Server"},
    "level-name": {"type": "string", "label": "Level Name", "default": "world"},
    "enable-command-block": {
        "type": "boolean",
        "label": "Command Blocks",
        "default": False,
    },
    "spawn-protection": {
        "type": "number",
        "label": "Spawn Protection Radius",
        "min": 0,
        "max": 256,
        "default": 16,
    },
    "white-list": {"type": "boolean", "label": "Whitelist", "default": False},
    "allow-flight": {"type": "boolean", "label": "Allow Flight", "default": False},
    "spawn-npcs": {"type": "boolean", "label": "Spawn NPCs", "default": True},
    "spawn-animals": {"type": "boolean", "label": "Spawn Animals", "default": True},
    "spawn-monsters": {"type": "boolean", "label": "Spawn Monsters", "default": True},
    "generate-structures": {
        "type": "boolean",
        "label": "Generate Structures",
        "default": True,
    },
    "allow-nether": {"type": "boolean", "label": "Allow Nether", "default": True},
    "level-type": {
        "type": "string",
        "label": "Level Type",
        "default": "minecraft\\:normal",
    },
    "hardcore": {"type": "boolean", "label": "Hardcore", "default": False},
    "enable-rcon": {"type": "boolean", "label": "Enable RCON", "default": False},
    "rcon.port": {
        "type": "number",
        "label": "RCON Port",
        "min": 1,
        "max": 65535,
        "default": 25575,
    },
    "enable-query": {"type": "boolean", "label": "Enable Query", "default": False},
    "server-ip": {"type": "string", "label": "Server IP", "default": ""},
    "simulation-distance": {
        "type": "number",
        "label": "Simulation Distance",
        "min": 3,
        "max": 32,
        "default": 10,
    },
}

# Complete Minecraft Java server.properties defaults (1.21+).
# All values are strings since server.properties is a plain-text format.
MINECRAFT_JAVA_DEFAULTS = {
    # Network
    "server-port": "25565",
    "server-ip": "",
    "enable-status": "true",
    "network-compression-threshold": "256",
    "prevent-proxy-connections": "false",
    "use-native-transport": "true",
    "rate-limit": "0",
    # Gameplay
    "gamemode": "survival",
    "force-gamemode": "false",
    "difficulty": "easy",
    "hardcore": "false",
    "pvp": "true",
    "max-players": "20",
    "max-world-size": "29999984",
    "spawn-protection": "16",
    "view-distance": "10",
    "simulation-distance": "10",
    "allow-flight": "false",
    "allow-nether": "true",
    "generate-structures": "true",
    "spawn-npcs": "true",
    "spawn-animals": "true",
    "spawn-monsters": "true",
    "entity-broadcast-range-percentage": "100",
    # World
    "level-name": "world",
    "level-seed": "",
    "level-type": "minecraft\\:normal",
    "max-tick-time": "60000",
    "player-idle-timeout": "0",
    # Server info
    "motd": "A Minecraft Server",
    "enable-command-block": "false",
    "online-mode": "true",
    "white-list": "false",
    "enforce-whitelist": "false",
    "op-permission-level": "4",
    "function-permission-level": "2",
    "hide-online-players": "false",
    "enforce-secure-profile": "true",
    # RCON / Query
    "enable-rcon": "false",
    "rcon.port": "25575",
    "rcon.password": "",
    "enable-query": "false",
    "query.port": "25565",
    "broadcast-rcon-to-ops": "true",
    "broadcast-console-to-ops": "true",
    # Resource pack
    "resource-pack": "",
    "resource-pack-sha1": "",
    "require-resource-pack": "false",
    # Logging / misc
    "log-ips": "true",
    "sync-chunk-writes": "true",
    "text-filtering-config": "",
    "accepts-transfers": "false",
    "pause-when-empty-seconds": "60",
    "bug-report-link": "",
}


def _sanitize_property_value(value: str) -> str:
    """Remove CR and LF characters to prevent property injection in server.properties."""
    return str(value).replace("\r", "").replace("\n", " ")


def generate_default_properties(overrides: dict[str, str] | None = None) -> str:
    """Generate a complete server.properties file content with Minecraft Java defaults.

    Args:
        overrides: Optional dict of property key -> value to override defaults.

    Returns:
        The full server.properties file content as a string.
    """
    props = dict(MINECRAFT_JAVA_DEFAULTS)
    if overrides:
        props.update(overrides)
    lines = [f"{key}={_sanitize_property_value(value)}" for key, value in props.items()]
    return "\n".join(lines) + "\n"


MC_BEDROCK_FIELDS = {
    "server-port": {"type": "number", "label": "Server Port", "min": 1, "max": 65535},
    "server-portv6": {
        "type": "number",
        "label": "Server Port (IPv6)",
        "min": 1,
        "max": 65535,
    },
    "gamemode": {
        "type": "enum",
        "label": "Game Mode",
        "options": ["survival", "creative", "adventure"],
    },
    "difficulty": {
        "type": "enum",
        "label": "Difficulty",
        "options": ["peaceful", "easy", "normal", "hard"],
    },
    "max-players": {"type": "number", "label": "Max Players", "min": 1, "max": 1000},
    "online-mode": {"type": "boolean", "label": "Online Mode"},
    "white-list": {"type": "boolean", "label": "Whitelist"},
    "level-name": {"type": "string", "label": "Level Name"},
    "server-name": {"type": "string", "label": "Server Name"},
    "view-distance": {"type": "number", "label": "View Distance", "min": 5, "max": 48},
    "allow-cheats": {"type": "boolean", "label": "Allow Cheats"},
}


def parse_properties(content: str) -> dict[str, str]:
    """Parse a server.properties file content into a dict."""
    props = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    return props


def write_properties(original_content: str, updates: dict[str, str]) -> str:
    """Write updates into a server.properties file, preserving comments and order."""
    lines = original_content.splitlines()
    updated_keys = set()
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                result.append(f"{key}={updates[key]}")
                updated_keys.add(key)
                continue
        result.append(line)
    # Append new keys not already present
    for key, value in updates.items():
        if key not in updated_keys:
            result.append(f"{key}={value}")
    return "\n".join(result) + "\n"


def get_field_schema(server_type: str) -> dict:
    """Return the appropriate field schema for a server type."""
    if server_type == "minecraft_bedrock":
        return MC_BEDROCK_FIELDS
    return MC_JAVA_FIELDS
