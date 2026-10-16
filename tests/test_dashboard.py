"""Every entity id the shipped dashboard references must exist."""

from __future__ import annotations

import re
from pathlib import Path

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

DASHBOARD = Path(__file__).parent.parent / "dashboard" / "samduo_dashboard.yaml"
ENTITY_ID = re.compile(r"entity: ((?:sensor|binary_sensor|select|number|switch|text|button)\.samduo_[a-z0-9_]+)")


async def test_dashboard_entities_exist(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    referenced = set(ENTITY_ID.findall(DASHBOARD.read_text()))
    assert len(referenced) > 40
    missing = sorted(entity_id for entity_id in referenced if hass.states.get(entity_id) is None)
    assert missing == []
