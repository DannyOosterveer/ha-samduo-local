"""SAMDUO Nex home battery: local control and smart charging strategies."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import SamduoClient
from .const import CONF_SERIAL
from .coordinator import SamduoConfigEntry, SamduoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: SamduoConfigEntry) -> bool:
    host: str = entry.data[CONF_HOST]
    port: int = entry.data[CONF_PORT]
    client = SamduoClient(host, port, serial=entry.data.get(CONF_SERIAL))
    try:
        await client.async_connect()
    except (TimeoutError, OSError) as exc:
        raise ConfigEntryNotReady(f"Cannot connect to {host}:{port}: {exc}") from exc

    coordinator = SamduoCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _LOGGER.info("SAMDUO battery '%s' ready at %s:%s", entry.title, host, port)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: SamduoConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SamduoConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
