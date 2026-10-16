"""The README documents every entity, and only entities that exist."""

from __future__ import annotations

import re
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

README = Path(__file__).parent.parent / "README.md"
ENTITY_ID = re.compile(r"`((?:sensor|binary_sensor|select|number|switch|text|button)\.samduo_[a-z0-9_]+)`")


async def test_readme_lists_every_entity(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    documented = set(ENTITY_ID.findall(README.read_text()))
    registered = {
        entry.entity_id for entry in er.async_entries_for_config_entry(er.async_get(hass), setup_integration.entry_id)
    }
    assert sorted(documented - registered) == []  # documented but missing
    assert sorted(registered - documented) == []  # exists but undocumented
