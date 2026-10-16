"""Setup, entities and unload."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samduo.const import DOMAIN

from .conftest import MOCK_SERIAL


async def test_setup_creates_device_and_entities(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, MOCK_SERIAL)}
    assert device.manufacturer == "SAMDUO"
    assert device.model == "Nex E6000"
    assert device.sw_version == "INV 1148 / BMS 1074 / EMS 1155"

    assert hass.states.get("sensor.samduo_battery_soc").state == "45.0"
    assert hass.states.get("sensor.samduo_battery_power").state == "0"
    assert hass.states.get("sensor.samduo_battery_status").state == "idle"
    assert hass.states.get("sensor.samduo_grid_voltage").state == "238.2"
    assert hass.states.get("sensor.samduo_strategy").state == "standby"
    assert float(hass.states.get("sensor.samduo_grid_power").state) == 0
    assert hass.states.get("sensor.samduo_available_energy").state == "2.1"  # (45 - 10) % of 6 kWh
    assert hass.states.get("sensor.samduo_energy_to_full").state == "3.3"
    assert hass.states.get("select.samduo_mode").state == "standby"
    assert hass.states.get("number.samduo_max_charge_power").state == "800"
    assert hass.states.get("switch.samduo_soc_protection").state == "on"
    assert hass.states.get("switch.samduo_backup_output").state == "off"
    assert hass.states.get("binary_sensor.samduo_controlling").state == "off"
    assert hass.states.get("text.samduo_manual_periods_today").state == ""

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    # 16 telemetry + 15 strategy sensors, 7 binary sensors, 1 select, 15 numbers, 3 switches, 2 texts, 2 buttons
    assert len(entities) == 16 + 15 + 7 + 1 + 15 + 3 + 2 + 2

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_fails_when_unreachable(hass: HomeAssistant, config_entry: MockConfigEntry, mock_client) -> None:
    mock_client.async_connect.side_effect = OSError("refused")
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_entities_unavailable_after_sustained_failure(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    coordinator.client.get_device_data.return_value = None
    for _ in range(5):
        await coordinator.async_refresh()
    assert hass.states.get("sensor.samduo_battery_soc").state == "45.0"  # still serving the last good frame
    await coordinator.async_refresh()
    assert hass.states.get("sensor.samduo_battery_soc").state == "unavailable"
