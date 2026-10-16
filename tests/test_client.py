"""Client tests against a fake battery server on localhost."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import pytest

from custom_components.samduo.client import SamduoClient
from custom_components.samduo.const import SILENT_READS_BEFORE_RECYCLE

pytestmark = pytest.mark.usefixtures("socket_enabled")

SN = "E01TEST"


def reply(request: dict, service_id: str, result: dict, code: int = 200) -> bytes:
    message = {
        "header": f"triplus/ESD/{SN}/thing/action/execute_response",
        "payload": {
            "ver": "1.0",
            "msgId": request["payload"]["msgId"],
            "contentType": "json",
            "ts": 1,
            "code": code,
            "services": [{"serviceId": service_id, "result": result}],
        },
    }
    return json.dumps(message, separators=(",", ":")).encode()


class FakeBattery:
    """Speaks the Open API over a real socket. ``handler`` returns the bytes
    to send for a request, or None to stay silent."""

    def __init__(self, handler: Callable[[dict], bytes | None]) -> None:
        self.handler = handler
        self.connections = 0
        self.requests: list[dict] = []
        self.server: asyncio.AbstractServer | None = None

    async def __aenter__(self) -> FakeBattery:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.sockets[0].getsockname()[1]

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while chunk := await reader.read(4096):
                request = json.loads(chunk)
                self.requests.append(request)
                response = self.handler(request)
                if response is not None:
                    writer.write(response)
                    await writer.drain()
        finally:
            writer.close()


async def test_request_roundtrip_learns_serial() -> None:
    def handler(request: dict) -> bytes:
        return reply(request, "22600", {"bms_soc": 450, "inv_gridp": -799})

    async with FakeBattery(handler) as battery:
        client = SamduoClient("127.0.0.1", battery.port, response_timeout=2)
        await client.async_connect()
        data = await client.get_device_data()
        assert data == {"battery_soc": 45.0, "battery_power": -799}
        assert client.device_serial == SN
        # The learned serial is used in the next request header.
        await client.get_power_config()
        assert battery.requests[-1]["header"] == f"triplus/ESD/{SN}/thing/action/execute"
        assert battery.connections == 1
        await client.async_disconnect()


async def test_split_frames_and_stale_replies() -> None:
    """A reply split over two writes, preceded by a stale reply, is still matched."""

    def handler(request: dict) -> bytes:
        stale = dict(request)
        stale["payload"] = {**request["payload"], "msgId": "0"}
        return reply(stale, "22046", {"power": 1}) + reply(request, "22046", {"power": 800, "timeoutS": 60})

    async with FakeBattery(handler) as battery:
        client = SamduoClient("127.0.0.1", battery.port, response_timeout=2)
        config = await client.get_power_config()
        assert config == {"control_power": 800, "control_timeout": 60}
        await client.async_disconnect()


async def test_error_code_returns_none() -> None:
    def handler(request: dict) -> bytes:
        return reply(request, "22045", {}, code=400)

    async with FakeBattery(handler) as battery:
        client = SamduoClient("127.0.0.1", battery.port, response_timeout=2)
        assert await client.set_power_control(100, 60) is None
        await client.async_disconnect()


async def test_silent_device_recycles_socket() -> None:
    async with FakeBattery(lambda request: None) as battery:
        client = SamduoClient("127.0.0.1", battery.port, response_timeout=0.05)
        for _ in range(SILENT_READS_BEFORE_RECYCLE):
            assert await client.get_device_data() is None
        assert not client.connected
        await asyncio.sleep(0)
        assert battery.connections == 1
        await client.async_disconnect()


async def test_connection_refused_backs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("custom_components.samduo.client.asyncio.sleep", fake_sleep)
    client = SamduoClient("127.0.0.1", 1, connect_timeout=0.5, response_timeout=0.5)
    assert await client.get_device_data() is None
    assert await client.get_device_data() is None
    assert client.consecutive_failures == 2
    assert sleeps == [2.0, 4.0]
    with pytest.raises(OSError):
        await client.async_connect()


async def test_rejected_params_raise_before_sending() -> None:
    client = SamduoClient("127.0.0.1", 1)
    with pytest.raises(ValueError):
        await client.set_power_control(9000, 60)
