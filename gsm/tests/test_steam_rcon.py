import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.models.server import Server, ServerStatus, ServerType
from app.services.steam_rcon import SteamRCONService


@pytest.fixture
def steam_server():
    server = Server(
        name="Test CS Server",
        server_type=ServerType.STEAM,
        status=ServerStatus.STOPPED,
        path="/tmp/test",
        port=27015,
        query_port=27016,
        rcon_enabled=True,
        rcon_port=27017,
        rcon_password="secret",
    )
    return server


async def test_steam_rcon_sends_command(steam_server):
    service = SteamRCONService()
    with patch("app.services.steam_rcon.RCONClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.connect = AsyncMock(return_value=True)
        mock_client.send_command = AsyncMock(return_value="Server cvar value")
        mock_client.close = AsyncMock()
        mock_client_cls.return_value = mock_client

        result = await service.send_command(steam_server, "cvarlist")

    assert result["ok"] is True
    assert result["response"] == "Server cvar value"
    mock_client.connect.assert_awaited_once_with(
        "127.0.0.1", 27017, "secret", timeout=5.0
    )
    mock_client.send_command.assert_awaited_once_with("cvarlist", timeout=5.0)
    mock_client.close.assert_awaited()


async def test_steam_rcon_not_enabled(steam_server):
    steam_server.rcon_enabled = False
    service = SteamRCONService()
    result = await service.send_command(steam_server, "status")
    assert result["ok"] is False
    assert "not enabled" in result["error"]


async def test_steam_rcon_auth_failure(steam_server):
    service = SteamRCONService()
    with patch("app.services.steam_rcon.RCONClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.connect = AsyncMock(return_value=False)
        mock_client.close = AsyncMock()
        mock_client_cls.return_value = mock_client

        result = await service.send_command(steam_server, "status")

    assert result["ok"] is False
    assert "authentication failed" in result["error"]


async def test_steam_rcon_wrong_server_type(steam_server):
    steam_server.server_type = ServerType.MINECRAFT_JAVA
    service = SteamRCONService()
    result = await service.send_command(steam_server, "help")
    assert result["ok"] is False
    assert "only supported for Steam servers" in result["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
