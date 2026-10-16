"""Switches: the backup output on the device, and two strategy toggles."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import SETTING_QUARTER_HOUR_PERIODS, SETTING_SOC_PROTECTION
from .coordinator import SamduoConfigEntry, SamduoCoordinator
from .entity import SamduoEntity, raise_set_failed


async def async_setup_entry(
    hass: HomeAssistant, entry: SamduoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            SamduoBackupSwitch(coordinator),
            SamduoSettingSwitch(coordinator, SETTING_SOC_PROTECTION, "mdi:battery-heart-variant"),
            SamduoSettingSwitch(coordinator, SETTING_QUARTER_HOUR_PERIODS, "mdi:clock-time-three-outline"),
        ]
    )


class SamduoBackupSwitch(SamduoEntity, SwitchEntity):
    """The off-grid (EPS) output, via 22013. State comes from the regular
    22600 poll; the write's own echo reports the previous value."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:power-plug"

    def __init__(self, coordinator: SamduoCoordinator) -> None:
        super().__init__(coordinator, "backup_output")

    @property
    def is_on(self) -> bool | None:
        value = self.data.get("backup_enabled")
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not await self.coordinator.async_set_backup(True):
            raise_set_failed(str(self.name))

    async def async_turn_off(self, **kwargs: Any) -> None:
        if not await self.coordinator.async_set_backup(False):
            raise_set_failed(str(self.name))


class SamduoSettingSwitch(SamduoEntity, SwitchEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SamduoCoordinator, key: str, icon: str) -> None:
        super().__init__(coordinator, key)
        self._attr_icon = icon

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self.coordinator.restore_setting(self._key, last.state == "on")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.settings[self._key])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_setting(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_setting(self._key, False)
