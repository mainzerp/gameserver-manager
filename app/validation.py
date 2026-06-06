import re

from app.models.server import ServerType


def validate_server_name(name: str) -> str | None:
    name = name.strip()
    if not name:
        return "Server name is required."
    if len(name) > 50:
        return "Server name must be 50 characters or fewer."
    if not re.match(r"^[a-zA-Z0-9 _-]+$", name):
        return "Server name may only contain letters, numbers, spaces, hyphens, and underscores."
    if "\x00" in name or ".." in name or "/" in name or "\\" in name:
        return "Server name contains invalid characters."
    return None


def validate_port(port: int | str) -> str | None:
    try:
        port = int(port)
    except (ValueError, TypeError):
        return "Port must be a number."
    if port < 1024 or port > 65535:
        return "Port must be between 1024 and 65535."
    return None


def validate_memory(min_memory: int, max_memory: int) -> str | None:
    if min_memory < 256 or min_memory > 32768:
        return "Minimum memory must be between 256 and 32768 MB."
    if max_memory < 256 or max_memory > 32768:
        return "Maximum memory must be between 256 and 32768 MB."
    if min_memory > max_memory:
        return "Minimum memory cannot exceed maximum memory."
    return None


def validate_mc_version(version: str) -> str | None:
    if version and not re.match(r"^\d+\.\d+(\.\d+)?$", version):
        return "Minecraft version must match format like 1.21 or 1.21.4."
    return None


def validate_server_type(server_type: str) -> str | None:
    valid = {t.value for t in ServerType}
    if server_type not in valid:
        return f"Invalid server type. Must be one of: {', '.join(sorted(valid))}."
    return None


def validate_mod_install(source: str, project_id: str) -> str | None:
    if not source or source not in ("modrinth",):
        return "Invalid mod source."
    if not project_id or not re.match(r"^[a-zA-Z0-9_-]+$", project_id):
        return "Invalid project ID."
    return None


def validate_file_content_size(content: str, max_bytes: int) -> str | None:
    if len(content.encode("utf-8")) > max_bytes:
        return f"File content exceeds maximum allowed size ({max_bytes // 1024} KB)."
    return None


def validate_command_length(command: str, max_len: int = 500) -> str | None:
    if len(command) > max_len:
        return f"Command must be {max_len} characters or fewer."
    return None
