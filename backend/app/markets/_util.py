"""Small helpers shared by providers."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_TZ: dict[str, ZoneInfo] = {}


def tz(name: str) -> ZoneInfo:
    z = _TZ.get(name)
    if z is None:
        z = _TZ[name] = ZoneInfo(name)
    return z


def utc_midnight_of_local_date(ts_ms: int, tz_name: str) -> int:
    """Map a bar open time to 00:00 UTC of its calendar date in the exchange timezone.
    Used for D/W/M bars so that dates render correctly in any display timezone."""
    d = datetime.fromtimestamp(ts_ms / 1000, tz=tz(tz_name)).date()
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def local_naive_to_ms(s: str, tz_name: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> int:
    dt = datetime.strptime(s, fmt).replace(tzinfo=tz(tz_name))
    return int(dt.timestamp() * 1000)


def ms_to_local_str(ts_ms: int, tz_name: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=tz(tz_name)).strftime(fmt)


def iso_to_ms(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def ms_to_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
