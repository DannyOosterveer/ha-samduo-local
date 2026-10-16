"""The strategy engine: turns a mode, settings and live readings into a
power setpoint.

Everything here is pure Python with no Home Assistant imports, so every
mode can be exercised in a unit test with hand-written readings.

Sign conventions (never flipped anywhere in this integration):

* battery power and setpoints: positive = discharging, negative = charging
  (the device's own convention, also what EMHASS expects);
* grid power: positive = importing from the grid, negative = exporting.

The modes follow the Zendure community package by Gielz1986 closely, so a
user coming from there finds the same knobs with the same meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .const import (
    DIRECTION_DWELL_S,
    IDLE_BAND_W,
    RECHARGE_SOC_HYSTERESIS,
    START_FACTOR,
    Mode,
)


@dataclass(frozen=True)
class Settings:
    manual_power: float = 0
    max_charge_power: float = 800
    max_discharge_power: float = 800
    charge_start: float = -300
    discharge_start: float = 100
    charge_margin: float = 50
    discharge_margin: float = 5
    min_soc: float = 10
    max_soc: float = 100
    min_spread: float = 25
    soc_protection: bool = True


@dataclass(frozen=True)
class Readings:
    soc: float | None
    battery_power: float | None
    grid_power: float | None = None
    grid_available: bool = False
    daytime: bool = False


@dataclass(frozen=True)
class Prices:
    available: bool = False
    cheap_active: bool = False
    expensive_active: bool = False
    spread: float = 0.0
    spread_nom: float = 0.0


@dataclass(frozen=True)
class Decision:
    """``setpoint`` None means: release control, let the battery be."""

    setpoint: int | None
    state: str


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class StrategyEngine:
    """Holds the little state the modes need between ticks."""

    recently_charged: bool = False
    dynamic_charging: bool = False
    dynamic_discharging: bool = False
    _last_stop: float = field(default=-1e9)
    _direction: int = 0  # -1 charging, 0 idle, 1 discharging (as commanded)

    def reset(self) -> None:
        self.recently_charged = False
        self.dynamic_charging = False
        self.dynamic_discharging = False
        self._direction = 0

    # ── Entry point ────────────────────────────────────────────────────────

    def evaluate(self, mode: Mode, settings: Settings, readings: Readings, prices: Prices, now: float) -> Decision:
        if mode == Mode.STANDBY:
            self.reset()
            return Decision(None, "standby")

        if mode == Mode.MANUAL:
            power = int(round(_clamp(settings.manual_power, -settings.max_charge_power, settings.max_discharge_power)))
            self._note_direction(power, now)
            return Decision(power, "manual")

        soc = readings.soc
        if soc is None:
            return Decision(0, "no_telemetry")

        if mode == Mode.FAST_CHARGE:
            if soc >= settings.max_soc:
                return self._stop(now, "soc_max_reached")
            return self._go(-int(settings.max_charge_power), now, "fast_charging")

        if mode == Mode.FAST_DISCHARGE:
            if soc <= settings.min_soc:
                return self._stop(now, "soc_min_reached")
            return self._go(int(settings.max_discharge_power), now, "fast_discharging")

        discharge_allowed = mode != Mode.SMART_CHARGE_ONLY
        charge_allowed = mode != Mode.SMART_DISCHARGE_ONLY

        if mode in (Mode.DYNAMIC_NOM, Mode.DYNAMIC_NOM_EXPENSIVE, Mode.DYNAMIC_TRADING, Mode.DYNAMIC_TRADING_NOM):
            decision = self._dynamic(mode, settings, soc, prices, now)
            if decision is not None:
                return decision
            if mode == Mode.DYNAMIC_TRADING:
                return self._stop(now, "between_periods")
            if mode == Mode.DYNAMIC_NOM_EXPENSIVE:
                discharge_allowed = prices.expensive_active and prices.spread >= settings.min_spread
        else:
            self.dynamic_charging = self.dynamic_discharging = False

        return self._zero_on_meter(settings, readings, soc, now, charge_allowed, discharge_allowed)

    # ── Dynamic (price driven) layer ───────────────────────────────────────

    def _dynamic(self, mode: Mode, settings: Settings, soc: float, prices: Prices, now: float) -> Decision | None:
        """Charge in cheap periods, discharge in expensive ones (trading
        modes). Returns None when neither applies and the mode's fallback
        (NOM or standby) should run."""
        if not prices.available:
            self.dynamic_charging = self.dynamic_discharging = False
            return self._stop(now, "price_sensor_unavailable")

        # Dynamic NOM charges once per cheap block and then lives off that
        # charge; it recharges only after the SOC has dropped well below the
        # maximum. Trading modes buy every cheap period.
        if self.recently_charged and (
            mode in (Mode.DYNAMIC_TRADING, Mode.DYNAMIC_TRADING_NOM)
            or soc <= settings.max_soc - RECHARGE_SOC_HYSTERESIS
        ):
            self.recently_charged = False

        spread_ref = prices.spread_nom if mode == Mode.DYNAMIC_NOM else prices.spread
        if prices.cheap_active and not self.recently_charged and soc < settings.max_soc:
            if spread_ref >= settings.min_spread:
                self.dynamic_charging = True
                return self._go(-int(settings.max_charge_power), now, "dynamic_charging")
            self.dynamic_charging = False
            return None
        if self.dynamic_charging:
            # Cheap block ended or the battery is full.
            self.dynamic_charging = False
            self.recently_charged = True

        trading = mode in (Mode.DYNAMIC_TRADING, Mode.DYNAMIC_TRADING_NOM)
        if trading and prices.expensive_active and prices.spread >= settings.min_spread:
            if soc > settings.min_soc:
                self.dynamic_discharging = True
                return self._go(int(settings.max_discharge_power), now, "dynamic_discharging")
            self.dynamic_discharging = False
            return self._stop(now, "soc_min_reached")
        self.dynamic_discharging = False
        return None

    # ── Zero-on-the-meter layer ────────────────────────────────────────────

    def _zero_on_meter(
        self,
        settings: Settings,
        readings: Readings,
        soc: float,
        now: float,
        charge_allowed: bool,
        discharge_allowed: bool,
    ) -> Decision:
        if not readings.grid_available or readings.grid_power is None:
            return self._stop(now, "grid_sensor_unavailable")
        battery = readings.battery_power or 0.0
        grid = readings.grid_power
        # What the meter would read with the battery idle.
        house = grid + battery

        # SOC protection: top the battery up to its minimum from the grid,
        # in daylight or whenever the house is exporting anyway.
        if settings.soc_protection and soc < settings.min_soc and (readings.daytime or grid < settings.charge_start):
            return self._go(-int(settings.max_charge_power), now, "soc_protection")

        if self._direction > 0 and battery > -IDLE_BAND_W:
            # Discharging: balance to the margin at 100 % of the demand.
            if not discharge_allowed or soc <= settings.min_soc:
                return self._stop(now, "soc_min_reached" if soc <= settings.min_soc else "discharge_not_allowed")
            target = _clamp(house + settings.discharge_margin, 0, settings.max_discharge_power)
            if target <= 0:
                return self._stop(now, "waiting")
            return self._go(int(round(target)), now, "discharging_nom")

        if self._direction < 0 and battery < IDLE_BAND_W:
            # Charging: absorb the surplus minus the margin at 100 %.
            if not charge_allowed or soc >= settings.max_soc:
                return self._stop(now, "soc_max_reached" if soc >= settings.max_soc else "charge_not_allowed")
            target = _clamp(-house - settings.charge_margin, 0, settings.max_charge_power)
            if target <= 0:
                return self._stop(now, "waiting")
            return self._go(-int(round(target)), now, "charging_nom")

        # Idle: start only beyond the thresholds, after the dwell time, at
        # 75 % of the demand so the first step does not overshoot.
        if now - self._last_stop < DIRECTION_DWELL_S:
            return Decision(0, "waiting")
        if discharge_allowed and grid > settings.discharge_start and soc > settings.min_soc:
            target = _clamp((house + settings.discharge_margin) * START_FACTOR, 0, settings.max_discharge_power)
            return self._go(int(round(target)), now, "discharging_nom")
        if (
            charge_allowed
            and grid < settings.charge_start
            and soc < settings.max_soc
            and (soc >= settings.min_soc or not settings.soc_protection)
        ):
            target = _clamp((-house - settings.charge_margin) * START_FACTOR, 0, settings.max_charge_power)
            return self._go(-int(round(target)), now, "charging_nom")
        return Decision(0, "waiting")

    # ── Bookkeeping ────────────────────────────────────────────────────────

    def _note_direction(self, power: int, now: float) -> None:
        direction = (power > 0) - (power < 0)
        if direction == 0 and self._direction != 0:
            self._last_stop = now
        self._direction = direction

    def _go(self, power: int, now: float, state: str) -> Decision:
        self._note_direction(power, now)
        return Decision(power, state)

    def _stop(self, now: float, state: str) -> Decision:
        self._note_direction(0, now)
        return Decision(0, state)
