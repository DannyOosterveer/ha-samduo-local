"""Pure protocol tests: framing, parsing, scaling, validation."""

from __future__ import annotations

import json

import pytest

from custom_components.samduo import protocol

# Verbatim reply from a Nex E6000 (fw 0.0.1.10) to a 22600 request.
REAL_FRAME = (
    b'{"header":"triplus/ESD/E01PYB1600802/thing/action/execute_response","payload":{"ver":"1.0",'
    b'"msgId":"1792146018095","contentType":"json","ts":1792146018322,"code":200,"services":[{"serviceId":"22600",'
    b'"result":{"inv_state":0,"inv_ver":1148,"inv_code":1,"inv_err1":0,"inv_gridp":0,"inv_offgridp":0,'
    b'"inv_gridAcwh":0,"inv_gridAdwh":0,"inv_gridf":500,"inv_gridv":2382,"inv_offgridv":2,"inv_backup":0,'
    b'"inv_temp1":235,"inv_temp2":220,"bms_ver":1074,"bms_soc":450,"bms_soh":999,"bms_vol":196,"ems_ver":1155}}]}}'
)


def test_build_request_matches_spec_shape() -> None:
    req = protocol.build_request("E01", "22045", {"power": 800, "timeoutS": 60}, ts=1783672424390)
    message = json.loads(req.raw)
    assert message["header"] == "triplus/ESD/E01/thing/action/execute"
    payload = message["payload"]
    assert payload["ver"] == "1.0"
    assert payload["msgId"] == "1783672424390" == req.msg_id
    assert payload["sys"] == {"ack": 1, "trace": 0}
    assert payload["services"] == [{"serviceId": "22045", "params": {"power": 800, "timeoutS": 60}}]
    assert b" " not in req.raw  # compact JSON


def test_extract_frames_handles_concatenation_split_and_noise() -> None:
    frames, rest = protocol.extract_frames(b'garbage{"a":1}{"b":"}"}{"c":')
    assert frames == [b'{"a":1}', b'{"b":"}"}']
    assert rest == b'{"c":'
    frames, rest = protocol.extract_frames(rest + b"2}")
    assert frames == [b'{"c":2}']
    assert rest == b""
    assert protocol.extract_frames(b"") == ([], b"")
    assert protocol.extract_frames(b"   ") == ([], b"")


def test_extract_frames_ignores_escaped_quotes() -> None:
    frames, rest = protocol.extract_frames(b'{"s":"a\\"}b"}')
    assert frames == [b'{"s":"a\\"}b"}']
    assert rest == b""


def test_parse_real_frame() -> None:
    response = protocol.parse_response(REAL_FRAME)
    assert response is not None
    assert response.ok
    assert response.sn == "E01PYB1600802"
    assert response.msg_id == "1792146018095"
    result = response.first_result()
    assert result is not None
    scaled = protocol.scale_device_data(result)
    assert scaled["battery_soc"] == 45.0
    assert scaled["battery_soh"] == 99.9
    assert scaled["grid_voltage"] == 238.2
    assert scaled["grid_frequency"] == 50.0
    assert scaled["temperature_1"] == 23.5
    assert scaled["battery_voltage"] == 19.6
    assert scaled["energy_charged"] == 0.0
    assert scaled["battery_power"] == 0


def test_parse_strips_trailing_space_in_keys() -> None:
    frame = (
        b'{"header":"triplus/ESD/X/thing/action/execute_response","payload":{"msgId":"1","code":200,'
        b'"services":[{"serviceId":"22046","result":{"power":800,"timeoutS":60,"timeLeft ":58}}]}}'
    )
    response = protocol.parse_response(frame)
    assert response is not None
    assert protocol.scale_power_config(response.first_result()) == {
        "control_power": 800,
        "control_timeout": 60,
        "control_time_left": 58,
    }


def test_parse_error_and_garbage() -> None:
    frame = b'{"header":"x","payload":{"msgId":"1","code":400,"services":[{"serviceId":"1","error":{"code":-1}}]}}'
    response = protocol.parse_response(frame)
    assert response is not None
    assert not response.ok
    assert response.first_error() == {"code": -1}
    assert response.first_result() is None
    assert protocol.parse_response(b"not json") is None
    assert protocol.parse_response(b"[1,2]") is None
    assert protocol.parse_response(b'{"header":"x"}') is None


def test_scaling_drops_non_numeric_and_bool() -> None:
    assert protocol.scale_device_data({"bms_soc": "450", "inv_backup": True, "inv_gridp": -799}) == {
        "battery_power": -799
    }
    assert protocol.scale_backup_state({"inv_backup": 1}) == {"backup_enabled": 1}


@pytest.mark.parametrize(("power", "timeout"), [(5001, 60), (-5001, 60), (0, 0), (0, 3601)])
def test_power_params_rejected(power: int, timeout: int) -> None:
    with pytest.raises(ValueError):
        protocol.build_power_control_params(power, timeout)


def test_power_params_accepted() -> None:
    assert protocol.build_power_control_params(-800.0, 90) == {"power": -800, "timeoutS": 90}
    assert protocol.build_backup_params(True) == {"inv_backup": 1}
    assert protocol.build_backup_params(False) == {"inv_backup": 0}
