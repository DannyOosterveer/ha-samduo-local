"""Coordinator: polls the battery, runs the strategy engine, writes setpoints.

One coordinator per battery. Every poll cycle it

1. refreshes the device watchdog if a setpoint is being held,
2. reads telemetry (22600) and the control state (22046),
3. gathers the grid power and price context from Home Assistant,
4. asks the strategy engine for a decision and writes it when it differs
   from what the battery is already holding.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import prices as price_logic
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
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SETTINGS,
    DOMAIN,
    GRID_SENSOR_STALE_S,
    ISSUE_HEMS_BLOCKED,
    ISSUE_MISSING_GRID_SENSOR,
    ISSUE_MISSING_PRICE_SENSOR,
    MANUFACTURER,
    MIN_POLL_INTERVAL,
    P1_MODES,
    PERIOD_COUNT_HOURLY,
    PERIOD_COUNT_QUARTER_HOURLY,
    PERIOD_COUNT_SETTINGS,
    PRICE_MODES,
    RECOMMENDED_SETTINGS,
    RELEASE_TIMEOUT_S,
    SETPOINT_DEADBAND_W,
    SETTING_CHARGE_MARGIN,
    SETTING_CHARGE_START,
    SETTING_CHEAP_PERIODS,
    SETTING_CHEAP_PERIODS_TOMORROW,
    SETTING_DISCHARGE_MARGIN,
    SETTING_DISCHARGE_START,
    SETTING_EXPENSIVE_PERIODS,
    SETTING_EXPENSIVE_PERIODS_TOMORROW,
    SETTING_EXPORT_CORRECTION,
    SETTING_MANUAL_PERIODS,
    SETTING_MANUAL_PERIODS_TOMORROW,
    SETTING_MANUAL_POWER,
    SETTING_MAX_CHARGE_POWER,
    SETTING_MAX_DISCHARGE_POWER,
    SETTING_MAX_SOC,
    SETTING_MIN_SOC,
    SETTING_MIN_SPREAD,
    SETTING_QUARTER_HOUR_PERIODS,
    SETTING_SOC_PROTECTION,
    Mode,
)
from .strategy import Decision, Prices, Readings, Settings, StrategyEngine

_LOGGER = logging.getLogger(__name__)

# A mode or setting change refreshes at once; further changes within this
# window are batched into one refresh.
REFRESH_COOLDOWN_S = 1.0

type SamduoConfigEntry = ConfigEntry[SamduoCoordinator]


class SamduoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one battery and drives it according to the selected mode."""

    # Delay between a write and the readback that records what the device
    # stored; purely diagnostic.
    _VERIFY_DELAY_S = 0.5
    _WRITE_RETRIES = 2
    _WRITE_RETRY_DELAY_S = 1.0

    # HEMS-conflict detector: with "HEMS Managed" on, the battery confirms
    # every setpoint and quietly ignores it. The only tell is delivered
    # power not following the command for a while.
    _TRACK_TOLERANCE_W = 150
    _TRACK_TOLERANCE_REL = 0.10
    _TRACK_MIN_POLLS = 3
    _TRACK_MIN_SECONDS = 10.0
    _TRACK_SETTLE_S = 10.0

    def __init__(self, hass: HomeAssistant, entry: SamduoConfigEntry, client: SamduoClient) -> None:
        self.entry = entry
        poll = int(entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.title}",
            update_interval=timedelta(seconds=max(poll, MIN_POLL_INTERVAL)),
            config_entry=entry,
            request_refresh_debouncer=Debouncer(hass, _LOGGER, cooldown=REFRESH_COOLDOWN_S, immediate=True),
        )
        self.client = client
        self.device_name: str = entry.title
        self.model: str = entry.data.get(CONF_MODEL, "")
        self._configured_serial: str | None = entry.data.get(CONF_SERIAL)
        self.keepalive_interval = int(entry.options.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL))
        self.control_timeout = int(entry.options.get(CONF_CONTROL_TIMEOUT, DEFAULT_CONTROL_TIMEOUT))
        if self.control_timeout <= self.keepalive_interval:
            self.control_timeout = self.keepalive_interval * 3
        self.capacity_kwh = float(entry.options.get(CONF_CAPACITY_KWH, DEFAULT_CAPACITY_KWH))
        self.grid_entity: str | None = entry.options.get(CONF_GRID_POWER_ENTITY) or None
        self.price_entity: str | None = entry.options.get(CONF_PRICE_ENTITY) or None

        # Strategy state. Settings are owned here; the number/switch/text
        # entities are views on this dict and restore their last value into
        # it at startup.
        self.mode: Mode = Mode.STANDBY
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.engine = StrategyEngine()
        self._last_tick_date = dt_util.now().date()

        # Control state. None = not controlling; an int (including 0) is an
        # actively held setpoint the keepalive refreshes.
        self._commanded: int | None = None
        self._last_keepalive = 0.0
        self._setpoint_changed_at = 0.0
        self._track_streak = 0
        self._track_since: float | None = None
        self.control_blocked = False
        self._write_lock = asyncio.Lock()
        self.write_history: deque[dict[str, Any]] = deque(maxlen=20)

        self._last_good: dict[str, Any] = {}
        self._failed_polls = 0
        self._failure_tolerance = 5

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Raise repair issues for missing helpers; call once after setup."""
        self._check_helper_issues()

    async def async_stop(self) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, self.hems_issue_id)
        for issue in (ISSUE_MISSING_GRID_SENSOR, ISSUE_MISSING_PRICE_SENSOR):
            ir.async_delete_issue(self.hass, DOMAIN, f"{issue}_{self.entry.entry_id}")
        if self._commanded is not None:
            await self.async_release_control()
        await self.client.async_disconnect()

    # ── Identity ───────────────────────────────────────────────────────────

    @property
    def device_serial(self) -> str | None:
        return self._configured_serial or self.client.device_serial

    @property
    def hub_identifier(self) -> str:
        return self.device_serial or f"{self.client.host}:{self.client.port}"

    @property
    def commanded_setpoint(self) -> int | None:
        return self._commanded

    @property
    def device_info(self) -> DeviceInfo:
        data = self.data or {}
        versions = [
            f"{label} {data[key]}"
            for label, key in (("INV", "inverter_version"), ("BMS", "bms_version"), ("EMS", "ems_version"))
            if data.get(key) is not None
        ]
        return DeviceInfo(
            identifiers={(DOMAIN, self.hub_identifier)},
            name=self.device_name,
            manufacturer=MANUFACTURER,
            model=self.model or None,
            serial_number=self.device_serial,
            sw_version=" / ".join(versions) or None,
        )

    # ── Poll cycle ─────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        await self._maybe_keepalive()

        device = await self.client.get_device_data()
        if device is None:
            return self._failed_poll()
        data = dict(device)

        power_config = await self.client.get_power_config()
        if power_config is not None:
            data.update(power_config)
        else:
            for key in ("control_power", "control_timeout", "control_time_left"):
                if key in self._last_good:
                    data[key] = self._last_good[key]

        self._failed_polls = 0
        self._evaluate_tracking(data)

        self._rollover_if_new_day()
        data.update(self._derive(data))
        decision = self._decide(data)
        data["strategy_state"] = decision.state
        await self._apply(decision)
        data["control_setpoint"] = self._commanded
        data["control_blocked"] = self.control_blocked
        self._last_good = data
        return data

    def _failed_poll(self) -> dict[str, Any]:
        self._failed_polls += 1
        if self._last_good and self._failed_polls <= self._failure_tolerance:
            _LOGGER.debug("Poll failed (%d/%d), serving last good data", self._failed_polls, self._failure_tolerance)
            return self._last_good
        raise UpdateFailed(
            f"No answer from the battery for {self._failed_polls} polls "
            f"(connection failures: {self.client.consecutive_failures})"
        )

    # ── Derived values for the sensors ─────────────────────────────────────

    def _grid_power(self) -> tuple[float | None, bool]:
        """The grid power and whether it is fresh enough to steer on.

        Freshness comes from ``last_reported``, which moves every time the
        sensor writes its state, also when the value did not change; a
        sensor that stopped reporting is treated as gone.
        """
        if not self.grid_entity:
            return None, False
        state = self.hass.states.get(self.grid_entity)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None, False
        try:
            value = float(state.state)
        except ValueError:
            return None, False
        age = (dt_util.utcnow() - state.last_reported).total_seconds()
        return value, age < GRID_SENSOR_STALE_S

    def _daytime(self) -> bool:
        """Two hours after sunrise until two hours before sunset."""
        now = dt_util.utcnow()
        try:
            sunrise = get_astral_event_date(self.hass, "sunrise", now)
            sunset = get_astral_event_date(self.hass, "sunset", now)
        except Exception:  # noqa: BLE001 - no location configured
            return False
        if sunrise is None or sunset is None:
            return False
        return sunrise + timedelta(hours=2) <= now <= sunset - timedelta(hours=2)

    def _price_context(self) -> dict[str, Any]:
        empty = {
            "price_available": False,
            "current_price": None,
            "cheap_active": False,
            "expensive_active": False,
            "spread": 0.0,
            "spread_nom": 0.0,
            "spread_tomorrow": 0.0,
            "spread_nom_tomorrow": 0.0,
            "price_periods_today": [],
            "price_periods_tomorrow": [],
        }
        if not self.price_entity:
            return empty
        state = self.hass.states.get(self.price_entity)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return empty
        now = dt_util.now()
        today_raw, tomorrow_raw = price_logic.parse_price_attributes(state.attributes, now.date())
        if not self.settings[SETTING_QUARTER_HOUR_PERIODS]:
            today_raw = price_logic.aggregate_hourly(today_raw)
            tomorrow_raw = price_logic.aggregate_hourly(tomorrow_raw)
        today = price_logic.classify(
            today_raw,
            int(self.settings[SETTING_CHEAP_PERIODS]),
            int(self.settings[SETTING_EXPENSIVE_PERIODS]),
            str(self.settings[SETTING_MANUAL_PERIODS]),
        )
        tomorrow = price_logic.classify(
            tomorrow_raw,
            int(self.settings[SETTING_CHEAP_PERIODS_TOMORROW]),
            int(self.settings[SETTING_EXPENSIVE_PERIODS_TOMORROW]),
            str(self.settings[SETTING_MANUAL_PERIODS_TOMORROW]),
        )
        correction = float(self.settings[SETTING_EXPORT_CORRECTION])
        active = price_logic.active_period(today, now)
        return {
            "price_available": bool(today),
            "current_price": round(active.value, 5) if active else None,
            "cheap_active": bool(active and active.cheap),
            "expensive_active": bool(active and active.expensive),
            "spread": price_logic.spread(today, correction),
            "spread_nom": price_logic.spread_nom(today),
            "spread_tomorrow": price_logic.spread(tomorrow, correction),
            "spread_nom_tomorrow": price_logic.spread_nom(tomorrow),
            "price_periods_today": [p.as_dict() for p in today],
            "price_periods_tomorrow": [p.as_dict() for p in tomorrow],
        }

    def _derive(self, data: dict[str, Any]) -> dict[str, Any]:
        grid, grid_fresh = self._grid_power()
        battery = data.get("battery_power")
        soc = data.get("battery_soc")
        derived: dict[str, Any] = {
            "grid_power": grid,
            "grid_available": grid is not None and grid_fresh,
            "house_power": None if grid is None or battery is None else grid + battery,
            "mode": self.mode,
        }
        derived.update(self._price_context())

        min_soc = float(self.settings[SETTING_MIN_SOC])
        max_soc = float(self.settings[SETTING_MAX_SOC])
        if soc is not None:
            usable = max(min(soc, max_soc) - min_soc, 0.0)
            needed = max(max_soc - min(soc, max_soc), 0.0)
            derived["available_energy"] = round(usable / 100 * self.capacity_kwh, 2)
            derived["needed_energy"] = round(needed / 100 * self.capacity_kwh, 2)
        else:
            derived["available_energy"] = derived["needed_energy"] = None

        derived["remaining_discharge_time"] = derived["remaining_charge_time"] = None
        if battery is not None and soc is not None:
            if battery > 0 and derived["available_energy"] is not None:
                derived["remaining_discharge_time"] = round(derived["available_energy"] / (battery / 1000) * 60)
            if battery < 0 and derived["needed_energy"] is not None:
                derived["remaining_charge_time"] = round(derived["needed_energy"] / (-battery / 1000) * 60)
        return derived

    # ── Strategy ───────────────────────────────────────────────────────────

    def _strategy_settings(self) -> Settings:
        s = self.settings
        return Settings(
            manual_power=float(s[SETTING_MANUAL_POWER]),
            max_charge_power=float(s[SETTING_MAX_CHARGE_POWER]),
            max_discharge_power=float(s[SETTING_MAX_DISCHARGE_POWER]),
            charge_start=float(s[SETTING_CHARGE_START]),
            discharge_start=float(s[SETTING_DISCHARGE_START]),
            charge_margin=float(s[SETTING_CHARGE_MARGIN]),
            discharge_margin=float(s[SETTING_DISCHARGE_MARGIN]),
            min_soc=float(s[SETTING_MIN_SOC]),
            max_soc=float(s[SETTING_MAX_SOC]),
            min_spread=float(s[SETTING_MIN_SPREAD]),
            soc_protection=bool(s[SETTING_SOC_PROTECTION]),
        )

    def _decide(self, data: dict[str, Any]) -> Decision:
        readings = Readings(
            soc=data.get("battery_soc"),
            battery_power=data.get("battery_power"),
            grid_power=data.get("grid_power"),
            grid_available=bool(data.get("grid_available")),
            daytime=self._daytime() if self.mode in P1_MODES else False,
        )
        prices = Prices(
            available=bool(data.get("price_available")),
            cheap_active=bool(data.get("cheap_active")),
            expensive_active=bool(data.get("expensive_active")),
            spread=float(data.get("spread") or 0.0),
            spread_nom=float(data.get("spread_nom") or 0.0),
        )
        return self.engine.evaluate(self.mode, self._strategy_settings(), readings, prices, time.monotonic())

    async def _apply(self, decision: Decision) -> None:
        if decision.setpoint is None:
            if self._commanded is not None:
                await self.async_release_control()
            return
        if self._commanded is None and decision.setpoint == 0:
            # Nothing held and nothing to do: leave the battery alone.
            return
        if self._commanded is not None and abs(decision.setpoint - self._commanded) < SETPOINT_DEADBAND_W:
            return
        await self.async_set_power_setpoint(decision.setpoint)

    def _rollover_if_new_day(self) -> None:
        """At midnight the 'tomorrow' period settings become today's."""
        today = dt_util.now().date()
        if today == self._last_tick_date:
            return
        self._last_tick_date = today
        self.settings[SETTING_CHEAP_PERIODS] = self.settings[SETTING_CHEAP_PERIODS_TOMORROW]
        self.settings[SETTING_EXPENSIVE_PERIODS] = self.settings[SETTING_EXPENSIVE_PERIODS_TOMORROW]
        self.settings[SETTING_MANUAL_PERIODS] = self.settings[SETTING_MANUAL_PERIODS_TOMORROW]
        self.settings[SETTING_MANUAL_PERIODS_TOMORROW] = ""
        _LOGGER.info("New day: tomorrow's dynamic period settings are now active")

    # ── Settings and mode (called by the entities) ─────────────────────────

    async def async_set_mode(self, mode: Mode) -> None:
        if mode == self.mode:
            return
        _LOGGER.info("Mode changed from %s to %s", self.mode, mode)
        self.mode = mode
        self.engine.reset()
        self._check_helper_issues()
        self.async_update_listeners()
        await self.async_request_refresh()

    def restore_mode(self, mode: Mode) -> None:
        """Restore the last mode at startup without triggering a refresh."""
        self.mode = mode
        self._check_helper_issues()

    async def async_set_setting(self, key: str, value: Any) -> None:
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        if key == SETTING_QUARTER_HOUR_PERIODS:
            self._apply_period_counts()
        self.async_update_listeners()
        await self.async_request_refresh()

    def _apply_period_counts(self) -> None:
        """Hourly prices: 3 cheap and 3 expensive periods; quarter-hourly: 12."""
        count = PERIOD_COUNT_QUARTER_HOURLY if self.settings[SETTING_QUARTER_HOUR_PERIODS] else PERIOD_COUNT_HOURLY
        for key in PERIOD_COUNT_SETTINGS:
            self.settings[key] = count

    def restore_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value

    async def async_apply_recommended_settings(self) -> None:
        self.settings.update(RECOMMENDED_SETTINGS)
        self._apply_period_counts()
        _LOGGER.info("Recommended settings applied")
        self.async_update_listeners()
        await self.async_request_refresh()

    def _check_helper_issues(self) -> None:
        grid_issue = f"{ISSUE_MISSING_GRID_SENSOR}_{self.entry.entry_id}"
        price_issue = f"{ISSUE_MISSING_PRICE_SENSOR}_{self.entry.entry_id}"
        if self.mode in P1_MODES and not self.grid_entity:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                grid_issue,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_MISSING_GRID_SENSOR,
                translation_placeholders={"device_name": self.device_name},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, grid_issue)
        if self.mode in PRICE_MODES and not self.price_entity:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                price_issue,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_MISSING_PRICE_SENSOR,
                translation_placeholders={"device_name": self.device_name},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, price_issue)

    # ── HEMS-conflict detector ─────────────────────────────────────────────

    @property
    def hems_issue_id(self) -> str:
        return f"{ISSUE_HEMS_BLOCKED}_{self.entry.entry_id}"

    def _evaluate_tracking(self, data: dict[str, Any]) -> None:
        commanded = self._commanded
        if commanded is None:
            self._clear_tracking()
            return
        delivered = data.get("battery_power")
        soc = data.get("battery_soc")
        if delivered is None or time.monotonic() - self._setpoint_changed_at < self._TRACK_SETTLE_S:
            return
        if commanded < 0 and soc is not None and soc >= 99:
            return
        if commanded > 0 and soc is not None and soc <= 5:
            return
        if data.get("inverter_state") not in (0, None):
            return
        tolerance = max(self._TRACK_TOLERANCE_W, abs(commanded) * self._TRACK_TOLERANCE_REL)
        if abs(delivered - commanded) <= tolerance:
            self._clear_tracking()
            return
        if commanded != 0 and delivered * commanded > 0 and abs(delivered) >= 100:
            self._clear_tracking()  # delivering in the right direction: an app limit, not a refusal
            return
        now = time.monotonic()
        if self._track_since is None:
            self._track_since = now
        self._track_streak += 1
        if (
            not self.control_blocked
            and self._track_streak >= self._TRACK_MIN_POLLS
            and now - self._track_since >= self._TRACK_MIN_SECONDS
        ):
            self.control_blocked = True
            _LOGGER.warning(
                "The battery is not following the %d W setpoint (delivering %s W). "
                "Is 'HEMS Managed' switched on in the SAMDUO app?",
                commanded,
                delivered,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.hems_issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_HEMS_BLOCKED,
                translation_placeholders={"device_name": self.device_name},
            )

    def _clear_tracking(self) -> None:
        self._track_streak = 0
        self._track_since = None
        if self.control_blocked:
            self.control_blocked = False
            _LOGGER.info("The battery follows setpoints again")
            ir.async_delete_issue(self.hass, DOMAIN, self.hems_issue_id)

    # ── Control writes ─────────────────────────────────────────────────────

    async def async_set_power_setpoint(self, watts: float) -> bool:
        """Hold a signed setpoint: + discharge, - charge, 0 idle."""
        power = int(round(watts))
        max_charge = int(self.settings[SETTING_MAX_CHARGE_POWER])
        max_discharge = int(self.settings[SETTING_MAX_DISCHARGE_POWER])
        if power > max_discharge:
            _LOGGER.warning("Discharge setpoint %d W clamped to the %d W limit", power, max_discharge)
            power = max_discharge
        elif power < -max_charge:
            _LOGGER.warning("Charge setpoint %d W clamped to the %d W limit", -power, max_charge)
            power = -max_charge

        ok = await self._write(
            f"setpoint {power} W",
            lambda: self.client.set_power_control(power, self.control_timeout),
            expected={"power": power, "timeoutS": self.control_timeout},
        )
        if ok:
            previous = self._commanded
            tolerance = max(self._TRACK_TOLERANCE_W, abs(power) * self._TRACK_TOLERANCE_REL)
            sign = (power > 0) - (power < 0)
            direction_changed = previous is not None and sign != (previous > 0) - (previous < 0)
            if previous is None or direction_changed or abs(power - previous) > tolerance:
                self._setpoint_changed_at = time.monotonic()
                self._track_streak = 0
                self._track_since = None
            self._commanded = power
            self._last_keepalive = time.monotonic()
            self.async_update_listeners()
        return ok

    async def async_release_control(self) -> bool:
        """Hand the battery back to itself: a short 0 W hold, then the
        watchdog expires and the device resumes self-management."""
        if self._commanded is None:
            return True
        ok = await self._write(
            "release control",
            lambda: self.client.set_power_control(0, RELEASE_TIMEOUT_S),
            expected={"power": 0, "timeoutS": RELEASE_TIMEOUT_S},
        )
        if ok:
            self._commanded = None
            self._clear_tracking()
            self.async_update_listeners()
        return ok

    async def async_set_backup(self, enabled: bool) -> bool:
        ok = await self._write(f"backup {'on' if enabled else 'off'}", lambda: self.client.set_backup(enabled))
        if ok and self.data is not None:
            self.data["backup_enabled"] = 1 if enabled else 0
            self.async_update_listeners()
        return ok

    async def _maybe_keepalive(self) -> None:
        if self._commanded is None or time.monotonic() - self._last_keepalive < self.keepalive_interval:
            return
        held = self._commanded
        ok = await self._write(
            f"keepalive {held} W",
            lambda: self.client.set_power_control(held, self.control_timeout),
            expected={"power": held, "timeoutS": self.control_timeout},
        )
        if ok:
            self._last_keepalive = time.monotonic()
        else:
            _LOGGER.warning(
                "Keepalive for the %d W setpoint not confirmed; the battery frees itself %d s after the last refresh",
                held,
                self.control_timeout,
            )

    async def _write(self, operation: str, send, expected: dict[str, Any] | None = None) -> bool:
        """Send a control write with retries and record it in the audit trail."""
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "operation": operation,
            "attempts": 0,
            "confirmed": False,
            "echo": None,
            "echo_match": None,
        }
        self.write_history.append(entry)
        result = None
        async with self._write_lock:
            for attempt in range(1 + self._WRITE_RETRIES):
                entry["attempts"] = attempt + 1
                result = await send()
                if result is not None or attempt >= self._WRITE_RETRIES or self.client.consecutive_failures >= 3:
                    break
                await asyncio.sleep(self._WRITE_RETRY_DELAY_S)
        entry["confirmed"] = result is not None
        if result is None:
            _LOGGER.warning("Write '%s': no answer from the battery after %d attempt(s)", operation, entry["attempts"])
            return False
        entry["echo"] = dict(result)
        if expected is not None:
            entry["echo_match"] = all(result.get(k) == v for k, v in expected.items())
            if not entry["echo_match"]:
                _LOGGER.warning("Write '%s' echo mismatch: sent %s, got %s", operation, expected, result)
        return True
