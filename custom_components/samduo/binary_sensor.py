"""Binary sensors: period flags, control state and the HEMS conflict."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SamduoConfigEntry, SamduoCoordinator
from .entity import SamduoEntity


@dataclass(frozen=True, kw_only=True)
class SamduoBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[dict[str, Any], SamduoCoordinator], bool | None]


DESCRIPTIONS: tuple[SamduoBinarySensorDescription, ...] = (
    SamduoBinarySensorDescription(
        key="cheap_period_active",
        icon="mdi:arrow-down-bold-circle",
        is_on_fn=lambda data, c: bool(data.get("cheap_active")),
    ),
    SamduoBinarySensorDescription(
        key="expensive_period_active",
        icon="mdi:arrow-up-bold-circle",
        is_on_fn=lambda data, c: bool(data.get("expensive_active")),
    ),
    SamduoBinarySensorDescription(
        key="controlling",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:robot",
        is_on_fn=lambda data, c: c.commanded_setpoint is not None,
    ),
    SamduoBinarySensorDescription(
        key="dynamic_charging",
        icon="mdi:cash-plus",
        is_on_fn=lambda data, c: c.engine.dynamic_charging,
    ),
    SamduoBinarySensorDescription(
        key="dynamic_discharging",
        icon="mdi:cash-minus",
        is_on_fn=lambda data, c: c.engine.dynamic_discharging,
    ),
    SamduoBinarySensorDescription(
        key="grid_sensor_available",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda data, c: bool(data.get("grid_available")),
    ),
    SamduoBinarySensorDescription(
        key="control_blocked",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda data, c: c.control_blocked,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SamduoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SamduoBinarySensor(coordinator, description) for description in DESCRIPTIONS)


class SamduoBinarySensor(SamduoEntity, BinarySensorEntity):
    entity_description: SamduoBinarySensorDescription

    def __init__(self, coordinator: SamduoCoordinator, description: SamduoBinarySensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.data, self.coordinator)
