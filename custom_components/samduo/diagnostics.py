"""Diagnostics download: config, live state, engine state and write log."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import SamduoConfigEntry

REDACT = {"serial", "sn", "unique_id", "identifiers", "serial_number"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SamduoConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    payload: dict[str, Any] = {
        "config": {"data": dict(entry.data), "options": dict(entry.options)},
        "device": {
            "model": coordinator.model,
            "last_update_success": coordinator.last_update_success,
            "connection_failures": coordinator.client.consecutive_failures,
        },
        "strategy": {
            "mode": coordinator.mode.value,
            "settings": dict(coordinator.settings),
            "engine": {
                "recently_charged": coordinator.engine.recently_charged,
                "dynamic_charging": coordinator.engine.dynamic_charging,
                "dynamic_discharging": coordinator.engine.dynamic_discharging,
            },
            "commanded_setpoint": coordinator.commanded_setpoint,
            "control_blocked": coordinator.control_blocked,
        },
        "data": dict(coordinator.data or {}),
        "write_history": list(coordinator.write_history),
    }
    return async_redact_data(payload, REDACT)
