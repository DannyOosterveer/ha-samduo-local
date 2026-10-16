"""Coordinator behaviour through the entities: modes, writes, watchdog."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samduo.const import (
    DIRECTION_DWELL_S,
    DOMAIN,
    GRID_SENSOR_STALE_S,
    ISSUE_HEMS_BLOCKED,
    RELEASE_TIMEOUT_S,
)

from .conftest import GRID_SENSOR, MOCK_DEVICE_DATA, PRICE_SENSOR


async def select_mode(hass: HomeAssistant, mode: str) -> None:
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.samduo_mode", "option": mode}, blocking=True
    )
    await hass.async_block_till_done()


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True)
    await hass.async_block_till_done()


async def test_manual_mode_writes_setpoint_and_standby_releases(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    client = coordinator.client

    await select_mode(hass, "manual")
    assert client.set_power_control.await_count == 0  # manual power is 0 and nothing was held
    await set_number(hass, "number.samduo_manual_power", 300)
    client.set_power_control.assert_awaited_with(300, 90)
    assert hass.states.get("sensor.samduo_power_setpoint").state == "300"
    assert hass.states.get("binary_sensor.samduo_controlling").state == "on"
    assert hass.states.get("sensor.samduo_strategy").state == "manual"

    # A charge setpoint beyond the limit is clamped.
    await set_number(hass, "number.samduo_manual_power", -2000)
    client.set_power_control.assert_awaited_with(-800, 90)

    await select_mode(hass, "standby")
    client.set_power_control.assert_awaited_with(0, RELEASE_TIMEOUT_S)
    assert coordinator.commanded_setpoint is None
    assert hass.states.get("binary_sensor.samduo_controlling").state == "off"


async def test_zero_on_meter_follows_the_grid_sensor(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    coordinator = setup_integration.runtime_data
    client = coordinator.client
    await select_mode(hass, "zero_on_meter")
    assert client.set_power_control.await_count == 0  # importing 0 W: nothing to do

    hass.states.async_set(GRID_SENSOR, "400", {"device_class": "power"})
    await coordinator.async_refresh()
    client.set_power_control.assert_awaited_with(304, 90)  # 75 % of (400 + 5)
    assert hass.states.get("sensor.samduo_strategy").state == "discharging_nom"
    assert float(hass.states.get("sensor.samduo_house_consumption").state) == 400

    client.get_device_data.return_value = {**MOCK_DEVICE_DATA, "battery_power": 304}
    hass.states.async_set(GRID_SENSOR, "96", {"device_class": "power"})
    await coordinator.async_refresh()
    client.set_power_control.assert_awaited_with(405, 90)

    # Small deviations stay within the deadband and are not written.
    await_count = client.set_power_control.await_count
    client.get_device_data.return_value = {**MOCK_DEVICE_DATA, "battery_power": 405}
    hass.states.async_set(GRID_SENSOR, "-3", {"device_class": "power"})
    await coordinator.async_refresh()
    assert client.set_power_control.await_count == await_count


async def test_grid_sensor_unavailable_stops_the_battery(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    client = coordinator.client
    await select_mode(hass, "zero_on_meter")
    hass.states.async_set(GRID_SENSOR, "400", {"device_class": "power"})
    await coordinator.async_refresh()
    hass.states.async_set(GRID_SENSOR, "unavailable")
    client.get_device_data.return_value = {**MOCK_DEVICE_DATA, "battery_power": 304}
    await coordinator.async_refresh()
    client.set_power_control.assert_awaited_with(0, 90)
    assert hass.states.get("sensor.samduo_strategy").state == "grid_sensor_unavailable"
    assert hass.states.get("binary_sensor.samduo_grid_power_sensor").state == "off"


async def test_frozen_grid_sensor_counts_as_unavailable(
    hass: HomeAssistant, setup_integration: MockConfigEntry, freezer
) -> None:
    coordinator = setup_integration.runtime_data
    await select_mode(hass, "zero_on_meter")
    hass.states.async_set(GRID_SENSOR, "400", {"device_class": "power"})
    await coordinator.async_refresh()
    assert hass.states.get("sensor.samduo_strategy").state == "discharging_nom"
    freezer.tick(GRID_SENSOR_STALE_S + 1)
    await coordinator.async_refresh()
    assert hass.states.get("sensor.samduo_strategy").state == "grid_sensor_unavailable"
    # A sensor that reports the same value again is fresh again.
    hass.states.async_set(GRID_SENSOR, "400", {"device_class": "power"})
    freezer.tick(DIRECTION_DWELL_S + 1)
    await coordinator.async_refresh()
    assert hass.states.get("sensor.samduo_strategy").state == "discharging_nom"


async def test_missing_grid_sensor_raises_repair(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client
) -> None:
    hass.config_entries.async_update_entry(config_entry, options={})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await select_mode(hass, "zero_on_meter")
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"missing_grid_sensor_{config_entry.entry_id}") is not None
    await select_mode(hass, "standby")
    assert registry.async_get_issue(DOMAIN, f"missing_grid_sensor_{config_entry.entry_id}") is None


async def test_keepalive_refreshes_held_setpoint(
    hass: HomeAssistant, setup_integration: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = setup_integration.runtime_data
    client = coordinator.client
    await select_mode(hass, "manual")
    await set_number(hass, "number.samduo_manual_power", 200)
    count = client.set_power_control.await_count
    await coordinator.async_refresh()
    assert client.set_power_control.await_count == count  # not due yet
    coordinator._last_keepalive -= coordinator.keepalive_interval + 1
    await coordinator.async_refresh()
    assert client.set_power_control.await_count == count + 1
    client.set_power_control.assert_awaited_with(200, 90)


async def test_hems_conflict_detected_and_cleared(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    coordinator = setup_integration.runtime_data
    client = coordinator.client
    await select_mode(hass, "manual")
    await set_number(hass, "number.samduo_manual_power", 500)
    # The battery confirms but keeps delivering 0 W.
    for _ in range(3):
        await coordinator.async_refresh()
    assert coordinator.control_blocked
    assert hass.states.get("binary_sensor.samduo_control_blocked").state == "on"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_HEMS_BLOCKED}_{setup_integration.entry_id}")

    client.get_device_data.return_value = {**MOCK_DEVICE_DATA, "battery_power": 490}
    await coordinator.async_refresh()
    assert not coordinator.control_blocked
    assert hass.states.get("binary_sensor.samduo_control_blocked").state == "off"


async def test_write_failure_raises(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    coordinator = setup_integration.runtime_data
    coordinator.client.set_backup.return_value = None
    with pytest.raises(Exception, match="did not reach"):
        await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.samduo_backup_output"}, blocking=True)
    coordinator.client.set_backup.return_value = {"inv_backup": 0}
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.samduo_backup_output"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("switch.samduo_backup_output").state == "on"


async def test_recommended_settings_button(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    await set_number(hass, "number.samduo_minimum_soc", 25)
    assert hass.states.get("number.samduo_minimum_soc").state == "25"
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.samduo_apply_recommended_settings"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("number.samduo_minimum_soc").state == "10"


async def test_release_button_puts_mode_on_standby(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    coordinator = setup_integration.runtime_data
    await select_mode(hass, "fast_discharge")
    coordinator.client.set_power_control.assert_awaited_with(800, 90)
    await hass.services.async_call("button", "press", {"entity_id": "button.samduo_release_control"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("select.samduo_mode").state == "standby"
    assert coordinator.commanded_setpoint is None


async def test_price_context_from_nordpool_sensor(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    coordinator = setup_integration.runtime_data
    now = dt_util.now().replace(minute=0, second=0, microsecond=0)
    day = now.replace(hour=0)
    values = [0.30] * 24
    values[now.hour] = 0.01  # the current hour is the cheapest of the day
    raw = [
        {"start": (day + timedelta(hours=h)).isoformat(), "end": (day + timedelta(hours=h + 1)).isoformat(), "value": v}
        for h, v in enumerate(values)
    ]
    hass.states.async_set(PRICE_SENSOR, "0.01", {"raw_today": raw, "raw_tomorrow": []})
    await coordinator.async_refresh()
    assert hass.states.get("sensor.samduo_current_price").state == "0.01"
    assert hass.states.get("binary_sensor.samduo_cheap_period").state == "on"
    assert hass.states.get("sensor.samduo_price_periods_today").state == "24"
    assert float(hass.states.get("sensor.samduo_spread").state) > 25  # above the minimum spread

    await select_mode(hass, "dynamic_trading")
    coordinator.client.set_power_control.assert_awaited_with(-800, 90)
    assert hass.states.get("sensor.samduo_strategy").state == "dynamic_charging"
    assert hass.states.get("binary_sensor.samduo_dynamic_charging").state == "on"


async def test_midnight_rollover_copies_tomorrow_settings(
    hass: HomeAssistant, setup_integration: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = setup_integration.runtime_data
    await set_number(hass, "number.samduo_cheap_periods_tomorrow", 5)
    await hass.services.async_call(
        "text", "set_value", {"entity_id": "text.samduo_manual_periods_tomorrow", "value": "G02:00"}, blocking=True
    )
    tomorrow = datetime.now(tz=dt_util.get_default_time_zone()) + timedelta(days=1)
    monkeypatch.setattr("custom_components.samduo.coordinator.dt_util.now", lambda: tomorrow)
    await coordinator.async_refresh()
    assert hass.states.get("number.samduo_cheap_periods_today").state == "5"
    assert hass.states.get("text.samduo_manual_periods_today").state == "G02:00"
    assert hass.states.get("text.samduo_manual_periods_tomorrow").state == ""


async def test_settings_restored_after_restart(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    entry = setup_integration
    await select_mode(hass, "fast_charge")
    await set_number(hass, "number.samduo_max_charge_power", 1500)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.samduo_soc_protection"}, blocking=True)
    await hass.async_block_till_done()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.mode.value == "fast_charge"
    assert coordinator.settings["max_charge_power"] == 1500
    assert coordinator.settings["soc_protection"] is False
    assert hass.states.get("select.samduo_mode").state == "fast_charge"
