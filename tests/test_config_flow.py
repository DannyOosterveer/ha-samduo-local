"""Config flow: manual, zeroconf and options."""

from __future__ import annotations

from ipaddress import ip_address

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samduo.const import (
    CONF_CAPACITY_KWH,
    CONF_GRID_POWER_ENTITY,
    CONF_MODEL,
    CONF_POLL_INTERVAL,
    CONF_PRICE_ENTITY,
    CONF_SERIAL,
    DOMAIN,
)

from .conftest import MOCK_SERIAL


def zeroconf_info(pn: str = "samduonexe6000-582abdf5a208", sn: str = MOCK_SERIAL) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address("192.168.1.65"),
        ip_addresses=[ip_address("192.168.1.65")],
        hostname="SamduoNexE6000-582ABDF5A208.local.",
        name=f"{pn}._samduo._tcp.local.",
        port=3335,
        properties={"pn": pn, "sn": sn, "ver": "0.0.1.10"},
        type="_samduo._tcp.local.",
    )


async def test_user_flow(hass: HomeAssistant, mock_client) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.65 ", CONF_PORT: 3335, CONF_NAME: "Garage"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage"
    assert result["data"] == {
        CONF_HOST: "192.168.1.65",
        CONF_PORT: 3335,
        CONF_NAME: "Garage",
        CONF_SERIAL: MOCK_SERIAL,
        CONF_MODEL: "",
    }
    assert result["result"].unique_id == MOCK_SERIAL
    mock_client.async_disconnect.assert_awaited()


async def test_user_flow_cannot_connect(hass: HomeAssistant, mock_client) -> None:
    mock_client.request.return_value = None
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.65", CONF_PORT: 3335, CONF_NAME: "x"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client.async_connect.side_effect = TimeoutError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.65", CONF_PORT: 3335, CONF_NAME: "x"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_zeroconf_flow(hass: HomeAssistant, mock_client) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=zeroconf_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"]["model"] == "Nex E6000"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NAME: ""})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Samduo"
    assert result["data"][CONF_MODEL] == "Nex E6000"
    assert result["data"][CONF_SERIAL] == MOCK_SERIAL


async def test_zeroconf_ignores_p1_meter_and_updates_host(hass: HomeAssistant, mock_client) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf_info(pn="samduop1meter-abc", sn="P1SERIAL"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"

    entry = MockConfigEntry(domain=DOMAIN, unique_id=MOCK_SERIAL, data={CONF_HOST: "10.0.0.1", CONF_PORT: 3335})
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=zeroconf_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.65"


async def test_options_flow(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.70",
            CONF_PORT: 3335,
            CONF_GRID_POWER_ENTITY: "sensor.other_p1",
            CONF_CAPACITY_KWH: 12,
            CONF_POLL_INTERVAL: 10,
            "keepalive_interval": 30,
            "control_timeout": 90,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_HOST] == "192.168.1.70"
    assert entry.options[CONF_GRID_POWER_ENTITY] == "sensor.other_p1"
    assert entry.options[CONF_PRICE_ENTITY] == ""
    assert entry.options[CONF_CAPACITY_KWH] == 12.0
    assert entry.runtime_data.capacity_kwh == 12.0
