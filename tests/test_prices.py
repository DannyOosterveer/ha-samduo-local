"""Price period parsing, selection and spread."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.samduo import prices

TZ = dt_util.get_time_zone("Europe/Amsterdam")
DAY = date(2026, 10, 16)


def _hourly(values: list[float], day: date = DAY) -> list[dict]:
    return [
        {
            "start": datetime(day.year, day.month, day.day, hour, tzinfo=TZ).isoformat(),
            "end": datetime(day.year, day.month, day.day, hour, tzinfo=TZ) + timedelta(hours=1),
            "value": value,
        }
        for hour, value in enumerate(values)
    ]


# A plausible day: cheap at night (02:00-04:00), expensive in the evening.
PRICES = [0.12, 0.10, 0.05, 0.04, 0.06, 0.11, 0.15, 0.20, 0.22, 0.18, 0.14, 0.12] + [
    0.11,
    0.10,
    0.12,
    0.16,
    0.22,
    0.30,
    0.35,
    0.33,
    0.28,
    0.21,
    0.17,
    0.13,
]


@pytest.fixture(autouse=True)
def _tz():
    dt_util.set_default_time_zone(TZ)
    yield
    dt_util.set_default_time_zone(dt_util.UTC)


def test_parse_nordpool_format() -> None:
    today, tomorrow = prices.parse_price_attributes({"raw_today": _hourly(PRICES), "raw_tomorrow": []}, DAY)
    assert len(today) == 24
    assert tomorrow == []
    assert today[2].value == 0.05
    assert today[2].start.hour == 2
    assert today[2].end - today[2].start == timedelta(hours=1)


def test_parse_entsoe_format_without_end() -> None:
    raw = [{"time": datetime(2026, 10, 16, h, tzinfo=TZ), "price": p} for h, p in enumerate(PRICES)]
    today, _ = prices.parse_price_attributes({"prices_today": raw, "prices_tomorrow": None}, DAY)
    assert len(today) == 24
    assert today[0].end == today[1].start
    assert today[23].end - today[23].start == timedelta(hours=1)


def test_parse_generic_prices_split_by_day() -> None:
    raw = [{"timestamp": datetime(2026, 10, 16, 23, tzinfo=TZ), "price": 0.1}] + [
        {"timestamp": datetime(2026, 10, 17, h, tzinfo=TZ), "price": 0.2} for h in range(3)
    ]
    today, tomorrow = prices.parse_price_attributes({"prices": raw}, DAY)
    assert len(today) == 1
    assert len(tomorrow) == 3


def test_parse_unknown_attributes_is_empty() -> None:
    assert prices.parse_price_attributes({"foo": 1}, DAY) == ([], [])
    assert prices.parse_price_attributes({"raw_today": "nope"}, DAY) == ([], [])


def test_aggregate_hourly() -> None:
    quarters = [
        {
            "start": datetime(2026, 10, 16, 0, minute, tzinfo=TZ),
            "end": datetime(2026, 10, 16, 0, minute, tzinfo=TZ) + timedelta(minutes=15),
            "value": value,
        }
        for minute, value in ((0, 0.1), (15, 0.2), (30, 0.3), (45, 0.4))
    ]
    periods, _ = prices.parse_price_attributes({"raw_today": quarters}, DAY)
    hourly = prices.aggregate_hourly(periods)
    assert len(hourly) == 1
    assert hourly[0].value == pytest.approx(0.25)
    assert hourly[0].end - hourly[0].start == timedelta(hours=1)


def test_classify_by_count() -> None:
    periods, _ = prices.parse_price_attributes({"raw_today": _hourly(PRICES)}, DAY)
    classified = prices.classify(periods, cheap_count=3, expensive_count=2)
    assert [p.start.hour for p in classified if p.cheap] == [2, 3, 4]
    assert [p.start.hour for p in classified if p.expensive] == [18, 19]
    assert prices.classify(periods, 0, 0) == [p for p in classified if False] or all(
        not p.cheap and not p.expensive for p in prices.classify(periods, 0, 0)
    )


def test_classify_manual_overrides_counts() -> None:
    periods, _ = prices.parse_price_attributes({"raw_today": _hourly(PRICES)}, DAY)
    classified = prices.classify(periods, 3, 21, manual="G01:00;D17:00-20:00;C23:00")
    assert [p.start.hour for p in classified if p.cheap] == [1, 23]
    assert [p.start.hour for p in classified if p.expensive] == [17, 18, 19]


def test_parse_manual_periods_tolerates_junk() -> None:
    assert prices.parse_manual_periods("g02:00; D18:00-21:00 ;;bogus;E7:30") == [
        (True, "02:00", None),
        (False, "18:00", "21:00"),
        (False, "7:30", None),
    ]


def test_active_period() -> None:
    periods, _ = prices.parse_price_attributes({"raw_today": _hourly(PRICES)}, DAY)
    classified = prices.classify(periods, 3, 2)
    active = prices.active_period(classified, datetime(2026, 10, 16, 3, 30, tzinfo=TZ))
    assert active is not None and active.cheap and active.start.hour == 3
    assert prices.active_period(classified, datetime(2026, 10, 17, 3, 30, tzinfo=TZ)) is None


def test_spread_and_spread_nom() -> None:
    periods, _ = prices.parse_price_attributes({"raw_today": _hourly(PRICES)}, DAY)
    classified = prices.classify(periods, 3, 2)
    cheap_avg = (0.05 + 0.04 + 0.06) / 3
    expensive_avg = (0.35 + 0.33) / 2
    assert prices.spread(classified) == pytest.approx((expensive_avg - cheap_avg) / cheap_avg * 100, abs=0.1)
    assert prices.spread(classified, export_correction=0.10) == pytest.approx(
        (expensive_avg - 0.10 - cheap_avg) / cheap_avg * 100, abs=0.1
    )
    # Both expensive hours are after the first cheap hour, so NOM spread equals the raw spread.
    assert prices.spread_nom(classified) == prices.spread(classified)
    # Put the expensive hours before the cheap ones: nothing left to displace.
    morning_expensive = prices.classify(periods, 3, 21, manual="G22:00;G23:00;D00:00")
    assert prices.spread_nom(morning_expensive) == 0.0
    assert prices.spread([]) == 0.0


def test_format_periods() -> None:
    periods, _ = prices.parse_price_attributes({"raw_today": _hourly(PRICES)}, DAY)
    classified = prices.classify(periods, 1, 1)
    assert prices.format_periods(classified, "cheap") == ["03:00 - €0.0400"]
    assert prices.format_periods(classified, "expensive") == ["18:00 - €0.3500"]
