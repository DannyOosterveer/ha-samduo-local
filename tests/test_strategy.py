"""Strategy engine: every mode, with hand-written readings.

Sign reminder: battery/setpoint positive = discharging; grid positive =
importing.
"""

from __future__ import annotations

import pytest

from custom_components.samduo.const import DIRECTION_DWELL_S, Mode
from custom_components.samduo.strategy import Prices, Readings, Settings, StrategyEngine

S = Settings()
NO_PRICES = Prices()


def readings(grid: float, battery: float = 0, soc: float = 50, daytime: bool = False) -> Readings:
    return Readings(soc=soc, battery_power=battery, grid_power=grid, grid_available=True, daytime=daytime)


@pytest.fixture
def engine() -> StrategyEngine:
    return StrategyEngine()


def test_standby_releases(engine: StrategyEngine) -> None:
    decision = engine.evaluate(Mode.STANDBY, S, readings(500), NO_PRICES, 0)
    assert decision.setpoint is None
    assert decision.state == "standby"


def test_manual_is_clamped(engine: StrategyEngine) -> None:
    settings = Settings(manual_power=5000, max_discharge_power=600)
    assert engine.evaluate(Mode.MANUAL, settings, readings(0), NO_PRICES, 0).setpoint == 600
    settings = Settings(manual_power=-5000, max_charge_power=700)
    assert engine.evaluate(Mode.MANUAL, settings, readings(0), NO_PRICES, 0).setpoint == -700


def test_fast_modes_respect_soc_limits(engine: StrategyEngine) -> None:
    assert engine.evaluate(Mode.FAST_CHARGE, S, readings(0, soc=50), NO_PRICES, 0).setpoint == -800
    assert engine.evaluate(Mode.FAST_CHARGE, S, readings(0, soc=100), NO_PRICES, 0).state == "soc_max_reached"
    assert engine.evaluate(Mode.FAST_DISCHARGE, S, readings(0, soc=50), NO_PRICES, 0).setpoint == 800
    assert engine.evaluate(Mode.FAST_DISCHARGE, S, readings(0, soc=10), NO_PRICES, 0).state == "soc_min_reached"


def test_no_telemetry(engine: StrategyEngine) -> None:
    decision = engine.evaluate(Mode.ZERO_ON_METER, S, Readings(soc=None, battery_power=None), NO_PRICES, 0)
    assert decision.state == "no_telemetry"


# ── Zero on the meter ──────────────────────────────────────────────────────


def test_nom_waits_below_thresholds(engine: StrategyEngine) -> None:
    assert engine.evaluate(Mode.ZERO_ON_METER, S, readings(80), NO_PRICES, 0).state == "waiting"
    assert engine.evaluate(Mode.ZERO_ON_METER, S, readings(-200), NO_PRICES, 0).state == "waiting"


def test_nom_discharge_starts_at_75_percent_then_balances(engine: StrategyEngine) -> None:
    # Importing 400 W with the battery idle: first step is 75 % of (400 + 5 margin).
    first = engine.evaluate(Mode.ZERO_ON_METER, S, readings(400), NO_PRICES, 0)
    assert first.state == "discharging_nom"
    assert first.setpoint == round(405 * 0.75)
    # Battery now delivers 300 W, the meter still shows 100 W import: balance to 100 %.
    second = engine.evaluate(Mode.ZERO_ON_METER, S, readings(100, battery=300), NO_PRICES, 5)
    assert second.setpoint == 100 + 300 + 5
    # Load disappears: meter exports 500 W while the battery delivers 405 W -> stop.
    third = engine.evaluate(Mode.ZERO_ON_METER, S, readings(-500, battery=405), NO_PRICES, 10)
    assert third.setpoint == 0
    assert third.state == "waiting"


def test_nom_discharge_is_capped(engine: StrategyEngine) -> None:
    decision = engine.evaluate(Mode.ZERO_ON_METER, Settings(max_discharge_power=500), readings(2000), NO_PRICES, 0)
    assert decision.setpoint == 500


def test_nom_charge_starts_and_balances_with_margin(engine: StrategyEngine) -> None:
    # Exporting 600 W: charge 75 % of (600 - 50 margin).
    first = engine.evaluate(Mode.ZERO_ON_METER, S, readings(-600), NO_PRICES, 0)
    assert first.state == "charging_nom"
    assert first.setpoint == -round(550 * 0.75)
    # Battery takes 412 W, meter still exports 188 W: absorb the rest minus the margin.
    second = engine.evaluate(Mode.ZERO_ON_METER, S, readings(-188, battery=-412), NO_PRICES, 5)
    assert second.setpoint == -(188 + 412 - 50)


