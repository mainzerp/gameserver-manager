import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class WorldManager:
    STANDARD_WORLD_DIRS = ["world", "world_nether", "world_the_end"]

    def _parse_properties(self, path: str) -> dict[str, str]:
        props = {}
        props_path = os.path.join(path, "server.properties")
        if not os.path.exists(props_path):
            return props
        with open(props_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    props[key.strip()] = value.strip()
        return props

    def _write_properties(self, path: str, props: dict[str, str]):
        props_path = os.path.join(path, "server.properties")
        lines = []
        if os.path.exists(props_path):
            with open(props_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

        updated_keys = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in props:
                    new_lines.append(f"{key}={props[key]}\n")
                    updated_keys.add(key)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        for key, value in props.items():
            if key not in updated_keys:
                new_lines.append(f"{key}={value}\n")

        with open(props_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    def get_level_name(self, server_path: str) -> str:
        props = self._parse_properties(server_path)
        return props.get("level-name", "world")

    def list_worlds(self, server_path: str) -> list[dict]:
        worlds = []
        active_level = self.get_level_name(server_path)
        base = Path(server_path)

        if not base.exists():
            return worlds

        for item in sorted(base.iterdir()):
            if not item.is_dir():
                continue
            level_dat = item / "level.dat"
            if not level_dat.exists() and item.name not in self.STANDARD_WORLD_DIRS:
                continue
            if not level_dat.exists():
                continue

            size_bytes = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
            size_mb = round(size_bytes / (1024 * 1024), 1)

            worlds.append(
                {
                    "name": item.name,
                    "size_mb": size_mb,
                    "is_active": item.name == active_level
                    or item.name in self.STANDARD_WORLD_DIRS,
                }
            )

        return worlds

    async def reset_world(
        self,
        server_id: int,
        server_path: str,
        world_name: str,
        create_backup: bool = True,
    ) -> dict:
        from app.services.backup_manager import backup_manager
        from app.services.server_manager import server_manager

        if server_manager.is_running(server_id):
            return {
                "ok": False,
                "error": "Server must be stopped before resetting a world",
            }

        if ".." in world_name or "/" in world_name or "\\" in world_name:
            return {"ok": False, "error": "Invalid world name"}

        world_path = Path(server_path) / world_name
        resolved = world_path.resolve()
        base_resolved = Path(server_path).resolve()
        if not str(resolved).startswith(str(base_resolved)):
            return {"ok": False, "error": "Invalid world path"}

        if not world_path.exists() or not world_path.is_dir():
            return {"ok": False, "error": "World directory not found"}

        if create_backup:
            try:
                await backup_manager.create_backup(
                    server_id, note=f"Pre-reset backup ({world_name})"
                )
            except Exception as e:
                logger.warning(f"Backup before world reset failed: {e}")

        try:
            shutil.rmtree(world_path)
            logger.info(f"World '{world_name}' deleted for server {server_id}")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def switch_world(self, server_path: str, level_name: str) -> dict:
        if ".." in level_name or "/" in level_name or "\\" in level_name:
            return {"ok": False, "error": "Invalid level name"}
        if not level_name or len(level_name) > 64:
            return {"ok": False, "error": "Level name must be 1-64 characters"}
        self._write_properties(server_path, {"level-name": level_name})
        return {"ok": True}


world_manager = WorldManager()
