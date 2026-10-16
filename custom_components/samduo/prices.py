"""Dynamic price periods: parsing, selection and spread.

Pure functions over a list of price periods. The periods come from the
attributes of a price sensor in Home Assistant; several popular sensors are
understood (Nordpool ``raw_today``/``raw_tomorrow``, ENTSO-e
``prices_today``/``prices_tomorrow``, and generic ``prices`` lists with a
timestamp and a price per entry).

Terminology follows the Zendure community package this is modelled on:
a day has N *cheap* periods (when the battery is charged from the grid) and
M *expensive* periods (when it is discharged), and the *spread* is the
relative price difference between the two groups.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

_START_KEYS = ("start", "time", "timestamp", "datetime", "from", "start_time")
_END_KEYS = ("end", "till", "to", "end_time")
_VALUE_KEYS = ("value", "price", "price_ct_per_kwh")

_MANUAL_ITEM = re.compile(r"^\s*([GDCE])\s*(\d{1,2}:\d{2})(?:\s*-\s*(\d{1,2}:\d{2}))?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class PricePeriod:
    start: datetime
    end: datetime
    value: float
    cheap: bool = False
    expensive: bool = False

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "value": round(self.value, 5),
            "cheap": self.cheap,
            "expensive": self.expensive,
        }


# ── Parsing ─────────────────────────────────────────────────────────────────


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_local(parsed)


def _first(entry: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in entry:
            return entry[key]
    return None


def _parse_list(raw: Any) -> list[PricePeriod]:
    """Parse a list of period dicts; entries without an end get one from the
    spacing to the next entry (or an hour for the last one)."""
    if not isinstance(raw, list):
        return []
    partial: list[tuple[datetime, datetime | None, float]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        start = _to_datetime(_first(entry, _START_KEYS))
        value = _first(entry, _VALUE_KEYS)
        if start is None or isinstance(value, bool) or not isinstance(value, int | float):
            continue
        end = _to_datetime(_first(entry, _END_KEYS))
        partial.append((start, end, float(value)))
    partial.sort(key=lambda item: item[0])

    periods: list[PricePeriod] = []
    for index, (start, end, value) in enumerate(partial):
        if end is None:
            if index + 1 < len(partial):
                end = partial[index + 1][0]
            elif index > 0:
                end = start + (start - partial[index - 1][0])
            else:
                end = start + timedelta(hours=1)
        periods.append(PricePeriod(start=start, end=end, value=value))
    return periods


def parse_price_attributes(attributes: Mapping[str, Any], today: date) -> tuple[list[PricePeriod], list[PricePeriod]]:
    """Return (today, tomorrow) period lists from a price sensor's attributes."""
    if "raw_today" in attributes or "raw_tomorrow" in attributes:
        return _parse_list(attributes.get("raw_today")), _parse_list(attributes.get("raw_tomorrow"))
    if "prices_today" in attributes or "prices_tomorrow" in attributes:
        return _parse_list(attributes.get("prices_today")), _parse_list(attributes.get("prices_tomorrow"))
    if "prices" in attributes:
        everything = _parse_list(attributes.get("prices"))
        tomorrow = today + timedelta(days=1)
        return (
            [p for p in everything if p.start.date() == today],
            [p for p in everything if p.start.date() == tomorrow],
        )
    return [], []


def aggregate_hourly(periods: list[PricePeriod]) -> list[PricePeriod]:
    """Average quarter-hour periods into hourly ones (flags are dropped)."""
    buckets: dict[datetime, list[float]] = {}
    for period in periods:
        hour = period.start.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour, []).append(period.value)
    return [
        PricePeriod(start=hour, end=hour + timedelta(hours=1), value=sum(values) / len(values))
        for hour, values in sorted(buckets.items())
    ]


# ── Selection ───────────────────────────────────────────────────────────────


