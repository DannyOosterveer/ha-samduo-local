"""Shared entity base and error helpers."""

from __future__ import annotations

from typing import NoReturn

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SamduoCoordinator


class SamduoEntity(CoordinatorEntity[SamduoCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SamduoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def data(self) -> dict:
        return self.coordinator.data or {}


def raise_set_failed(entity_name: str) -> NoReturn:
    raise HomeAssistantError(
        translation_domain=DOMAIN, translation_key="set_failed", translation_placeholders={"entity": entity_name}
    )


def raise_set_rejected(entity_name: str, reason: str) -> NoReturn:
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="set_rejected",
        translation_placeholders={"entity": entity_name, "reason": reason},
    )
