"""Self-maintenance of the candle cache (public instance, small disk).

* nightly retention: 1m bars older than 30 days, 3m–30m older than 180 days, and every series nobody has opened
  for 60 days are deleted (they are re-fetched from Bybit if someone asks again);
* emergency cleanup: when free disk drops under 3 GB, delete in stages until 5 GB are free again;
* idle prewarm: when no chart has been loaded for a while, slowly fill the history of the top Bybit coins,
  inside the shared Bybit budget, pausing as soon as someone uses the site.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time

from . import config
from .candles.store import StoreRegistry
from .intervals import DAY
from .limits import Busy

log = logging.getLogger("lv.maint")

RETAIN_DAYS = {"1m": 30, "3m": 180, "5m": 180, "15m": 180, "30m": 180}
UNUSED_DAYS = 60
EMERGENCY_FREE = 3 * 1024**3
TARGET_FREE = 5 * 1024**3


def free_bytes() -> int:
    return shutil.disk_usage(config.CANDLES_DIR).free


def _markets(stores: StoreRegistry, providers) -> list:
    return [stores.get(m) for m in providers]


def retention(stores: StoreRegistry, providers, now: float | None = None) -> dict:
    """Blocking (run in a thread)."""
    now = now or time.time()
    deleted, dropped = 0, 0
    for st in _markets(stores, providers):
        for symbol, interval, _first, _last, accessed in st.series():
            if accessed and now - accessed > UNUSED_DAYS * 86400:
                deleted += st.drop(symbol, interval)
                dropped += 1
                continue
            days = RETAIN_DAYS.get(interval)
            if days:
                deleted += st.prune(symbol, interval, int((now - days * 86400) * 1000))
        st.compact()
    return {"bars_deleted": deleted, "series_dropped": dropped}


def emergency(stores: StoreRegistry, providers) -> dict | None:
    """Blocking. Only acts below EMERGENCY_FREE; stops as soon as TARGET_FREE is reached."""
    if free_bytes() >= EMERGENCY_FREE:
        return None
    now = time.time()
    ms = lambda days: int((now - days * 86400) * 1000)  # noqa: E731
    stages = [
        ("1m older than 7 days", lambda st, s, iv, acc: iv == "1m" and st.prune(s, iv, ms(7))),
        ("all 1m and 3m", lambda st, s, iv, acc: iv in ("1m", "3m") and st.drop(s, iv)),
        ("5m-30m older than 30 days", lambda st, s, iv, acc: iv in ("5m", "15m", "30m") and st.prune(s, iv, ms(30))),
        ("series unused for 7 days", lambda st, s, iv, acc: acc and now - acc > 7 * 86400 and st.drop(s, iv)),
    ]
    done = []
    for name, act in stages:
        for st in _markets(stores, providers):
            for symbol, interval, _f, _l, accessed in st.series():
                act(st, symbol, interval, accessed)
            st.compact()
        done.append(name)
        if free_bytes() >= TARGET_FREE:
            break
    log.warning("EMERGENCY disk cleanup: %s -> %.1f GB free", "; ".join(done), free_bytes() / 1024**3)
    return {"stages": done, "free_gb": round(free_bytes() / 1024**3, 1)}


class Prewarm:
    """Fills history for the top coins while the site is idle. One small step per tick."""

    PLAN = [("1D", None), ("4h", 730), ("1h", 730), ("15m", 30)]  # interval, days of history (None = all)
    IDLE_S = 120

    def __init__(self, state, top_n: int = 50):
        self.state = state
        self.top_n = top_n
        self.queue: list[tuple[str, str, int | None]] = []   # (symbol id, interval, days)
        self.cursor: dict[tuple[str, str], int | None] = {}  # next `to` for scrolling back
        self.refreshed = 0.0

    def idle(self) -> bool:
        last = getattr(self.state, "last_user_bars", 0.0)
        return time.monotonic() - last > self.IDLE_S and len(self.state.hub.clients) < 10

    async def tick(self) -> None:
        if not self.idle() or not self.state.candles.can_persist():
            return
        if not self.queue:
            if time.monotonic() - self.refreshed < 6 * 3600:
                return
            self.refreshed = time.monotonic()
            bybit = self.state.providers.get("bybit")
            if bybit is None:
                return
            top = await bybit.top_by_turnover(self.top_n)
            self.queue = [(f"bybit:{s}", iv, days) for s in top for iv, days in self.PLAN]
            self.cursor.clear()
            log.info("prewarm: %d series queued", len(self.queue))
        sid, interval, days = self.queue[0]
        info = await self.state.symbols.get(sid)
        if info is None:
            self.queue.pop(0)
            return
        key = (sid, interval)
        try:
            page = await self.state.candles.get_bars(info, interval, self.cursor.get(key), 1000)
        except Busy:
            return  # budget in use: try again next tick
        except Exception as exc:  # noqa: BLE001
            log.info("prewarm %s %s: %s", sid, interval, exc)
            self.queue.pop(0)
            return
        oldest = page.bars[0][0] if page.bars else None
        horizon = None if days is None else (time.time() - days * 86400) * 1000
        if not page.more or oldest is None or (horizon is not None and oldest <= horizon) or oldest == self.cursor.get(key):
            self.queue.pop(0)
        else:
            self.cursor[key] = oldest
