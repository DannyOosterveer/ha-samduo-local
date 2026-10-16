"""Async TCP client for a SAMDUO Nex battery.

One persistent connection per client. The device serves exactly one TCP
client at a time: a second connection is accepted at the socket level but
never answered, so the integration keeps a single socket per battery and
never opens a parallel one.

Every public method returns ``None`` on failure instead of raising; the
coordinator decides what a missing reply means. Recovery (backoff and
reconnect) happens inside the client.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from . import protocol
from .const import (
    CONNECT_TIMEOUT,
    RECONNECT_BASE_COOLDOWN,
    RECONNECT_MAX_COOLDOWN,
    RESPONSE_TIMEOUT,
    SERVICE_CHECK_BACKUP,
    SERVICE_DEVICE_DATA,
    SERVICE_ENABLE_BACKUP,
    SERVICE_GET_POWER_CONFIG,
    SERVICE_SET_POWER,
    SILENT_READS_BEFORE_RECYCLE,
)

_LOGGER = logging.getLogger(__name__)


class _ReadTimeout(Exception):
    """The device accepted the request but never answered."""


class SamduoClient:
    def __init__(
        self,
        host: str,
        port: int,
        serial: str | None = None,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
        response_timeout: float = RESPONSE_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self._serial = serial or protocol.SN_PLACEHOLDER
        self.device_serial: str | None = serial
        self._connect_timeout = connect_timeout
        self._response_timeout = response_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._buffer = b""
        self._silent_reads = 0
        self._connection_failures = 0

    # ── Connection ─────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    @property
    def consecutive_failures(self) -> int:
        """Connection-failure streak; 0 means healthy."""
        return self._connection_failures

    async def async_connect(self) -> None:
        """Open the socket; raises on failure so setup can report it."""
        async with self._lock:
            await self._open()

    async def async_disconnect(self) -> None:
        async with self._lock:
            await self._close()

    async def _open(self) -> None:
        _LOGGER.debug("Connecting to %s:%s", self.host, self.port)
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self._connect_timeout
        )
        self._buffer = b""
        self._connection_failures = 0
        _LOGGER.debug("Connected to %s:%s", self.host, self.port)

    async def _close(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        self._buffer = b""
        if writer is None or writer.is_closing():
            return
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    def _cooldown(self) -> float:
        return min(RECONNECT_BASE_COOLDOWN * (2**self._connection_failures), RECONNECT_MAX_COOLDOWN)

    # ── Public API ─────────────────────────────────────────────────────────

    async def get_device_data(self) -> dict[str, Any] | None:
        result = await self.request(SERVICE_DEVICE_DATA)
        return protocol.scale_device_data(result) if result is not None else None

    async def get_power_config(self) -> dict[str, Any] | None:
        result = await self.request(SERVICE_GET_POWER_CONFIG)
        return protocol.scale_power_config(result) if result is not None else None

    async def get_backup_state(self) -> dict[str, Any] | None:
        result = await self.request(SERVICE_CHECK_BACKUP)
        return protocol.scale_backup_state(result) if result is not None else None

    async def set_power_control(self, power: int, timeout_s: int) -> dict[str, Any] | None:
        """Write 22045. The echo carries the NEW power/timeout on success."""
        return await self.request(SERVICE_SET_POWER, protocol.build_power_control_params(power, timeout_s))

    async def set_backup(self, enabled: bool) -> dict[str, Any] | None:
        """Write 22013. The echo carries the PREVIOUS value; confirm via a readback."""
        return await self.request(SERVICE_ENABLE_BACKUP, protocol.build_backup_params(enabled))

    async def request(self, service_id: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Send one service request and return its result dict, or None."""
        req = protocol.build_request(self._serial, service_id, params)
        async with self._lock:
            try:
                if not self.connected:
                    await self._open()
                assert self._writer is not None and self._reader is not None
                self._writer.write(req.raw)
                await self._writer.drain()
                response = await self._read_response(req.msg_id)
                self._silent_reads = 0
            except _ReadTimeout:
                await self._on_silent_read(service_id)
                return None
            except (TimeoutError, ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
                await self._on_connection_error(service_id, exc)
                return None

        if response.sn and response.sn != protocol.SN_PLACEHOLDER:
            self.device_serial = response.sn
            self._serial = response.sn
        if not response.ok:
            _LOGGER.warning("Service %s failed: code=%s error=%s", service_id, response.code, response.first_error())
            return None
        result = response.first_result()
        if result is None:
            _LOGGER.warning("Service %s answered 200 without a result", service_id)
        return result

    # ── Failure handling ───────────────────────────────────────────────────

    async def _on_connection_error(self, service_id: str, exc: Exception) -> None:
        cooldown = self._cooldown()
        self._connection_failures += 1
        _LOGGER.warning("Service %s: connection error (%s); retrying after %.0f s", service_id, exc, cooldown)
        await self._close()
        await asyncio.sleep(cooldown)

    async def _on_silent_read(self, service_id: str) -> None:
        self._silent_reads += 1
        if self._silent_reads >= SILENT_READS_BEFORE_RECYCLE:
            _LOGGER.warning(
                "Service %s: %d requests without a reply, reconnecting "
                "(is the SAMDUO app holding the battery's only connection?)",
                service_id,
                self._silent_reads,
            )
            await self._close()
            self._silent_reads = 0

    async def _read_response(self, msg_id: str) -> protocol.Response:
        """Read frames until the reply matching ``msg_id`` arrives.

        The buffer survives across requests so a frame split over two reads
        is completed, and a late reply to a request we already gave up on is
        consumed here instead of corrupting the next read.
        """
        assert self._reader is not None
        try:
            async with asyncio.timeout(self._response_timeout):
                while True:
                    frames, self._buffer = protocol.extract_frames(self._buffer)
                    for frame in frames:
                        response = protocol.parse_response(frame)
                        if response is None:
                            _LOGGER.debug("Discarding unparseable frame: %.200s", frame)
                            continue
                        if response.msg_id != msg_id:
                            _LOGGER.debug("Discarding stale reply msgId=%s", response.msg_id)
                            continue
                        return response
                    chunk = await self._reader.read(4096)
                    if not chunk:
                        raise ConnectionResetError("battery closed the connection")
                    self._buffer += chunk
        except TimeoutError:
            raise _ReadTimeout from None
