"""Aggregate bars into coarser intervals. Buckets are UTC-aligned (clock-aligned) like most charting apps."""
from __future__ import annotations

from datetime import datetime, timezone

from ..intervals import INTERVALS
from ..markets.base import Bar


def bucket_start(ts_ms: int, key: str) -> int:
    """Open time of the `key` bucket containing ts_ms."""
    if key == "1W":
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(midnight.timestamp() * 1000) - midnight.weekday() * 86_400_000
    if key == "1M":
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        first = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(first.timestamp() * 1000)
    ms = INTERVALS[key].ms
    return ts_ms - ts_ms % ms


def resample(bars: list[Bar], dst_key: str) -> list[Bar]:
    """Aggregate ascending bars into `dst_key` buckets (open=first, high=max, low=min, close=last, vol=sum)."""
    out: list[Bar] = []
    cur: Bar | None = None
    for b in bars:
        bk = bucket_start(int(b[0]), dst_key)
        if cur is None or cur[0] != bk:
            if cur is not None:
                out.append(cur)
            cur = [bk, b[1], b[2], b[3], b[4], b[5]]
        else:
            if b[2] > cur[2]:
                cur[2] = b[2]
            if b[3] < cur[3]:
                cur[3] = b[3]
            cur[4] = b[4]
            cur[5] += b[5]
    if cur is not None:
        out.append(cur)
    return out