def parse_manual_periods(text: str) -> list[tuple[bool, str, str | None]]:
    """Parse ``G02:00;D18:00-21:00`` into (is_cheap, start, end) tuples.

    ``G``/``C`` mark cheap periods, ``D``/``E`` expensive ones (Dutch
    goedkoop/duur or English cheap/expensive). A single time covers one
    period slot; a range covers every slot that overlaps it.
    """
    items: list[tuple[bool, str, str | None]] = []
    for raw_item in text.split(";"):
        match = _MANUAL_ITEM.match(raw_item)
        if not match:
            continue
        kind, start, end = match.groups()
        items.append((kind.upper() in ("G", "C"), start, end))
    return items


def _hhmm_on(day: date, hhmm: str, tz) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def classify(
    periods: list[PricePeriod],
    cheap_count: int,
    expensive_count: int,
    manual: str = "",
) -> list[PricePeriod]:
    """Flag the cheapest/most expensive periods, or the manually chosen ones.

    A non-empty manual string overrides the counts completely.
    """
    if not periods:
        return []
    cheap_starts: set[datetime] = set()
    expensive_starts: set[datetime] = set()
    manual_items = parse_manual_periods(manual)
    if manual_items:
        slot = periods[0].end - periods[0].start
        for is_cheap, start_text, end_text in manual_items:
            for period in periods:
                day = period.start.date()
                tz = period.start.tzinfo
                window_start = _hhmm_on(day, start_text, tz)
                if end_text:
                    window_end = _hhmm_on(day, end_text, tz)
                    if window_end <= window_start:
                        window_end += timedelta(days=1)
                else:
                    window_end = window_start + slot
                if period.start < window_end and period.end > window_start:
                    (cheap_starts if is_cheap else expensive_starts).add(period.start)
    else:
        ordered = sorted(periods, key=lambda p: p.value)
        if cheap_count > 0:
            cheap_starts = {p.start for p in ordered[:cheap_count]}
        if expensive_count > 0:
            expensive_starts = {p.start for p in ordered[-expensive_count:]}
    return [
        replace(p, cheap=p.start in cheap_starts, expensive=p.start in expensive_starts)
        for p in sorted(periods, key=lambda p: p.start)
    ]


def active_period(periods: list[PricePeriod], now: datetime) -> PricePeriod | None:
    return next((p for p in periods if p.contains(now)), None)


# ── Spread ──────────────────────────────────────────────────────────────────


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def spread(periods: list[PricePeriod], export_correction: float = 0.0) -> float:
    """Relative spread in % between the expensive and cheap groups.

    The export correction (what you lose per kWh when selling back) is
    taken off the expensive average, so the spread reflects what trading
    actually earns.
    """
    cheap_avg = _average([p.value for p in periods if p.cheap])
    expensive_avg = _average([p.value for p in periods if p.expensive])
    if cheap_avg is None or expensive_avg is None or abs(cheap_avg) < 1e-9:
        return 0.0
    return round((expensive_avg - export_correction - cheap_avg) / abs(cheap_avg) * 100, 1)


def spread_nom(periods: list[PricePeriod]) -> float:
    """Spread between the cheap periods and the expensive periods *after* the
    first cheap one - the energy bought in the night is only worth the
    prices it can displace later that day."""
    cheap = [p for p in periods if p.cheap]
    if not cheap:
        return 0.0
    first_cheap = min(p.start for p in cheap)
    later_expensive = [p for p in periods if p.expensive and p.start > first_cheap]
    cheap_avg = _average([p.value for p in cheap])
    expensive_avg = _average([p.value for p in later_expensive])
    if cheap_avg is None or expensive_avg is None or abs(cheap_avg) < 1e-9:
        return 0.0
    return round((expensive_avg - cheap_avg) / abs(cheap_avg) * 100, 1)


def format_periods(periods: list[PricePeriod], flag: str) -> list[str]:
    """Human readable list such as ``["02:00 - €0.0812", ...]``."""
    chosen = [p for p in periods if getattr(p, flag)]
    return [f"{p.start.strftime('%H:%M')} - €{p.value:.4f}" for p in chosen]
