import logging
import shutil
import time
from datetime import datetime, timedelta, timezone

import psutil

from app.config import settings

logger = logging.getLogger(__name__)


class ResourceMonitor:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._cache = {}
            cls._instance._cache_time = {}
            cls._instance._cache_ttl = 3
            cls._instance._primed_pids: set[int] = set()
            psutil.cpu_percent(interval=0.1)
        return cls._instance

    def _is_cache_valid(self, key: str) -> bool:
        return time.time() - self._cache_time.get(key, 0) < self._cache_ttl

    def get_system_stats(self) -> dict:
        if self._is_cache_valid("system"):
            return self._cache["system"]

        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            disk = shutil.disk_usage(settings.servers_dir)

            stats = {
                "cpu_percent": cpu,
                "ram_total_mb": round(mem.total / (1024 * 1024)),
                "ram_used_mb": round(mem.used / (1024 * 1024)),
                "ram_percent": mem.percent,
                "disk_total_gb": round(disk.total / (1024**3), 1),
                "disk_used_gb": round(disk.used / (1024**3), 1),
                "disk_free_gb": round(disk.free / (1024**3), 1),
                "disk_percent": round(disk.used / disk.total * 100, 1)
                if disk.total
                else 0,
            }
        except Exception as e:
            logger.error(f"Failed to get system stats: {e}")
            stats = {
                "cpu_percent": 0,
                "ram_total_mb": 0,
                "ram_used_mb": 0,
                "ram_percent": 0,
                "disk_total_gb": 0,
                "disk_used_gb": 0,
                "disk_free_gb": 0,
                "disk_percent": 0,
            }

        self._cache["system"] = stats
        self._cache_time["system"] = time.time()
        return stats

    def get_process_stats(self, pid: int) -> dict | None:
        cache_key = f"process_{pid}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key]

        try:
            proc = psutil.Process(pid)

            if pid not in self._primed_pids:
                proc.cpu_percent(interval=0.1)
                self._primed_pids.add(pid)

            children = proc.children(recursive=True)

            total_mem = proc.memory_info().rss
            total_cpu = proc.cpu_percent(interval=None)
            child_count = len(children)

            for child in children:
                try:
                    if child.pid not in self._primed_pids:
                        child.cpu_percent(interval=0.1)
                        self._primed_pids.add(child.pid)
                    total_mem += child.memory_info().rss
                    total_cpu += child.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            stats = {
                "cpu_percent": round(total_cpu, 1),
                "ram_mb": round(total_mem / (1024 * 1024), 1),
                "pid": pid,
                "child_count": child_count,
            }
        except psutil.NoSuchProcess:
            return None
        except psutil.AccessDenied:
            stats = {
                "cpu_percent": 0,
                "ram_mb": 0,
                "pid": pid,
                "child_count": 0,
            }

        self._cache[cache_key] = stats
        self._cache_time[cache_key] = time.time()
        return stats

    def clear_process_cache(self, pid: int) -> None:
        """Remove cached stats and primed state for a process that has exited."""
        self._primed_pids.discard(pid)
        cache_key = f"process_{pid}"
        self._cache.pop(cache_key, None)
        self._cache_time.pop(cache_key, None)

    async def collect_metrics(self):
        from app.database import async_session
        from app.models.metric import MetricSnapshot
        from app.services.server_manager import server_manager

        async with async_session() as session:
            sys_stats = self.get_system_stats()
            session.add(
                MetricSnapshot(
                    server_id=None,
                    cpu_percent=sys_stats["cpu_percent"],
                    memory_mb=sys_stats["ram_used_mb"],
                )
            )

            for server_id, proc in list(server_manager.processes.items()):
                stats = self.get_process_stats(proc.process.pid)
                if stats:
                    session.add(
                        MetricSnapshot(
                            server_id=server_id,
                            cpu_percent=stats["cpu_percent"],
                            memory_mb=stats["ram_mb"],
                        )
                    )

            await session.commit()

    async def cleanup_old_metrics(self):
        from sqlalchemy import delete

        from app.database import async_session
        from app.models.metric import MetricSnapshot

        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.metric_retention_days
        )
        async with async_session() as session:
            await session.execute(
                delete(MetricSnapshot).where(MetricSnapshot.timestamp < cutoff)
            )
            await session.commit()


resource_monitor = ResourceMonitor()
