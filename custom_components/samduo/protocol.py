"""Pure helpers for the SAMDUO Open API V1.0 (JSON over TCP).

Nothing in here touches a socket: envelope building, stream framing,
response parsing and unit scaling are all plain functions, so the whole
protocol layer is unit-testable without a device.

Facts about the real firmware that differ from the PDF, learned the hard
way by the community and confirmed against a Nex E6000:

* Only one service per message is accepted; larger ``services`` arrays are
  rejected with "Too many services".
* ``sys.ack`` must be 1, otherwise the device sends no reply at all.
* Frames are bare concatenated compact JSON with no delimiter.
* The SN in the request header is not validated; the device stamps its own
  SN on every reply, which is how the serial is learned.
* ``bms_soc``/``bms_soh`` are per-mille (995 = 99.5 %), not the "0.001" the
  spec table says.
* 22046 may answer with the key ``"timeLeft "`` (trailing space).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .const import PROTOCOL_POWER_MAX, PROTOCOL_TIMEOUT_MAX, PROTOCOL_TIMEOUT_MIN

PROTOCOL_VERSION = "1.0"
STATUS_OK = 200
SN_PLACEHOLDER = "0"

_HEADER_PREFIX = "triplus/ESD/"
_HEADER_REQUEST = "/thing/action/execute"


@dataclass(frozen=True)
class Request:
    msg_id: str
    raw: bytes


@dataclass(frozen=True)
class ServiceResult:
    service_id: str | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None


@dataclass(frozen=True)
class Response:
    sn: str | None
    msg_id: str | None
    code: int | None
    services: list[ServiceResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.code == STATUS_OK

    def first_result(self) -> dict[str, Any] | None:
        return next((s.result for s in self.services if s.result is not None), None)

    def first_error(self) -> dict[str, Any] | None:
        return next((s.error for s in self.services if s.error is not None), None)


def build_request(
    sn: str,
    service_id: str,
    params: dict[str, Any] | None = None,
    *,
    ts: int | None = None,
) -> Request:
    """Build one single-service request message."""
    if ts is None:
        ts = int(time.time() * 1000)
    msg_id = str(ts)
    message = {
        "header": f"{_HEADER_PREFIX}{sn}{_HEADER_REQUEST}",
        "payload": {
            "ver": PROTOCOL_VERSION,
            "msgId": msg_id,
            "contentType": "json",
            "ts": ts,
            "sys": {"ack": 1, "trace": 0},
            "services": [{"serviceId": service_id, "params": params or {}}],
        },
    }
    return Request(msg_id=msg_id, raw=json.dumps(message, separators=(",", ":")).encode())


def extract_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Split a stream buffer into complete top-level JSON objects.

    Returns the complete frames and the unconsumed remainder. A partial
    frame stays in the remainder until the next chunk completes it; bytes
    before the first ``{`` are discarded as noise.
    """
    frames: list[bytes] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    consumed = 0
    for index, byte in enumerate(buffer):
        char = chr(byte)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                frames.append(buffer[start : index + 1])
                consumed = index + 1
    if depth == 0:
        return frames, buffer[consumed:].lstrip() if start != -1 else b""
    return frames, buffer[start:]


def _strip_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k).strip(): _strip_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_keys(v) for v in obj]
    return obj


def parse_response(frame: bytes) -> Response | None:
    """Parse one frame into a Response, or None when it is not an envelope."""
    try:
        message = json.loads(frame.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(message, dict):
        return None
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None

    sn: str | None = None
    header = message.get("header")
    if isinstance(header, str) and header.startswith(_HEADER_PREFIX):
        parts = header.split("/")
        if len(parts) >= 3 and parts[2]:
            sn = parts[2]

    services: list[ServiceResult] = []
    for entry in payload.get("services") or []:
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        error = entry.get("error")
        services.append(
            ServiceResult(
                service_id=entry.get("serviceId"),
                result=_strip_keys(result) if isinstance(result, dict) else None,
                error=_strip_keys(error) if isinstance(error, dict) else None,
            )
        )
    msg_id = payload.get("msgId")
    code = payload.get("code")
    return Response(
        sn=sn,
        msg_id=str(msg_id) if msg_id is not None else None,
        code=code if isinstance(code, int) else None,
        services=services,
    )


# ── Scaling to canonical keys and HA-native units ───────────────────────────
# Sign convention everywhere: positive = discharging, negative = charging.
# That is the device's own convention; it is never flipped.

DEVICE_DATA_FIELDS: dict[str, tuple[str, float]] = {
    "inv_state": ("inverter_state", 1),
    "inv_ver": ("inverter_version", 1),
    "inv_code": ("inverter_code", 1),
    "inv_err1": ("error_code", 1),
    "inv_gridp": ("battery_power", 1),
    "inv_offgridp": ("backup_power", 1),
    "inv_gridAcwh": ("energy_charged", 1000),  # Wh -> kWh
    "inv_gridAdwh": ("energy_discharged", 1000),
    "inv_gridf": ("grid_frequency", 10),
    "inv_gridv": ("grid_voltage", 10),
    "inv_offgridv": ("offgrid_voltage", 10),
    "inv_backup": ("backup_enabled", 1),
    "inv_temp1": ("temperature_1", 10),
    "inv_temp2": ("temperature_2", 10),
    "bms_ver": ("bms_version", 1),
    "bms_soc": ("battery_soc", 10),
    "bms_soh": ("battery_soh", 10),
    "bms_vol": ("battery_voltage", 10),
    "ems_ver": ("ems_version", 1),
}

POWER_CONFIG_FIELDS: dict[str, tuple[str, float]] = {
    "power": ("control_power", 1),
    "timeoutS": ("control_timeout", 1),
    "timeLeft": ("control_time_left", 1),
}

BACKUP_FIELDS: dict[str, tuple[str, float]] = {"inv_backup": ("backup_enabled", 1)}


def _scale(result: dict[str, Any], fields: dict[str, tuple[str, float]]) -> dict[str, Any]:
    scaled: dict[str, Any] = {}
    for raw_key, (canonical, divisor) in fields.items():
        value = result.get(raw_key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        scaled[canonical] = value / divisor if divisor != 1 else value
    return scaled


def scale_device_data(result: dict[str, Any]) -> dict[str, Any]:
    return _scale(result, DEVICE_DATA_FIELDS)


def scale_power_config(result: dict[str, Any]) -> dict[str, Any]:
    return _scale(result, POWER_CONFIG_FIELDS)


def scale_backup_state(result: dict[str, Any]) -> dict[str, Any]:
    return _scale(result, BACKUP_FIELDS)


# ── Control payload builders ────────────────────────────────────────────────


def build_power_control_params(power: int, timeout_s: int) -> dict[str, int]:
    """Params for 22045. The device validates nothing, so this does."""
    power = int(power)
    timeout_s = int(timeout_s)
    if not -PROTOCOL_POWER_MAX <= power <= PROTOCOL_POWER_MAX:
        raise ValueError(f"power {power} W is outside the protocol range of ±{PROTOCOL_POWER_MAX} W")
    if not PROTOCOL_TIMEOUT_MIN <= timeout_s <= PROTOCOL_TIMEOUT_MAX:
        raise ValueError(
            f"timeout {timeout_s} s is outside the protocol range of {PROTOCOL_TIMEOUT_MIN}-{PROTOCOL_TIMEOUT_MAX} s"
        )
    return {"power": power, "timeoutS": timeout_s}


def build_backup_params(enabled: bool) -> dict[str, int]:
    return {"inv_backup": 1 if enabled else 0}
