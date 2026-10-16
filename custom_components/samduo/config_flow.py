"""Config flow: mDNS discovery, manual setup, and the options."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import SamduoClient
from .const import (
    CONF_CAPACITY_KWH,
    CONF_CONTROL_TIMEOUT,
    CONF_GRID_POWER_ENTITY,
    CONF_KEEPALIVE_INTERVAL,
    CONF_MODEL,
    CONF_POLL_INTERVAL,
    CONF_PRICE_ENTITY,
    CONF_SERIAL,
    DEFAULT_CAPACITY_KWH,
    DEFAULT_CONTROL_TIMEOUT,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    MODEL_CAPACITY_KWH,
    PN_IGNORED_PREFIXES,
    PN_MODEL_NAMES,
    SERVICE_CHECK_BACKUP,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
    }
)


def model_from_pn(pn: str) -> str:
    return PN_MODEL_NAMES.get(pn.split("-")[0].lower(), "")


async def probe_device(host: str, port: int) -> str | None:
    """Connect, send one harmless read, return the serial from the reply."""
    client = SamduoClient(host, port)
    try:
        await client.async_connect()
        if await client.request(SERVICE_CHECK_BACKUP) is None:
            return None
        return client.device_serial
    finally:
        await client.async_disconnect()


class SamduoConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SamduoOptionsFlow:
        return SamduoOptionsFlow()

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        properties = {str(k): str(v) for k, v in discovery_info.properties.items()}
        pn = properties.get("pn", "")
        serial = properties.get("sn", "")
        if not serial or pn.lower().startswith(PN_IGNORED_PREFIXES):
            return self.async_abort(reason="not_supported")

        await self.async_set_unique_id(serial)
        host = str(discovery_info.ip_address)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        model = model_from_pn(pn)
        self._discovered = {
            CONF_HOST: host,
            CONF_PORT: discovery_info.port or DEFAULT_PORT,
            CONF_SERIAL: serial,
            CONF_MODEL: model,
        }
        self.context["title_placeholders"] = {"name": f"SAMDUO {model}".strip(), "host": host}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        model = self._discovered[CONF_MODEL]
        if user_input is not None:
            name = user_input[CONF_NAME].strip() or DEFAULT_NAME
            return self.async_create_entry(title=name, data={**self._discovered, CONF_NAME: name})
        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=vol.Schema({vol.Required(CONF_NAME, default=DEFAULT_NAME): str}),
            description_placeholders={
                "model": model or "SAMDUO battery",
                "host": self._discovered[CONF_HOST],
                "serial": self._discovered[CONF_SERIAL],
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            try:
                serial = await probe_device(host, port)
            except (TimeoutError, OSError):
                serial = None
            if serial is None:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                name = user_input[CONF_NAME].strip() or DEFAULT_NAME
                return self.async_create_entry(
                    title=name,
                    data={CONF_HOST: host, CONF_PORT: port, CONF_NAME: name, CONF_SERIAL: serial, CONF_MODEL: ""},
                )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)


class SamduoOptionsFlow(OptionsFlow):
    """Connection, helper sensors and watchdog tuning."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, CONF_HOST: user_input[CONF_HOST].strip(), CONF_PORT: int(user_input[CONF_PORT])},
            )
            options = {
                CONF_GRID_POWER_ENTITY: user_input.get(CONF_GRID_POWER_ENTITY) or "",
                CONF_PRICE_ENTITY: user_input.get(CONF_PRICE_ENTITY) or "",
                CONF_CAPACITY_KWH: float(user_input[CONF_CAPACITY_KWH]),
                CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL]),
                CONF_KEEPALIVE_INTERVAL: int(user_input[CONF_KEEPALIVE_INTERVAL]),
                CONF_CONTROL_TIMEOUT: int(user_input[CONF_CONTROL_TIMEOUT]),
            }
            return self.async_create_entry(title="", data=options)

        data, options = entry.data, entry.options
        default_capacity = options.get(
            CONF_CAPACITY_KWH, MODEL_CAPACITY_KWH.get(data.get(CONF_MODEL, ""), DEFAULT_CAPACITY_KWH)
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=data.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=data.get(CONF_PORT, DEFAULT_PORT)): vol.Coerce(int),
                vol.Optional(
                    CONF_GRID_POWER_ENTITY,
                    description={"suggested_value": options.get(CONF_GRID_POWER_ENTITY) or None},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power", multiple=False)
                ),
                vol.Optional(
                    CONF_PRICE_ENTITY,
                    description={"suggested_value": options.get(CONF_PRICE_ENTITY) or None},
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", multiple=False)),
                vol.Required(CONF_CAPACITY_KWH, default=default_capacity): vol.All(
                    vol.Coerce(float), vol.Range(min=0.5, max=100)
                ),
                vol.Required(
                    CONF_POLL_INTERVAL, default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL)),
                vol.Required(
                    CONF_KEEPALIVE_INTERVAL,
                    default=options.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
                vol.Required(
                    CONF_CONTROL_TIMEOUT, default=options.get(CONF_CONTROL_TIMEOUT, DEFAULT_CONTROL_TIMEOUT)
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
