"""Constants for the SAMDUO Nex integration."""

from __future__ import annotations

from enum import StrEnum

DOMAIN = "samduo"
MANUFACTURER = "SAMDUO"
DEFAULT_NAME = "Samduo"

# ── Transport ────────────────────────────────────────────────────────────────
DEFAULT_PORT = 3335
ZEROCONF_TYPE = "_samduo._tcp.local."

# The device answers a request in well under a second; the spec's ACK
# timeout is 8 s, so we never give up before the device would.
RESPONSE_TIMEOUT = 8.0
CONNECT_TIMEOUT = 5.0
RECONNECT_BASE_COOLDOWN = 2.0
RECONNECT_MAX_COOLDOWN = 60.0
# Consecutive silent reads before a possibly half-open socket is recycled.
# This is also the symptom of the SAMDUO app holding the device's only TCP
# slot: the connection is accepted but never answered.
SILENT_READS_BEFORE_RECYCLE = 3

# ── Open API V1.0 services (the complete surface) ────────────────────────────
SERVICE_DEVICE_DATA = "22600"
SERVICE_ENABLE_BACKUP = "22013"
SERVICE_CHECK_BACKUP = "22023"
SERVICE_SET_POWER = "22045"
SERVICE_GET_POWER_CONFIG = "22046"

# mDNS TXT "pn" is "<modelprefix>-<mac>". The SAMDUO P1 meter advertises the
# same service type but speaks a different command set.
PN_MODEL_NAMES = {
    "samduonexe6000": "Nex E6000",
    "samduonexe6000h": "Nex E6000H",
    "samduonexp2800pro": "Nex P2800 Pro",
    "samduonexbp2800": "Nex BP2800",
}
PN_IGNORED_PREFIXES = ("samduop1meter",)
# Nominal usable capacity per model (kWh), used as default for the capacity
# setting. Unknown models fall back to the E6000 figure.
MODEL_CAPACITY_KWH = {
    "Nex E6000": 6.0,
    "Nex E6000H": 6.0,
    "Nex P2800 Pro": 2.8,
    "Nex BP2800": 2.8,
}
DEFAULT_CAPACITY_KWH = 6.0

# ── Config entry data / options ──────────────────────────────────────────────
CONF_SERIAL = "serial"
CONF_MODEL = "model"
CONF_POLL_INTERVAL = "poll_interval"
CONF_GRID_POWER_ENTITY = "grid_power_entity"
CONF_PRICE_ENTITY = "price_entity"
CONF_KEEPALIVE_INTERVAL = "keepalive_interval"
CONF_CONTROL_TIMEOUT = "control_timeout"
CONF_CAPACITY_KWH = "capacity_kwh"

DEFAULT_POLL_INTERVAL = 5
MIN_POLL_INTERVAL = 2
MAX_POLL_INTERVAL = 300
# The 22045 watchdog: the setpoint is re-sent every keepalive interval with
# timeoutS = control timeout, so three missed refreshes free the battery.
DEFAULT_KEEPALIVE_INTERVAL = 30
DEFAULT_CONTROL_TIMEOUT = 90

# ── Device limits ────────────────────────────────────────────────────────────
# Protocol envelope for 22045 (spec §4.4). The firmware does not validate it,
# so the client-side checks are the only protection.
PROTOCOL_POWER_MAX = 5000
PROTOCOL_TIMEOUT_MIN = 1
PROTOCOL_TIMEOUT_MAX = 3600
# The E6000 inverter is rated 2600 W; 800 W is the grid-compliant default.
POWER_LIMIT_MIN = 100
POWER_LIMIT_MAX = 2600
DEFAULT_MAX_POWER = 800
# Releasing control: a short 0 W hold so the battery idles within seconds.
RELEASE_TIMEOUT_S = 5

# ── Strategy settings (number/switch/text entities, restored across restarts)
SETTING_MANUAL_POWER = "manual_power"
SETTING_MAX_CHARGE_POWER = "max_charge_power"
SETTING_MAX_DISCHARGE_POWER = "max_discharge_power"
SETTING_CHARGE_START = "charge_start"
SETTING_DISCHARGE_START = "discharge_start"
SETTING_CHARGE_MARGIN = "charge_margin"
SETTING_DISCHARGE_MARGIN = "discharge_margin"
SETTING_MIN_SOC = "min_soc"
SETTING_MAX_SOC = "max_soc"
SETTING_MIN_SPREAD = "min_spread"
SETTING_EXPORT_CORRECTION = "export_correction"
SETTING_CHEAP_PERIODS = "cheap_periods"
SETTING_EXPENSIVE_PERIODS = "expensive_periods"
SETTING_CHEAP_PERIODS_TOMORROW = "cheap_periods_tomorrow"
SETTING_EXPENSIVE_PERIODS_TOMORROW = "expensive_periods_tomorrow"
SETTING_SOC_PROTECTION = "soc_protection"
SETTING_QUARTER_HOUR_PERIODS = "quarter_hour_periods"
SETTING_MANUAL_PERIODS = "manual_periods"
SETTING_MANUAL_PERIODS_TOMORROW = "manual_periods_tomorrow"

