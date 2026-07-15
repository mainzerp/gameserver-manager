import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.server import Server
from app.services.resource_monitor import resource_monitor
from app.services.server_manager import server_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(request: Request, db: AsyncSession = Depends(get_db)):
    if not settings.prometheus_enabled:
        return PlainTextResponse("Prometheus metrics disabled\n", status_code=404)

    from app.services.auth import get_api_user, get_current_user

    user = await get_api_user(request)
    if not user:
        try:
            user = await get_current_user(request)
        except Exception:
            return PlainTextResponse("Unauthorized\n", status_code=401)

    lines = []
    system = resource_monitor.get_system_stats()

    lines.append("# HELP gsm_system_cpu_percent System CPU usage percentage")
    lines.append("# TYPE gsm_system_cpu_percent gauge")
    lines.append(f"gsm_system_cpu_percent {system['cpu_percent']}")

    lines.append("# HELP gsm_system_ram_used_bytes System RAM used in bytes")
    lines.append("# TYPE gsm_system_ram_used_bytes gauge")
    lines.append(f"gsm_system_ram_used_bytes {system['ram_used_mb'] * 1024 * 1024}")

    lines.append("# HELP gsm_system_ram_total_bytes System RAM total in bytes")
    lines.append("# TYPE gsm_system_ram_total_bytes gauge")
    lines.append(f"gsm_system_ram_total_bytes {system['ram_total_mb'] * 1024 * 1024}")

    lines.append("# HELP gsm_system_disk_used_bytes Disk used in bytes")
    lines.append("# TYPE gsm_system_disk_used_bytes gauge")
    lines.append(f"gsm_system_disk_used_bytes {int(system['disk_used_gb'] * 1024**3)}")

    lines.append("# HELP gsm_system_disk_total_bytes Disk total in bytes")
    lines.append("# TYPE gsm_system_disk_total_bytes gauge")
    lines.append(
        f"gsm_system_disk_total_bytes {int(system['disk_total_gb'] * 1024**3)}"
    )

    result = await db.execute(select(Server))
    servers = result.scalars().all()

    lines.append("# HELP gsm_servers_total Total number of managed servers")
    lines.append("# TYPE gsm_servers_total gauge")
    lines.append(f"gsm_servers_total {len(servers)}")

    running_count = sum(1 for s in servers if server_manager.is_running(s.id))
    lines.append("# HELP gsm_servers_running Number of currently running servers")
    lines.append("# TYPE gsm_servers_running gauge")
    lines.append(f"gsm_servers_running {running_count}")

    lines.append("# HELP gsm_server_status Server status (1=running, 0=stopped)")
    lines.append("# TYPE gsm_server_status gauge")
    lines.append("# HELP gsm_server_cpu_percent Server CPU usage percentage")
    lines.append("# TYPE gsm_server_cpu_percent gauge")
    lines.append("# HELP gsm_server_ram_used_bytes Server RAM usage in bytes")
    lines.append("# TYPE gsm_server_ram_used_bytes gauge")

    for s in servers:
        labels = f'server_name="{s.name}",server_type="{s.server_type.value}"'
        is_running = 1 if server_manager.is_running(s.id) else 0
        lines.append(f"gsm_server_status{{{labels}}} {is_running}")

        if is_running:
            sp = server_manager.processes.get(s.id)
            if sp and sp.process.pid:
                stats = resource_monitor.get_process_stats(sp.process.pid)
                if stats:
                    lines.append(
                        f"gsm_server_cpu_percent{{{labels}}} {stats.get('cpu_percent', 0)}"
                    )
                    lines.append(
                        f"gsm_server_ram_used_bytes{{{labels}}} {int(stats.get('ram_mb', 0) * 1024 * 1024)}"
                    )

    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
