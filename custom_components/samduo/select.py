"""The mode selector: the one entity that decides what the battery does."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import Mode
from .coordinator import SamduoConfigEntry, SamduoCoordinator
from .entity import SamduoEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SamduoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SamduoModeSelect(entry.runtime_data)])


class SamduoModeSelect(SamduoEntity, SelectEntity, RestoreEntity):
    _attr_icon = "mdi:battery-charging-wireless"
    _attr_options = [mode.value for mode in Mode]

    def __init__(self, coordinator: SamduoCoordinator) -> None:
        super().__init__(coordinator, "mode")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            self.coordinator.restore_mode(Mode(last.state))

    @property
    def current_option(self) -> str:
        return self.coordinator.mode.value

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(Mode(option))
