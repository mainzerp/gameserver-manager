import asyncio
import struct
import unittest

from app.services.rcon_client import (
    RCONClient,
    SERVERDATA_AUTH,
    SERVERDATA_EXECCOMMAND,
    SERVERDATA_RESPONSE_VALUE,
)


class EncodePacketTests(unittest.TestCase):
    def setUp(self):
        self.client = RCONClient()

    def test_packet_length_field(self):
        packet = self.client._encode_packet(1, SERVERDATA_AUTH, "password123")
        body_len = struct.unpack("<i", packet[0:4])[0]
        self.assertEqual(body_len, len(packet) - 4)

    def test_packet_request_id(self):
        packet = self.client._encode_packet(42, SERVERDATA_AUTH, "pw")
        request_id = struct.unpack("<i", packet[4:8])[0]
        self.assertEqual(request_id, 42)

    def test_packet_type(self):
        packet = self.client._encode_packet(1, SERVERDATA_EXECCOMMAND, "list")
        ptype = struct.unpack("<i", packet[8:12])[0]
        self.assertEqual(ptype, SERVERDATA_EXECCOMMAND)

    def test_packet_payload_and_terminators(self):
        payload = "say hello"
        packet = self.client._encode_packet(1, SERVERDATA_AUTH, payload)
        self.assertEqual(packet[12:], payload.encode("utf-8") + b"\x00\x00")

    def test_empty_payload(self):
        packet = self.client._encode_packet(0, SERVERDATA_RESPONSE_VALUE, "")
        body_len = struct.unpack("<i", packet[0:4])[0]
        self.assertEqual(body_len, 10)
        self.assertEqual(packet[12:], b"\x00\x00")

    def test_unicode_payload_encoded_as_utf8(self):
        payload = "kick player_1"
        packet = self.client._encode_packet(3, SERVERDATA_EXECCOMMAND, payload)
        decoded_payload = packet[12:-2].decode("utf-8")
        self.assertEqual(decoded_payload, payload)


class ReadPacketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = RCONClient()

    async def _feed(self, data: bytes):
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        self.client._reader = reader

    async def test_read_packet_decodes_correctly(self):
        encoded = self.client._encode_packet(
            7, SERVERDATA_RESPONSE_VALUE, "test response"
        )
        await self._feed(encoded)

        req_id, ptype, payload = await self.client._read_packet()
        self.assertEqual(req_id, 7)
        self.assertEqual(ptype, SERVERDATA_RESPONSE_VALUE)
        self.assertEqual(payload, "test response")

    async def test_read_packet_empty_payload(self):
        encoded = self.client._encode_packet(0, SERVERDATA_RESPONSE_VALUE, "")
        await self._feed(encoded)

        req_id, ptype, payload = await self.client._read_packet()
        self.assertEqual(req_id, 0)
        self.assertEqual(ptype, SERVERDATA_RESPONSE_VALUE)
        self.assertEqual(payload, "")

    async def test_read_packet_truncated_raises(self):
        encoded = self.client._encode_packet(1, SERVERDATA_AUTH, "abc")
        await self._feed(encoded[:-1])

        with self.assertRaises(asyncio.IncompleteReadError):
            await self.client._read_packet()


class SendCommandNotConnectedTests(unittest.TestCase):
    def test_send_command_raises_when_not_connected(self):
        client = RCONClient()
        with self.assertRaises(ConnectionError):
            asyncio.run(client.send_command("test"))


if __name__ == "__main__":
    unittest.main()
