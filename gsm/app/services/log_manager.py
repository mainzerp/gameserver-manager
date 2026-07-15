import asyncio
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class LogManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _get_log_dir(self, server_name: str) -> Path:
        log_dir = Path(settings.servers_dir) / server_name / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    def _get_log_path(self, server_name: str) -> Path:
        return self._get_log_dir(server_name) / "latest.log"

    async def write_line(self, server_name: str, line: str):
        try:
            await asyncio.to_thread(self._sync_write_line, server_name, line)
        except Exception as e:
            logger.error(f"Failed to write log line for {server_name}: {e}")

    def _sync_write_line(self, server_name: str, line: str):
        log_path = self._get_log_path(server_name)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if log_path.stat().st_size > settings.log_max_size_mb * 1024 * 1024:
            self._rotate(server_name)

    def _rotate(self, server_name: str):
        log_dir = self._get_log_dir(server_name)
        max_files = settings.log_max_files

        oldest = log_dir / f"latest.log.{max_files}"
        if oldest.exists():
            oldest.unlink()

        for i in range(max_files - 1, 0, -1):
            src = log_dir / f"latest.log.{i}"
            dst = log_dir / f"latest.log.{i + 1}"
            if src.exists():
                src.rename(dst)

        current = log_dir / "latest.log"
        if current.exists():
            current.rename(log_dir / "latest.log.1")

    def get_logs(self, server_name: str, lines: int = 500) -> list[str]:
        result = []
        log_dir = self._get_log_dir(server_name)
        log_path = log_dir / "latest.log"

        files_to_read = []
        if log_path.exists():
            files_to_read.append(log_path)
        for i in range(1, settings.log_max_files + 1):
            rotated = log_dir / f"latest.log.{i}"
            if rotated.exists():
                files_to_read.append(rotated)

        for fpath in reversed(files_to_read):
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    file_lines = f.read().splitlines()
                result.extend(file_lines)
            except Exception:
                pass

        return result[-lines:]

    def search_logs(
        self, server_name: str, query: str, max_results: int = 200
    ) -> list[dict]:
        results = []
        log_dir = self._get_log_dir(server_name)
        query_lower = query.lower()

        files_to_search = []
        log_path = log_dir / "latest.log"
        if log_path.exists():
            files_to_search.append(log_path)
        for i in range(1, settings.log_max_files + 1):
            rotated = log_dir / f"latest.log.{i}"
            if rotated.exists():
                files_to_search.append(rotated)

        for fpath in files_to_search:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, 1):
                        if query_lower in line.lower():
                            results.append(
                                {
                                    "line": line.rstrip(),
                                    "file": fpath.name,
                                    "line_number": line_num,
                                }
                            )
                            if len(results) >= max_results:
                                return results
            except Exception:
                pass

        return results


log_manager = LogManager()
