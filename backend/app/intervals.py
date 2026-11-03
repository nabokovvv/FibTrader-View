"""Canonical chart intervals shared by all providers.

Timestamps everywhere are UTC milliseconds of the bar *open*.
"""
from __future__ import annotations

from dataclasses import dataclass

MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR


@dataclass(frozen=True)
class Interval:
    key: str          # canonical name used in the API/UI
    ms: int           # nominal duration (months use 30 days; only used for bucketing/estimates)
    span: int         # klinecharts period span
    type: str         # klinecharts period type
    base: str | None  # interval to resample from when a provider lacks this one natively


INTERVALS: dict[str, Interval] = {
    "1m": Interval("1m", MINUTE, 1, "minute", None),
    "3m": Interval("3m", 3 * MINUTE, 3, "minute", "1m"),
    "5m": Interval("5m", 5 * MINUTE, 5, "minute", "1m"),
    "15m": Interval("15m", 15 * MINUTE, 15, "minute", "1m"),
    "30m": Interval("30m", 30 * MINUTE, 30, "minute", "1m"),
    "1h": Interval("1h", HOUR, 1, "hour", None),
    "2h": Interval("2h", 2 * HOUR, 2, "hour", "1h"),
    "4h": Interval("4h", 4 * HOUR, 4, "hour", "1h"),
    "1D": Interval("1D", DAY, 1, "day", None),
    "1W": Interval("1W", 7 * DAY, 1, "week", "1D"),
    "1M": Interval("1M", 30 * DAY, 1, "month", "1D"),
}

ORDER = list(INTERVALS)
INTRADAY = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"}


def get(key: str) -> Interval:
    try:
        return INTERVALS[key]
    except KeyError:
        raise ValueError(f"unknown interval '{key}'") from None


def floor_ts(ts_ms: int, key: str) -> int:
    """Start of the bucket containing ts_ms for intraday/daily intervals (UTC-aligned)."""
    ms = INTERVALS[key].ms
    return ts_ms - ts_ms % ms


def is_calendar(key: str) -> bool:
    return key in ("1W", "1M")
