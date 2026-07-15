import struct

import pytest

from app.services.query_protocol import SteamQueryProtocol


def build_a2s_info_response(
    name="Test Server",
    map_name="de_dust2",
    folder="csgo",
    game="Counter-Strike: Global Offensive",
    app_id=730,
    players=10,
    max_players=32,
    bots=0,
    server_type="d",
    environment="l",
    visibility=0,
    vac=1,
):
    payload = struct.pack("B", 17)  # protocol
    payload += name.encode("utf-8") + b"\x00"
    payload += map_name.encode("utf-8") + b"\x00"
    payload += folder.encode("utf-8") + b"\x00"
    payload += game.encode("utf-8") + b"\x00"
    payload += struct.pack("<h", app_id)
    payload += struct.pack("B", players)
    payload += struct.pack("B", max_players)
    payload += struct.pack("B", bots)
    payload += server_type.encode("utf-8")
    payload += environment.encode("utf-8")
    payload += struct.pack("B", visibility)
    payload += struct.pack("B", vac)
    return b"\xff\xff\xff\xff\x49" + payload


def test_parse_info_response():
    response = build_a2s_info_response(
        name="My CS Server",
        map_name="de_inferno",
        players=5,
        max_players=20,
    )
    protocol = SteamQueryProtocol()
    info = protocol._parse_info_response(response[5:])
    assert info is not None
    assert info["name"] == "My CS Server"
    assert info["map"] == "de_inferno"
    assert info["players"] == 5
    assert info["max_players"] == 20
    assert info["vac"] == 1


def test_parse_info_response_invalid():
    protocol = SteamQueryProtocol()
    assert protocol._parse_info_response(b"\x00") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
