"""Number entities: the strategy settings, restored across restarts."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    POWER_LIMIT_MAX,
    POWER_LIMIT_MIN,
    SETTING_CHARGE_MARGIN,
    SETTING_CHARGE_START,
    SETTING_CHEAP_PERIODS,
    SETTING_CHEAP_PERIODS_TOMORROW,
    SETTING_DISCHARGE_MARGIN,
    SETTING_DISCHARGE_START,
    SETTING_EXPENSIVE_PERIODS,
    SETTING_EXPENSIVE_PERIODS_TOMORROW,
    SETTING_EXPORT_CORRECTION,
    SETTING_MANUAL_POWER,
    SETTING_MAX_CHARGE_POWER,
    SETTING_MAX_DISCHARGE_POWER,
    SETTING_MAX_SOC,
    SETTING_MIN_SOC,
    SETTING_MIN_SPREAD,
)
from .coordinator import SamduoConfigEntry, SamduoCoordinator
from .entity import SamduoEntity


@dataclass(frozen=True, kw_only=True)
class SamduoNumberDescription(NumberEntityDescription):
    integer: bool = True


DESCRIPTIONS: tuple[SamduoNumberDescription, ...] = (
    SamduoNumberDescription(
        key=SETTING_MANUAL_POWER,
        native_min_value=-POWER_LIMIT_MAX,
        native_max_value=POWER_LIMIT_MAX,
        native_step=1,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        mode=NumberMode.BOX,
        icon="mdi:flash",
    ),
    SamduoNumberDescription(
        key=SETTING_MAX_CHARGE_POWER,
        native_min_value=POWER_LIMIT_MIN,
        native_max_value=POWER_LIMIT_MAX,
        native_step=1,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        mode=NumberMode.BOX,
        icon="mdi:format-vertical-align-top",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_MAX_DISCHARGE_POWER,
        native_min_value=POWER_LIMIT_MIN,
        native_max_value=POWER_LIMIT_MAX,
        native_step=1,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        mode=NumberMode.BOX,
        icon="mdi:format-vertical-align-bottom",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_CHARGE_START,
        native_min_value=-1000,
        native_max_value=-30,
        native_step=1,
        native_unit_of_measurement=UnitOfPower.WATT,
        mode=NumberMode.BOX,
        icon="mdi:battery-plus-variant",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_DISCHARGE_START,
        native_min_value=30,
        native_max_value=1000,
        native_step=1,
        native_unit_of_measurement=UnitOfPower.WATT,
        mode=NumberMode.BOX,
        icon="mdi:battery-minus-variant",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_CHARGE_MARGIN,
        native_min_value=0,
        native_max_value=250,
        native_step=1,
        native_unit_of_measurement=UnitOfPower.WATT,
        mode=NumberMode.BOX,
        icon="mdi:arrow-expand-vertical",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_DISCHARGE_MARGIN,
        native_min_value=-100,
        native_max_value=250,
        native_step=1,
        native_unit_of_measurement=UnitOfPower.WATT,
        mode=NumberMode.BOX,
        icon="mdi:arrow-expand-vertical",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_MIN_SOC,
        native_min_value=5,
        native_max_value=50,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.BOX,
        icon="mdi:battery-low",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_MAX_SOC,
        native_min_value=50,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.BOX,
        icon="mdi:battery-high",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_MIN_SPREAD,
        native_min_value=0,
        native_max_value=500,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.BOX,
        icon="mdi:percent-box",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_EXPORT_CORRECTION,
        native_min_value=0,
        native_max_value=0.5,
        native_step=0.001,
        native_unit_of_measurement="€/kWh",
        mode=NumberMode.BOX,
        icon="mdi:cash-minus",
        entity_category=EntityCategory.CONFIG,
        integer=False,
    ),
    SamduoNumberDescription(
        key=SETTING_CHEAP_PERIODS,
        native_min_value=0,
        native_max_value=96,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:view-grid-plus",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_EXPENSIVE_PERIODS,
        native_min_value=0,
        native_max_value=96,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:view-grid-plus",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_CHEAP_PERIODS_TOMORROW,
        native_min_value=0,
        native_max_value=96,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:view-grid-plus-outline",
        entity_category=EntityCategory.CONFIG,
    ),
    SamduoNumberDescription(
        key=SETTING_EXPENSIVE_PERIODS_TOMORROW,
        native_min_value=0,
        native_max_value=96,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:view-grid-plus-outline",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SamduoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SamduoSettingNumber(coordinator, description) for description in DESCRIPTIONS)


class SamduoSettingNumber(SamduoEntity, RestoreNumber):
    """A strategy setting. The value lives in the coordinator; the entity is
    the user's handle on it and brings the last value back after a restart."""

    entity_description: SamduoNumberDescription

    def __init__(self, coordinator: SamduoCoordinator, description: SamduoNumberDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.coordinator.restore_setting(self._key, self._coerce(last.native_value))

    def _coerce(self, value: float) -> float | int:
        return int(round(value)) if self.entity_description.integer else float(value)

    @property
    def native_value(self) -> float:
        return self.coordinator.settings[self._key]

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_setting(self._key, self._coerce(value))
