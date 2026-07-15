import asyncio
import logging
import struct

logger = logging.getLogger(__name__)

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


class RCONClient:
    def __init__(self):
        self._reader = None
        self._writer = None
        self._request_id = 0

    async def connect(
        self, host: str, port: int, password: str, timeout: float = 5.0
    ) -> bool:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
        except (OSError, asyncio.TimeoutError) as e:
            logger.error(f"RCON connection failed: {e}")
            return False

        self._request_id += 1
        auth_id = self._request_id
        packet = self._encode_packet(auth_id, SERVERDATA_AUTH, password)
        self._writer.write(packet)
        await self._writer.drain()

        try:
            req_id, ptype, _ = await asyncio.wait_for(
                self._read_packet(), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.error("RCON auth timed out")
            return False

        if req_id == -1:
            logger.error("RCON authentication failed")
            return False

        return True

    async def send_command(self, command: str, timeout: float = 10.0) -> str:
        if not self._writer:
            raise ConnectionError("Not connected")

        self._request_id += 1
        cmd_id = self._request_id
        packet = self._encode_packet(cmd_id, SERVERDATA_EXECCOMMAND, command)
        self._writer.write(packet)
        await self._writer.drain()

        response_parts = []
        try:
            while True:
                req_id, ptype, payload = await asyncio.wait_for(
                    self._read_packet(), timeout=timeout
                )
                if req_id == cmd_id:
                    response_parts.append(payload)
                    if len(payload) < 4096:
                        break
                else:
                    break
        except asyncio.TimeoutError:
            pass

        return "".join(response_parts)

    async def close(self):
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    def _encode_packet(self, request_id: int, packet_type: int, payload: str) -> bytes:
        body = (
            struct.pack("<ii", request_id, packet_type)
            + payload.encode("utf-8")
            + b"\x00\x00"
        )
        return struct.pack("<i", len(body)) + body

    async def _read_packet(self) -> tuple[int, int, str]:
        length_data = await self._reader.readexactly(4)
        length = struct.unpack("<i", length_data)[0]
        data = await self._reader.readexactly(length)
        request_id = struct.unpack("<i", data[0:4])[0]
        packet_type = struct.unpack("<i", data[4:8])[0]
        payload = data[8:-2].decode("utf-8", errors="replace")
        return request_id, packet_type, payload
