"""Embedded SFTP server for direct file access to game server directories."""

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

try:
    import asyncssh

    class GSMSFTPServer(asyncssh.SFTPServer):
        """SFTP handler that chroots connections to the servers directory."""

        def __init__(self, conn):
            root = conn.get_extra_info("server_path", settings.servers_dir)
            super().__init__(conn, chroot=root)

    class GSMSSHServer(asyncssh.SSHServer):
        """SSH server that authenticates against the GSM user database."""

        def connection_made(self, conn):
            self._conn = conn

        def begin_auth(self, username):
            return True

        def password_auth_supported(self):
            return True

        async def validate_password(self, username, password):
            from app.database import async_session
            from app.models.user import User
            from sqlalchemy import select
            from app.services.auth import pwd_context

            try:
                async with async_session() as session:
                    result = await session.execute(
                        select(User).where(User.username == username)
                    )
                    user = result.scalar_one_or_none()
                    if user and pwd_context.verify(password, user.password_hash):
                        if user.role != "admin":
                            logger.warning(
                                f"SFTP login denied for non-admin user '{username}'"
                            )
                            return False
                        self._conn.set_extra_info(server_path=settings.servers_dir)
                        return True
            except Exception as e:
                logger.warning(f"SFTP auth error: {e}")
            return False

    class SFTPManager:
        def __init__(self):
            self._server = None

        async def start(self):
            if not settings.sftp_enabled:
                return

            key_path = Path(settings.sftp_host_key_path)
            if not key_path.exists():
                key_path.parent.mkdir(parents=True, exist_ok=True)
                key = asyncssh.generate_private_key("ssh-rsa", 4096)
                key.write_private_key(str(key_path))
                logger.info(f"Generated SFTP host key at {key_path}")

            self._server = await asyncssh.create_server(
                GSMSSHServer,
                "",
                settings.sftp_port,
                server_host_keys=[str(key_path)],
                sftp_factory=GSMSFTPServer,
                process_factory=None,
            )
            logger.info(f"SFTP server started on port {settings.sftp_port}")

        async def stop(self):
            if self._server:
                self._server.close()
                await self._server.wait_closed()
                logger.info("SFTP server stopped")

    sftp_manager = SFTPManager()

except ImportError:

    class SFTPManager:
        """Stub when asyncssh is not installed."""

        async def start(self):
            if settings.sftp_enabled:
                logger.warning(
                    "SFTP is enabled but asyncssh is not installed. "
                    "Install it with: pip install asyncssh"
                )

        async def stop(self):
            pass

    sftp_manager = SFTPManager()
