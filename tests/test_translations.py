"""Every entity key has a name in strings.json; en.json mirrors it."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.samduo import binary_sensor, number, sensor
from custom_components.samduo.const import (
    SETTING_MANUAL_PERIODS,
    SETTING_MANUAL_PERIODS_TOMORROW,
    SETTING_QUARTER_HOUR_PERIODS,
    SETTING_SOC_PROTECTION,
    Mode,
)

COMPONENT = Path(__file__).parent.parent / "custom_components" / "samduo"


def _strings() -> dict:
    return json.loads((COMPONENT / "strings.json").read_text())


def test_entity_keys_have_names() -> None:
    entity = _strings()["entity"]
    for description in sensor.TELEMETRY + sensor.STRATEGY:
        assert description.key in entity["sensor"], description.key
    for description in binary_sensor.DESCRIPTIONS:
        assert description.key in entity["binary_sensor"], description.key
    for description in number.DESCRIPTIONS:
        assert description.key in entity["number"], description.key
    for key in ("backup_output", SETTING_SOC_PROTECTION, SETTING_QUARTER_HOUR_PERIODS):
        assert key in entity["switch"], key
    for key in (SETTING_MANUAL_PERIODS, SETTING_MANUAL_PERIODS_TOMORROW):
        assert key in entity["text"], key
    assert set(entity["select"]["mode"]["state"]) == {mode.value for mode in Mode}
    assert set(entity["sensor"]["strategy_state"]["state"]) == set(sensor.STRATEGY_STATES)


def test_translations_match_strings() -> None:
    strings = _strings()
    en = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert en == strings
    nl = json.loads((COMPONENT / "translations" / "nl.json").read_text())

    def keys(obj: dict, prefix: str = "") -> set[str]:
        out: set[str] = set()
        for key, value in obj.items():
            path = f"{prefix}/{key}"
            out.add(path)
            if isinstance(value, dict):
                out |= keys(value, path)
        return out

    assert keys(nl) == keys(strings)