def test_nom_dwell_time_before_direction_change(engine: StrategyEngine) -> None:
    engine.evaluate(Mode.ZERO_ON_METER, S, readings(400), NO_PRICES, 0)
    stop = engine.evaluate(Mode.ZERO_ON_METER, S, readings(-600, battery=300), NO_PRICES, 5)
    assert stop.setpoint == 0
    # Within the dwell time: wait even though export is beyond the charge threshold.
    waiting = engine.evaluate(Mode.ZERO_ON_METER, S, readings(-600), NO_PRICES, 5 + DIRECTION_DWELL_S - 1)
    assert waiting.state == "waiting" and waiting.setpoint == 0
    start = engine.evaluate(Mode.ZERO_ON_METER, S, readings(-600), NO_PRICES, 5 + DIRECTION_DWELL_S)
    assert start.state == "charging_nom"


def test_nom_respects_soc_limits(engine: StrategyEngine) -> None:
    assert engine.evaluate(Mode.ZERO_ON_METER, S, readings(400, soc=10), NO_PRICES, 0).state == "waiting"
    assert engine.evaluate(Mode.ZERO_ON_METER, S, readings(-600, soc=100), NO_PRICES, 0).state == "waiting"
    engine.evaluate(Mode.ZERO_ON_METER, S, readings(400, soc=50), NO_PRICES, 0)
    assert engine.evaluate(Mode.ZERO_ON_METER, S, readings(100, 300, soc=10), NO_PRICES, 1).state == "soc_min_reached"


def test_soc_protection_charges_in_daylight_or_on_export(engine: StrategyEngine) -> None:
    night_import = engine.evaluate(Mode.ZERO_ON_METER, S, readings(200, soc=5), NO_PRICES, 0)
    assert night_import.state == "waiting"
    daylight = engine.evaluate(Mode.ZERO_ON_METER, S, readings(200, soc=5, daytime=True), NO_PRICES, 0)
    assert daylight.state == "soc_protection" and daylight.setpoint == -800
    export = engine.evaluate(Mode.SMART_DISCHARGE_ONLY, S, readings(-400, soc=5), NO_PRICES, 0)
    assert export.state == "soc_protection"
    disabled = engine.evaluate(Mode.ZERO_ON_METER, Settings(soc_protection=False), readings(-400, soc=5), NO_PRICES, 0)
    assert disabled.state == "charging_nom"


def test_smart_only_modes() -> None:
    charge_only = StrategyEngine()
    assert charge_only.evaluate(Mode.SMART_CHARGE_ONLY, S, readings(400), NO_PRICES, 0).state == "waiting"
    assert charge_only.evaluate(Mode.SMART_CHARGE_ONLY, S, readings(-600), NO_PRICES, 0).state == "charging_nom"
    # Switching mode while charging: the engine stops first, then waits.
    switched = charge_only.evaluate(Mode.SMART_DISCHARGE_ONLY, S, readings(-600), NO_PRICES, 1)
    assert switched.state == "charge_not_allowed"
    discharge_only = StrategyEngine()
    assert discharge_only.evaluate(Mode.SMART_DISCHARGE_ONLY, S, readings(-600), NO_PRICES, 0).state == "waiting"
    assert discharge_only.evaluate(Mode.SMART_DISCHARGE_ONLY, S, readings(400), NO_PRICES, 0).state == "discharging_nom"


def test_nom_without_grid_sensor_holds_zero(engine: StrategyEngine) -> None:
    engine.evaluate(Mode.ZERO_ON_METER, S, readings(400), NO_PRICES, 0)
    stale = Readings(soc=50, battery_power=300, grid_power=100, grid_available=False)
    decision = engine.evaluate(Mode.ZERO_ON_METER, S, stale, NO_PRICES, 1)
    assert decision.setpoint == 0 and decision.state == "grid_sensor_unavailable"


# ── Dynamic modes ──────────────────────────────────────────────────────────

CHEAP = Prices(available=True, cheap_active=True, spread=60, spread_nom=60)
EXPENSIVE = Prices(available=True, expensive_active=True, spread=60, spread_nom=60)
NEUTRAL = Prices(available=True, spread=60, spread_nom=60)


@pytest.mark.parametrize(
    "mode", [Mode.DYNAMIC_NOM, Mode.DYNAMIC_NOM_EXPENSIVE, Mode.DYNAMIC_TRADING, Mode.DYNAMIC_TRADING_NOM]
)
def test_dynamic_without_prices_holds_zero(engine: StrategyEngine, mode: Mode) -> None:
    decision = engine.evaluate(mode, S, readings(400), NO_PRICES, 0)
    assert decision.setpoint == 0 and decision.state == "price_sensor_unavailable"