# Number of cheap and expensive periods per day: 3 with hourly prices, 12 with
# quarter-hourly ones (the same three hours). Switching the period length
# re-applies these to today and tomorrow.
PERIOD_COUNT_HOURLY = 3
PERIOD_COUNT_QUARTER_HOURLY = 12
PERIOD_COUNT_SETTINGS = (
    SETTING_CHEAP_PERIODS,
    SETTING_EXPENSIVE_PERIODS,
    SETTING_CHEAP_PERIODS_TOMORROW,
    SETTING_EXPENSIVE_PERIODS_TOMORROW,
)

# Recommended values (the "apply recommended settings" button writes these).
RECOMMENDED_SETTINGS: dict[str, float | bool | str] = {
    SETTING_CHARGE_START: -300,
    SETTING_DISCHARGE_START: 100,
    SETTING_CHARGE_MARGIN: 50,
    SETTING_DISCHARGE_MARGIN: 5,
    SETTING_MIN_SOC: 10,
    SETTING_MAX_SOC: 100,
    SETTING_MIN_SPREAD: 25,
    SETTING_CHEAP_PERIODS: PERIOD_COUNT_HOURLY,
    SETTING_EXPENSIVE_PERIODS: PERIOD_COUNT_HOURLY,
    SETTING_CHEAP_PERIODS_TOMORROW: PERIOD_COUNT_HOURLY,
    SETTING_EXPENSIVE_PERIODS_TOMORROW: PERIOD_COUNT_HOURLY,
    SETTING_EXPORT_CORRECTION: 0.0,
    SETTING_SOC_PROTECTION: True,
    SETTING_MANUAL_PERIODS: "",
    SETTING_MANUAL_PERIODS_TOMORROW: "",
}

DEFAULT_SETTINGS: dict[str, float | bool | str] = {
    **RECOMMENDED_SETTINGS,
    # The period length belongs to the price sensor, not to the recommendations.
    SETTING_QUARTER_HOUR_PERIODS: False,
    SETTING_MANUAL_POWER: 0,
    SETTING_MAX_CHARGE_POWER: DEFAULT_MAX_POWER,
    SETTING_MAX_DISCHARGE_POWER: DEFAULT_MAX_POWER,
}


class Mode(StrEnum):
    """Operating modes of the strategy engine (select entity options)."""

    STANDBY = "standby"
    MANUAL = "manual"
    ZERO_ON_METER = "zero_on_meter"
    DYNAMIC_NOM = "dynamic_nom"
    DYNAMIC_NOM_EXPENSIVE = "dynamic_nom_expensive"
    DYNAMIC_TRADING = "dynamic_trading"
    DYNAMIC_TRADING_NOM = "dynamic_trading_nom"
    SMART_DISCHARGE_ONLY = "smart_discharge_only"
    SMART_CHARGE_ONLY = "smart_charge_only"
    FAST_CHARGE = "fast_charge"
    FAST_DISCHARGE = "fast_discharge"


# Modes that need a grid power (P1) sensor to do anything useful.
P1_MODES = frozenset(
    {
        Mode.ZERO_ON_METER,
        Mode.DYNAMIC_NOM,
        Mode.DYNAMIC_NOM_EXPENSIVE,
        Mode.DYNAMIC_TRADING_NOM,
        Mode.SMART_DISCHARGE_ONLY,
        Mode.SMART_CHARGE_ONLY,
    }
)
# Modes that need a price sensor.
PRICE_MODES = frozenset(
    {
        Mode.DYNAMIC_NOM,
        Mode.DYNAMIC_NOM_EXPENSIVE,
        Mode.DYNAMIC_TRADING,
        Mode.DYNAMIC_TRADING_NOM,
    }
)

# ── Engine tuning ────────────────────────────────────────────────────────────
# Battery power within this band counts as idle (meter noise).
IDLE_BAND_W = 30
# First step towards a new target takes 75 % of the demand, then balancing
# runs at 100 % - the same two-stage approach that keeps the P1 reading
# from overshooting.
START_FACTOR = 0.75
# Minimum time a direction must be held before the opposite one may start.
DIRECTION_DWELL_S = 10
# A grid sensor that has not reported for this long is treated as gone and
# the NOM modes stop the battery instead of steering blind.
GRID_SENSOR_STALE_S = 120
# Hysteresis for the "recently charged" latch in Dynamic NOM: the battery is
# charged again in a cheap period only after it has dropped this far below
# the maximum SOC.
RECHARGE_SOC_HYSTERESIS = 10
# Setpoint changes smaller than this are not written (saves TCP chatter).
SETPOINT_DEADBAND_W = 5

# ── Repairs ──────────────────────────────────────────────────────────────────
ISSUE_HEMS_BLOCKED = "hems_blocked"
ISSUE_MISSING_GRID_SENSOR = "missing_grid_sensor"
ISSUE_MISSING_PRICE_SENSOR = "missing_price_sensor"
