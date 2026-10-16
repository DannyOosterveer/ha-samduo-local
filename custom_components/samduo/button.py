"""Buttons: release control, apply the recommended settings."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import Mode
from .coordinator import SamduoConfigEntry, SamduoCoordinator
from .entity import SamduoEntity, raise_set_failed


async def async_setup_entry(
    hass: HomeAssistant, entry: SamduoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([SamduoReleaseControlButton(coordinator), SamduoRecommendedSettingsButton(coordinator)])


class SamduoReleaseControlButton(SamduoEntity, ButtonEntity):
    """Hand the battery back to its own logic and put the mode on standby."""

    _attr_icon = "mdi:lock-open-variant"

    def __init__(self, coordinator: SamduoCoordinator) -> None:
        super().__init__(coordinator, "release_control")

    async def async_press(self) -> None:
        await self.coordinator.async_set_mode(Mode.STANDBY)
        if not await self.coordinator.async_release_control():
            raise_set_failed(str(self.name))


class SamduoRecommendedSettingsButton(SamduoEntity, ButtonEntity):
    _attr_icon = "mdi:hand-pointer"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SamduoCoordinator) -> None:
        super().__init__(coordinator, "apply_recommended_settings")

    async def async_press(self) -> None:
        await self.coordinator.async_apply_recommended_settings()
