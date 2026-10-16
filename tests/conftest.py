"""Shared fixtures.

The canned telemetry is a real 22600 reply from a Nex E6000 (firmware
0.0.1.10, idle at 45 %), scaled to canonical keys.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samduo.const import (
    CONF_GRID_POWER_ENTITY,
    CONF_MODEL,
    CONF_PRICE_ENTITY,
    CONF_SERIAL,
    DOMAIN,
)

MOCK_SERIAL = "E01PYB1600802"
GRID_SENSOR = "sensor.p1_power"
PRICE_SENSOR = "sensor.nordpool"

MOCK_DEVICE_DATA = {
    "inverter_state": 0,
    "inverter_version": 1148,
    "inverter_code": 1,
    "error_code": 0,
    "battery_power": 0,
    "backup_power": 0,
    "energy_charged": 0.0,
    "energy_discharged": 0.0,
    "grid_frequency": 50.0,
    "grid_voltage": 238.2,
    "offgrid_voltage": 0.2,
    "backup_enabled": 0,
    "temperature_1": 23.5,
    "temperature_2": 22.0,
    "bms_version": 1074,
    "battery_soc": 45.0,
    "battery_soh": 99.9,
    "battery_voltage": 19.6,
    "ems_version": 1155,
}

MOCK_POWER_CONFIG = {"control_power": 0, "control_timeout": 0, "control_time_left": 0}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def fast_writes(monkeypatch):
    from custom_components.samduo.coordinator import SamduoCoordinator

    monkeypatch.setattr(SamduoCoordinator, "_VERIFY_DELAY_S", 0)
    monkeypatch.setattr(SamduoCoordinator, "_WRITE_RETRY_DELAY_S", 0)
    monkeypatch.setattr(SamduoCoordinator, "_TRACK_SETTLE_S", 0)
    monkeypatch.setattr(SamduoCoordinator, "_TRACK_MIN_SECONDS", 0)
    monkeypatch.setattr("custom_components.samduo.coordinator.REFRESH_COOLDOWN_S", 0)


@pytest.fixture
def mock_client():
    """A SamduoClient double, patched into setup and the config flow."""
    with patch("custom_components.samduo.SamduoClient", autospec=True) as mock_cls:
        client = mock_cls.return_value
        client.host = "192.168.1.65"
        client.port = 3335
        client.device_serial = MOCK_SERIAL
        client.consecutive_failures = 0
        client.connected = True
        client.async_connect = AsyncMock()
        client.async_disconnect = AsyncMock()
        client.get_device_data = AsyncMock(return_value=dict(MOCK_DEVICE_DATA))
        client.get_power_config = AsyncMock(return_value=dict(MOCK_POWER_CONFIG))
        client.get_backup_state = AsyncMock(return_value={"backup_enabled": 0})
        client.request = AsyncMock(return_value={"inv_backup": 0})
        client.set_power_control = AsyncMock(side_effect=lambda p, t: {"power": p, "timeoutS": t})
        client.set_backup = AsyncMock(return_value={"inv_backup": 0})
        with patch("custom_components.samduo.config_flow.SamduoClient", new=mock_cls):
            yield client


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Samduo",
        unique_id=MOCK_SERIAL,
        data={
            CONF_HOST: "192.168.1.65",
            CONF_PORT: 3335,
            CONF_NAME: "Samduo",
            CONF_SERIAL: MOCK_SERIAL,
            CONF_MODEL: "Nex E6000",
        },
        options={CONF_GRID_POWER_ENTITY: GRID_SENSOR, CONF_PRICE_ENTITY: PRICE_SENSOR},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry, mock_client) -> MockConfigEntry:
    hass.states.async_set(GRID_SENSOR, "0", {"device_class": "power", "unit_of_measurement": "W"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
