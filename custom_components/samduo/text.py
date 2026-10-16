"""Text entities: manual cheap/expensive periods for today and tomorrow.

Format: items separated by ``;``. ``G02:00`` (or ``C02:00``) marks the
period starting at 02:00 as cheap, ``D18:00-21:00`` (or ``E18:00-21:00``)
marks every period between 18:00 and 21:00 as expensive. A non-empty text
overrides the automatic cheapest/most-expensive selection for that day.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import SETTING_MANUAL_PERIODS, SETTING_MANUAL_PERIODS_TOMORROW
from .coordinator import SamduoConfigEntry, SamduoCoordinator
from .entity import SamduoEntity

PATTERN = r"^(\s*[GDCEgdce]\d{1,2}:\d{2}(-\d{1,2}:\d{2})?\s*(;|$))*$"


async def async_setup_entry(
    hass: HomeAssistant, entry: SamduoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            SamduoManualPeriodsText(coordinator, SETTING_MANUAL_PERIODS),
            SamduoManualPeriodsText(coordinator, SETTING_MANUAL_PERIODS_TOMORROW),
        ]
    )


class SamduoManualPeriodsText(SamduoEntity, TextEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calendar-clock"
    _attr_native_max = 255
    _attr_pattern = PATTERN

    def __init__(self, coordinator: SamduoCoordinator, key: str) -> None:
        super().__init__(coordinator, key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable"):
            self.coordinator.restore_setting(self._key, last.state)

    @property
    def native_value(self) -> str:
        return str(self.coordinator.settings[self._key])

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_set_setting(self._key, value.strip())