def test_dynamic_charges_in_cheap_period_when_spread_is_enough(engine: StrategyEngine) -> None:
    decision = engine.evaluate(Mode.DYNAMIC_TRADING, S, readings(0), CHEAP, 0)
    assert decision.setpoint == -800 and decision.state == "dynamic_charging"
    assert engine.dynamic_charging
    low_spread = Prices(available=True, cheap_active=True, spread=10, spread_nom=10)
    decision = engine.evaluate(Mode.DYNAMIC_TRADING, S, readings(0), low_spread, 1)
    assert decision.state == "between_periods"


def test_dynamic_nom_charges_once_per_block_and_then_balances(engine: StrategyEngine) -> None:
    assert engine.evaluate(Mode.DYNAMIC_NOM, S, readings(0, soc=50), CHEAP, 0).state == "dynamic_charging"
    # Cheap period still on but the battery is full: block ends, latch set, the charge stops.
    decision = engine.evaluate(Mode.DYNAMIC_NOM, S, readings(400, soc=100), CHEAP, 1)
    assert engine.recently_charged and not engine.dynamic_charging
    assert decision.state == "soc_max_reached"
    # After the dwell time NOM takes over and discharges against the import.
    later = 1 + DIRECTION_DWELL_S
    assert engine.evaluate(Mode.DYNAMIC_NOM, S, readings(400, soc=100), CHEAP, later).state == "discharging_nom"
    # A second cheap hour at 95 % does not recharge (hysteresis of 10 %)...
    assert engine.evaluate(Mode.DYNAMIC_NOM, S, readings(100, 300, soc=95), CHEAP, later + 1).state == "discharging_nom"
    # ...but it does once the SOC dropped to 90 %.
    recharge = engine.evaluate(Mode.DYNAMIC_NOM, S, readings(100, 300, soc=90), CHEAP, later + 2)
    assert recharge.state == "dynamic_charging"


def test_dynamic_nom_uses_nom_spread(engine: StrategyEngine) -> None:
    prices = Prices(available=True, cheap_active=True, spread=60, spread_nom=5)
    assert engine.evaluate(Mode.DYNAMIC_NOM, S, readings(0), prices, 0).state == "waiting"
    assert engine.evaluate(Mode.DYNAMIC_TRADING, S, readings(0), prices, 0).state == "dynamic_charging"


def test_dynamic_trading_discharges_in_expensive_period(engine: StrategyEngine) -> None:
    decision = engine.evaluate(Mode.DYNAMIC_TRADING, S, readings(0, soc=50), EXPENSIVE, 0)
    assert decision.setpoint == 800 and decision.state == "dynamic_discharging"
    assert engine.dynamic_discharging
    empty = engine.evaluate(Mode.DYNAMIC_TRADING, S, readings(0, 800, soc=10), EXPENSIVE, 1)
    assert empty.setpoint == 0 and empty.state == "soc_min_reached"
    assert not engine.dynamic_discharging
    assert engine.evaluate(Mode.DYNAMIC_TRADING, S, readings(400, soc=50), NEUTRAL, 2).state == "between_periods"


def test_dynamic_trading_nom_balances_between_periods(engine: StrategyEngine) -> None:
    assert engine.evaluate(Mode.DYNAMIC_TRADING_NOM, S, readings(400), NEUTRAL, 0).state == "discharging_nom"
    assert engine.evaluate(Mode.DYNAMIC_TRADING_NOM, S, readings(0, 300), EXPENSIVE, 1).state == "dynamic_discharging"


def test_dynamic_nom_expensive_discharges_only_in_expensive_periods(engine: StrategyEngine) -> None:
    assert engine.evaluate(Mode.DYNAMIC_NOM_EXPENSIVE, S, readings(400), NEUTRAL, 0).state == "waiting"
    assert engine.evaluate(Mode.DYNAMIC_NOM_EXPENSIVE, S, readings(-600), NEUTRAL, 1).state == "charging_nom"
    engine.reset()
    assert engine.evaluate(Mode.DYNAMIC_NOM_EXPENSIVE, S, readings(400), EXPENSIVE, 20).state == "discharging_nom"


def test_trading_recharges_every_cheap_period(engine: StrategyEngine) -> None:
    engine.evaluate(Mode.DYNAMIC_TRADING, S, readings(0, soc=50), CHEAP, 0)
    engine.evaluate(Mode.DYNAMIC_TRADING, S, readings(0, soc=100), CHEAP, 1)
    assert engine.recently_charged
    # Next cheap block, SOC only slightly lower: trading charges again.
    assert engine.evaluate(Mode.DYNAMIC_TRADING, S, readings(0, soc=98), CHEAP, 2).state == "dynamic_charging"
