"""Docker container isolation for game servers (optional)."""

import logging

from app.config import settings

logger = logging.getLogger(__name__)

try:
    import aiodocker

    class DockerManager:
        def __init__(self):
            self._docker = None

        async def _get_client(self):
            if self._docker is None:
                self._docker = aiodocker.Docker()
            return self._docker

        async def create_and_start(self, server) -> str:
            """Create and start a Docker container for a server. Returns container_id."""
            docker = await self._get_client()
            container_name = f"gsm-{server.id}"
            from app.services.steamcmd import build_runtime_command

            config = {
                "Image": settings.docker_default_image,
                "Cmd": build_runtime_command(server),
                "WorkingDir": "/server",
                "AttachStdin": True,
                "OpenStdin": True,
                "Tty": False,
                "HostConfig": {
                    "Binds": [f"{server.path}:/server"],
                    "NetworkMode": settings.docker_network_mode,
                },
            }

            # Pass custom environment variables
            if server.environment_vars:
                import json

                try:
                    custom_env = json.loads(server.environment_vars)
                    if isinstance(custom_env, dict):
                        config["Env"] = [f"{k}={v}" for k, v in custom_env.items()]
                except (json.JSONDecodeError, TypeError):
                    pass

            if settings.docker_network_mode == "bridge":
                config["HostConfig"]["PortBindings"] = {
                    f"{server.port}/tcp": [{"HostPort": str(server.port)}],
                    f"{server.port}/udp": [{"HostPort": str(server.port)}],
                }
                if server.rcon_port:
                    config["HostConfig"]["PortBindings"][f"{server.rcon_port}/tcp"] = [
                        {"HostPort": str(server.rcon_port)}
                    ]

            if server.cpu_limit:
                config["HostConfig"]["NanoCpus"] = int(server.cpu_limit * 1e9)
            if server.memory_limit_mb:
                config["HostConfig"]["Memory"] = server.memory_limit_mb * 1024 * 1024

            try:
                old = await docker.containers.get(container_name)
                await old.delete(force=True)
            except aiodocker.exceptions.DockerError:
                pass

            container = await docker.containers.create_or_replace(
                container_name, config
            )
            await container.start()
            info = await container.show()
            return info["Id"]

        async def stop(self, container_id: str, timeout: int = 30):
            """Stop a container gracefully."""
            docker = await self._get_client()
            try:
                container = await docker.containers.get(container_id)
                await container.stop(t=timeout)
            except aiodocker.exceptions.DockerError as e:
                logger.warning(f"Error stopping container {container_id}: {e}")

        async def send_command(self, container_id: str, command: str):
            """Send a command to a container via exec."""
            import shlex

            docker = await self._get_client()
            try:
                container = await docker.containers.get(container_id)
                exec_instance = await container.exec(
                    cmd=["sh", "-c", f"echo {shlex.quote(command)} > /proc/1/fd/0"],
                    stdin=True,
                )
                return await exec_instance.start()
            except aiodocker.exceptions.DockerError as e:
                logger.warning(
                    f"Error sending command to container {container_id}: {e}"
                )
                return None

        async def get_logs(self, container_id: str, tail: int = 100) -> str:
            """Get container logs."""
            docker = await self._get_client()
            try:
                container = await docker.containers.get(container_id)
                logs = await container.log(stdout=True, stderr=True, tail=tail)
                return "\n".join(logs) if isinstance(logs, list) else str(logs)
            except aiodocker.exceptions.DockerError:
                return ""

        async def get_stats(self, container_id: str) -> dict | None:
            """Get container CPU/memory stats."""
            docker = await self._get_client()
            try:
                container = await docker.containers.get(container_id)
                stats = await container.stats(stream=False)
                if isinstance(stats, list):
                    if not stats:
                        return None
                    s = stats[0]
                elif isinstance(stats, dict):
                    s = stats
                else:
                    return None
                cpu_delta = s.get("cpu_stats", {}).get("cpu_usage", {}).get(
                    "total_usage", 0
                ) - s.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
                system_delta = s.get("cpu_stats", {}).get(
                    "system_cpu_usage", 0
                ) - s.get("precpu_stats", {}).get("system_cpu_usage", 0)
                num_cpus = s.get("cpu_stats", {}).get("online_cpus", 1)
                cpu_percent = (
                    (cpu_delta / system_delta * num_cpus * 100.0)
                    if system_delta > 0
                    else 0.0
                )
                memory_usage = s.get("memory_stats", {}).get("usage", 0) / (1024 * 1024)
                return {
                    "cpu_percent": round(cpu_percent, 1),
                    "memory_mb": round(memory_usage, 1),
                }
            except (aiodocker.exceptions.DockerError, Exception) as e:
                logger.warning(f"Error getting stats for {container_id}: {e}")
            return None

        async def is_running(self, container_id: str) -> bool:
            """Check if a container is running."""
            docker = await self._get_client()
            try:
                container = await docker.containers.get(container_id)
                info = await container.show()
                return info.get("State", {}).get("Running", False)
            except aiodocker.exceptions.DockerError:
                return False

        async def remove(self, container_id: str):
            """Remove a stopped container."""
            docker = await self._get_client()
            try:
                container = await docker.containers.get(container_id)
                await container.delete(force=True)
            except aiodocker.exceptions.DockerError as e:
                logger.warning(f"Error removing container {container_id}: {e}")

        async def close(self):
            if self._docker:
                await self._docker.close()
                self._docker = None

    docker_manager = DockerManager()

except ImportError:

    class DockerManager:
        """Stub when aiodocker is not installed."""

        async def create_and_start(self, server) -> str:
            raise RuntimeError(
                "aiodocker is not installed. Install with: pip install aiodocker"
            )

        async def stop(self, container_id: str, timeout: int = 30):
            pass

        async def send_command(self, container_id: str, command: str):
            pass

        async def get_logs(self, container_id: str, tail: int = 100) -> str:
            return ""

        async def get_stats(self, container_id: str) -> dict | None:
            return None

        async def is_running(self, container_id: str) -> bool:
            return False

        async def remove(self, container_id: str):
            pass

        async def close(self):
            pass

    docker_manager = DockerManager()
