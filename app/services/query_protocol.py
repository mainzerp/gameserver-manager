"""
Game server query protocols for retrieving online player information.

Implements:
- Minecraft Server List Ping (SLP) via TCP
- Steam A2S_PLAYER via UDP
"""

import asyncio
import json
import logging
import struct

logger = logging.getLogger(__name__)


class MinecraftQueryProtocol:
    """Minecraft Server List Ping (SLP) protocol implementation."""

    async def query(self, host: str, port: int, timeout: float = 5.0) -> dict | None:
        """Query Minecraft server for status via SLP.

        Returns {"online": int, "max": int, "players": [{"name": str}], "motd": str}
        or None on failure.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
        except (OSError, asyncio.TimeoutError) as e:
            logger.debug(f"SLP connection to {host}:{port} failed: {e}")
            return None

        try:
            # Build handshake packet (id=0x00)
            handshake_data = self._encode_varint(47)  # protocol version
            handshake_data += self._encode_string(host)
            handshake_data += struct.pack(">H", port)
            handshake_data += self._encode_varint(1)  # next state = status
            handshake_packet = self._encode_varint(0x00) + handshake_data
            writer.write(self._encode_varint(len(handshake_packet)) + handshake_packet)

            # Send status request packet (id=0x00)
            status_packet = self._encode_varint(0x00)
            writer.write(self._encode_varint(len(status_packet)) + status_packet)
            await writer.drain()

            # Read response
            _packet_length = await self._read_varint(reader, timeout)
            _packet_id = await self._read_varint(reader, timeout)

            json_length = await self._read_varint(reader, timeout)
            json_data = await asyncio.wait_for(
                reader.readexactly(json_length), timeout=timeout
            )

            data = json.loads(json_data.decode("utf-8"))

            players_sample = []
            if "players" in data and "sample" in data["players"]:
                for p in data["players"]["sample"]:
                    players_sample.append({"name": p.get("name", "Unknown")})

            motd = ""
            if "description" in data:
                desc = data["description"]
                if isinstance(desc, str):
                    motd = desc
                elif isinstance(desc, dict):
                    motd = desc.get("text", "")

            return {
                "online": data.get("players", {}).get("online", 0),
                "max": data.get("players", {}).get("max", 0),
                "players": players_sample,
                "motd": motd,
            }

        except Exception as e:
            logger.debug(f"SLP query to {host}:{port} failed: {e}")
            return None
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _encode_varint(self, value: int) -> bytes:
        result = bytearray()
        while True:
            byte = value & 0x7F
            value >>= 7
            if value != 0:
                byte |= 0x80
            result.append(byte)
            if value == 0:
                break
        return bytes(result)

    def _encode_string(self, value: str) -> bytes:
        encoded = value.encode("utf-8")
        return self._encode_varint(len(encoded)) + encoded

    async def _read_varint(self, reader: asyncio.StreamReader, timeout: float) -> int:
        result = 0
        num_read = 0
        while True:
            data = await asyncio.wait_for(reader.readexactly(1), timeout=timeout)
            byte = data[0]
            result |= (byte & 0x7F) << (7 * num_read)
            num_read += 1
            if num_read > 5:
                raise ValueError("VarInt too big")
            if (byte & 0x80) == 0:
                break
        return result


class SteamQueryProtocol:
    """Steam A2S_INFO and A2S_PLAYER query protocols."""

    A2S_INFO = b"\xff\xff\xff\xff\x54Source Engine Query\x00"
    A2S_INFO_RESPONSE = 0x49
    A2S_PLAYER = b"\xff\xff\xff\xff\x55"
    A2S_PLAYER_RESPONSE = 0x44

    async def query_info(
        self, host: str, port: int, timeout: float = 5.0
    ) -> dict | None:
        """Query Steam server for status via A2S_INFO.

        Returns dict with name, map, folder, game, app_id, players, max_players,
        bots, visibility, vac, or None on failure.
        """
        loop = asyncio.get_event_loop()
        transport = None
        try:
            transport, protocol = await asyncio.wait_for(
                loop.create_datagram_endpoint(
                    lambda: _SteamProtocol(),
                    remote_addr=(host, port),
                ),
                timeout=timeout,
            )

            # Step 1: Send challenge request
            transport.sendto(self.A2S_INFO)
            data = await asyncio.wait_for(protocol.response_future, timeout=timeout)

            if len(data) < 5 or data[4] != 0x41:
                logger.debug(
                    "A2S_INFO unexpected challenge response from %s:%s", host, port
                )
                return None

            challenge = data[5:9]

            # Step 2: Send actual request with challenge
            protocol.reset()
            transport.sendto(self.A2S_INFO + challenge)
            data = await asyncio.wait_for(protocol.response_future, timeout=timeout)

            if len(data) < 6 or data[4] != self.A2S_INFO_RESPONSE:
                logger.debug("A2S_INFO unexpected response from %s:%s", host, port)
                return None

            return self._parse_info_response(data[5:])
        except Exception as e:
            logger.debug("A2S_INFO query to %s:%s failed: %s", host, port, e)
            return None
        finally:
            if transport:
                transport.close()

    def _parse_info_response(self, data: bytes) -> dict | None:
        offset = 0

        def read_byte() -> int:
            nonlocal offset
            if offset >= len(data):
                return 0
            value = data[offset]
            offset += 1
            return value

        def read_string() -> str:
            nonlocal offset
            end = data.find(b"\x00", offset)
            if end == -1:
                end = len(data)
            value = data[offset:end].decode("utf-8", errors="replace")
            offset = end + 1
            return value

        try:
            protocol = read_byte()
            name = read_string()
            map_name = read_string()
            folder = read_string()
            game = read_string()
            app_id = struct.unpack_from("<h", data, offset)[0]
            offset += 2
            players = read_byte()
            max_players = read_byte()
            bots = read_byte()
            server_type = chr(read_byte())
            environment = chr(read_byte())
            visibility = read_byte()
            vac = read_byte()
            return {
                "protocol": protocol,
                "name": name,
                "map": map_name,
                "folder": folder,
                "game": game,
                "app_id": app_id,
                "players": players,
                "max_players": max_players,
                "bots": bots,
                "server_type": server_type,
                "environment": environment,
                "visibility": visibility,
                "vac": vac,
            }
        except Exception as exc:
            logger.debug("Failed to parse A2S_INFO response: %s", exc)
            return None

    async def query_players(
        self, host: str, port: int, timeout: float = 5.0
    ) -> list[dict] | None:
        """Query Steam server for player list via A2S_PLAYER.

        Returns [{"name": str, "score": int, "duration": float}] or None.
        """
        loop = asyncio.get_event_loop()
        transport = None
        try:
            # Create UDP socket
            transport, protocol = await asyncio.wait_for(
                loop.create_datagram_endpoint(
                    lambda: _SteamProtocol(),
                    remote_addr=(host, port),
                ),
                timeout=timeout,
            )

            # Step 1: Send challenge request
            transport.sendto(self.A2S_PLAYER + b"\xff\xff\xff\xff")
            data = await asyncio.wait_for(protocol.response_future, timeout=timeout)

            if len(data) < 9 or data[4] != 0x41:
                logger.debug(
                    f"A2S_PLAYER unexpected challenge response from {host}:{port}"
                )
                return None

            challenge = data[5:9]

            # Step 2: Send actual player request with challenge
            protocol.reset()
            transport.sendto(self.A2S_PLAYER + challenge)
            data = await asyncio.wait_for(protocol.response_future, timeout=timeout)

            if len(data) < 6 or data[4] != self.A2S_PLAYER_RESPONSE:
                logger.debug(f"A2S_PLAYER unexpected response from {host}:{port}")
                return None

            # Parse player list
            num_players = data[5]
            offset = 6
            players = []
            for _ in range(num_players):
                if offset >= len(data):
                    break
                _index = data[offset]
                offset += 1

                # Read null-terminated string
                name_end = data.index(0x00, offset)
                name = data[offset:name_end].decode("utf-8", errors="replace")
                offset = name_end + 1

                if offset + 8 > len(data):
                    break
                score = struct.unpack_from("<i", data, offset)[0]
                offset += 4
                duration = struct.unpack_from("<f", data, offset)[0]
                offset += 4

                players.append(
                    {
                        "name": name,
                        "score": score,
                        "duration": round(duration, 1),
                    }
                )

            return players

        except Exception as e:
            logger.debug(f"A2S_PLAYER query to {host}:{port} failed: {e}")
            return None
        finally:
            if transport:
                transport.close()


class _SteamProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.response_future = asyncio.get_event_loop().create_future()

    def datagram_received(self, data, addr):
        if not self.response_future.done():
            self.response_future.set_result(data)

    def error_received(self, exc):
        if not self.response_future.done():
            self.response_future.set_exception(exc)

    def reset(self):
        self.response_future = asyncio.get_event_loop().create_future()


minecraft_query = MinecraftQueryProtocol()
steam_query = SteamQueryProtocol()
