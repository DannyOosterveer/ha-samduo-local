"""Sensors: battery telemetry plus the strategy engine's view of the world."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SamduoConfigEntry, SamduoCoordinator
from .entity import SamduoEntity

STATUS_THRESHOLD_W = 10

STRATEGY_STATES = [
    "standby",
    "manual",
    "waiting",
    "discharging_nom",
    "charging_nom",
    "soc_protection",
    "fast_charging",
    "fast_discharging",
    "dynamic_charging",
    "dynamic_discharging",
    "between_periods",
    "soc_min_reached",
    "soc_max_reached",
    "discharge_not_allowed",
    "charge_not_allowed",
    "grid_sensor_unavailable",
    "price_sensor_unavailable",
    "no_telemetry",
]


@dataclass(frozen=True, kw_only=True)
class SamduoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any], SamduoCoordinator], Any] | None = None
    attributes_fn: Callable[[dict[str, Any], SamduoCoordinator], dict[str, Any]] | None = None


def _periods_attributes(day: str) -> Callable[[dict[str, Any], SamduoCoordinator], dict[str, Any]]:
    def attributes(data: dict[str, Any], _: SamduoCoordinator) -> dict[str, Any]:
        periods = data.get(f"price_periods_{day}") or []
        return {
            "periods": periods,
            "cheap": [f"{p['start'][11:16]} - €{p['value']:.4f}" for p in periods if p["cheap"]],
            "expensive": [f"{p['start'][11:16]} - €{p['value']:.4f}" for p in periods if p["expensive"]],
            "min": min((p["value"] for p in periods), default=None),
            "max": max((p["value"] for p in periods), default=None),
        }

    return attributes


def _battery_status(data: dict[str, Any], _: SamduoCoordinator) -> str | None:
    power = data.get("battery_power")
    if power is None:
        return None
    if power > STATUS_THRESHOLD_W:
        return "discharging"
    if power < -STATUS_THRESHOLD_W:
        return "charging"
    return "idle"


def _inverter_status(data: dict[str, Any], _: SamduoCoordinator) -> str | None:
    state = data.get("inverter_state")
    if state is None:
        return None
    return "normal" if state == 0 else f"unknown_{state}"


TELEMETRY: tuple[SamduoSensorDescription, ...] = (
    SamduoSensorDescription(
        key="battery_soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SamduoSensorDescription(
        key="battery_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-battery",
    ),
    SamduoSensorDescription(
        key="battery_status",
        device_class=SensorDeviceClass.ENUM,
        options=["charging", "discharging", "idle"],
        icon="mdi:battery-sync",
        value_fn=_battery_status,
    ),
    SamduoSensorDescription(
        key="backup_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:power-plug",
    ),
    SamduoSensorDescription(
        key="energy_charged",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-plus",
    ),
    SamduoSensorDescription(
        key="energy_discharged",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-minus",
    ),
    SamduoSensorDescription(
        key="grid_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamduoSensorDescription(
        key="grid_frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamduoSensorDescription(
        key="offgrid_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamduoSensorDescription(
        key="battery_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamduoSensorDescription(
        key="battery_soh",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamduoSensorDescription(
        key="temperature_1",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamduoSensorDescription(
        key="temperature_2",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamduoSensorDescription(
        key="inverter_status",
        icon="mdi:state-machine",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_inverter_status,
    ),
    SamduoSensorDescription(
        key="error_code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamduoSensorDescription(
        key="control_time_left",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:timer-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

STRATEGY: tuple[SamduoSensorDescription, ...] = (
    SamduoSensorDescription(
        key="strategy_state",
        device_class=SensorDeviceClass.ENUM,
        options=STRATEGY_STATES,
        icon="mdi:brain",
    ),
    SamduoSensorDescription(
        key="control_setpoint",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:gauge",
        value_fn=lambda data, c: 0 if c.commanded_setpoint is None else c.commanded_setpoint,
        attributes_fn=lambda data, c: {"controlling": c.commanded_setpoint is not None},
    ),
    SamduoSensorDescription(
        key="grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
    ),
    SamduoSensorDescription(
        key="house_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    SamduoSensorDescription(
        key="available_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt-circle",
    ),
    SamduoSensorDescription(
        key="needed_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt-outline",
    ),
    SamduoSensorDescription(
        key="remaining_discharge_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:clock-time-eight-outline",
    ),
    SamduoSensorDescription(
        key="remaining_charge_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:clock-time-four-outline",
    ),
    SamduoSensorDescription(
        key="current_price",
        native_unit_of_measurement="€/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        icon="mdi:cash",
    ),
    SamduoSensorDescription(
        key="spread",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent-box",
    ),
    SamduoSensorDescription(
        key="spread_nom",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent-box-outline",
    ),
    SamduoSensorDescription(
        key="spread_tomorrow",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent-box",
    ),
    SamduoSensorDescription(
        key="spread_nom_tomorrow",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent-box-outline",
    ),
    SamduoSensorDescription(
        key="price_periods_today",
        icon="mdi:chart-bar",
        value_fn=lambda data, c: len(data.get("price_periods_today") or []),
        attributes_fn=_periods_attributes("today"),
    ),
    SamduoSensorDescription(
        key="price_periods_tomorrow",
        icon="mdi:chart-bar",
        value_fn=lambda data, c: len(data.get("price_periods_tomorrow") or []),
        attributes_fn=_periods_attributes("tomorrow"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SamduoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SamduoSensor(coordinator, description) for description in TELEMETRY + STRATEGY)


class SamduoSensor(SamduoEntity, SensorEntity):
    entity_description: SamduoSensorDescription

    def __init__(self, coordinator: SamduoCoordinator, description: SamduoSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(self.data, self.coordinator)
        return self.data.get(self._key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.data, self.coordinator)
