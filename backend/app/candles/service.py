"""Candle service: serves chart pages from the store, filling gaps from providers and resampling."""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections import OrderedDict
from dataclasses import dataclass
from contextvars import ContextVar
from pathlib import Path

from ..limits import Busy

from ..intervals import DAY, INTERVALS, INTRADAY
from ..markets.base import Bar, Provider, SymbolInfo
from .resample import bucket_start, resample
from .store import StoreRegistry

log = logging.getLogger("lv.candles")
_budget: ContextVar[int | None] = ContextVar("candle_fetch_budget", default=None)

PROBE_MS = {"intraday": 30 * DAY, "daily": 400 * DAY, "calendar": 3 * 365 * DAY}


@dataclass
class BarsPage:
    bars: list[Bar]
    more: bool            # older history exists
    interval: str
    native: str


def now_ms() -> int:
    return int(time.time() * 1000)


class CandleService:
    def __init__(self, providers: dict[str, Provider], stores: StoreRegistry, *, backfill_bars: int | None = None,
                 cache_bytes: int | None = None, min_free_disk: int = 0, candles_dir: Path | None = None,
                 tail_ttl_ms: int = 0):
        """`backfill_bars` caps how many bars one request may make us fetch (public instance); with a cap, a window
        that is far from the stored range starts a new covered range instead of bridging an unbounded gap.
        `cache_bytes` / `min_free_disk`: above / below them, fetched bars are served but no longer stored.
        `tail_ttl_ms`: reuse the freshest fetch of a series for that long (many viewers of the same chart)."""
        self.providers = providers
        self.stores = stores
        self.backfill_bars = backfill_bars
        self.cache_bytes = cache_bytes
        self.min_free_disk = min_free_disk
        self.candles_dir = candles_dir
        self.tail_ttl_ms = tail_ttl_ms
        self._locks: dict[str, asyncio.Lock] = {}
        self._tail: OrderedDict[str, tuple[int, int, list[Bar]]] = OrderedDict()   # key -> (fetched_at, last, bars)
        self._persist_checked = 0.0
        self._persist_ok = True
        self._persist_archive_ok = True
        self._touched: OrderedDict[str, float] = OrderedDict()  # series -> last time we recorded an access

    def can_persist(self, archive: bool = False) -> bool:
        """Cache: under its size cap and enough free disk. Archive (bars that cannot be fetched again later): only
        the free-disk check, and the archive files do not count against the cache cap."""
        if self.candles_dir is None:
            return True
        now = time.monotonic()
        if now - self._persist_checked >= 60:
            self._persist_checked = now
            try:
                free = shutil.disk_usage(self.candles_dir).free
                used = sum(f.stat().st_size for f in self.candles_dir.glob("*.sqlite*") if "-archive." not in f.name)
            except OSError:
                return self._persist_archive_ok if archive else self._persist_ok
            ok = free >= self.min_free_disk and (self.cache_bytes is None or used <= self.cache_bytes)
            if ok != self._persist_ok:
                log.warning("candle cache %s (used %.1f GB, free %.1f GB)", "writable again" if ok else "FULL: serving without storing",
                            used / 1024**3, free / 1024**3)
            self._persist_ok = ok
            self._persist_archive_ok = free >= min(self.min_free_disk, 1024**3)
        return self._persist_archive_ok if archive else self._persist_ok

    def store_for(self, provider: Provider, native: str):
        """Intervals a provider archives (Hyperliquid 1h / 4h / 1D) live in '<market>-archive.sqlite': the cache
        cleanup never touches that file, because those bars cannot be fetched again once they leave the window."""
        if native in getattr(provider, "archive_intervals", ()):
            return self.stores.get(f"{provider.market}-archive")
        return self.stores.get(provider.market)

    def native_for(self, provider: Provider, interval: str) -> str:
        if interval in provider.native_intervals:
            return interval
        base = INTERVALS[interval].base
        if base and base in provider.native_intervals:
            return base
        raise ValueError(f"{provider.market} cannot serve interval {interval}")

    async def get_bars(self, sym: SymbolInfo, interval: str, to_ms: int | None, limit: int) -> BarsPage:
        token = _budget.set(4 * self.backfill_bars if self.backfill_bars else None)  # bars one request may fetch
        try:
            return await self._get_bars(sym, interval, to_ms, limit)
        finally:
            _budget.reset(token)

    async def _touch(self, provider: Provider, symbol: str, native: str) -> None:
        """Remember that a chart used this series (at most every 6 h per series), for maintenance."""
        key = f"{provider.market}:{symbol}:{native}"
        now = time.monotonic()
        if now - self._touched.get(key, -1e9) < 6 * 3600:
            return
        self._touched.pop(key, None)
        self._touched[key] = now
        while len(self._touched) > 20_000:
            self._touched.popitem(last=False)
        await asyncio.to_thread(self.store_for(provider, native).touch, symbol, native)

    async def _get_bars(self, sym: SymbolInfo, interval: str, to_ms: int | None, limit: int) -> BarsPage:
        provider = self.providers[sym.market]
        native = self.native_for(provider, interval)
        await self._touch(provider, sym.symbol, native)
        now = now_ms()
        end = now if to_ms is None else min(to_ms - 1, now)
        span = self._span_for(sym, interval, native, limit)
        store = self.store_for(provider, native)

        # Widen the window until we have `limit` bars or the provider's history is exhausted
        # (weekends, holidays and thin instruments leave long stretches without bars).
        rows: list[Bar] = []
        cov = None
        big = self._big_span(native)
        spans = [span, span * 4, span * 16, big]
        for span_i in spans:
            if span_i > big:
                span_i = big
            start = bucket_start(end - span_i, interval)
            live = await self._ensure(provider, sym.symbol, native, start, end)
            rows = await asyncio.to_thread(store.read, sym.symbol, native, start, end)
            if live:  # fetched-not-stored bars (the live one, or everything when the cache is full): merge by time
                merged = {b[0]: b for b in rows}
                for b in live:
                    if start <= b[0] <= end:
                        merged[b[0]] = b
                rows = [merged[k] for k in sorted(merged)]
            if native != interval:
                rows = resample(rows, interval)
            cov = await asyncio.to_thread(store.coverage, sym.symbol, native)
            exhausted = cov is not None and cov.exhausted and start <= cov.first_ts
            if len(rows) >= limit or exhausted or span_i >= big:
                break
        if not rows:
            # nothing in the look-back window, but older stored bars (a market delisted long ago): show the newest ones
            first_ts, last_ts = await asyncio.to_thread(store.bounds, sym.symbol, native)
            if last_ts is not None and last_ts <= end:
                per = max(1, INTERVALS[interval].ms // INTERVALS[native].ms + 1)
                rows = await asyncio.to_thread(store.read, sym.symbol, native, None, end, min(limit * per, 50_000))
                if native != interval:
                    rows = resample(rows, interval)
                cov = await asyncio.to_thread(store.coverage, sym.symbol, native)
        if len(rows) > limit:
            rows = rows[-limit:]

        more = True
        if cov is not None and cov.exhausted:
            more = bool(rows) and rows[0][0] > cov.first_ts + INTERVALS[native].ms
        elif cov is None and not rows:
            more = False  # nothing stored and nothing to fetch (before the provider's history): stop scrolling
        return BarsPage(bars=rows, more=more, interval=interval, native=native)

    # ------------------------------------------------------------------ internals
    def _span_for(self, sym: SymbolInfo, interval: str, native: str, limit: int) -> int:
        iv = INTERVALS[interval]
        span = limit * iv.ms
        if interval in INTRADAY:
            if sym.session != "24x7":
                span *= 4          # nights/weekends carry no bars
            else:
                span = int(span * 1.05)
        elif interval == "1D":
            span = int(span * 1.5)  # weekends/holidays
        else:
            span = int(span * 1.1)
        return span

    @staticmethod
    def _big_span(native: str) -> int:
        """Widest single look-back we are willing to fetch in one page request."""
        if native in INTRADAY:
            return 35 * DAY if native == "1m" else 120 * DAY
        if native == "1D":
            return 6 * 365 * DAY
        return 40 * 365 * DAY

    def _lock(self, key: str) -> asyncio.Lock:
        lk = self._locks.get(key)
        if lk is None:
            lk = self._locks[key] = asyncio.Lock()
        return lk

    async def _ensure(self, provider: Provider, symbol: str, native: str, start: int, end: int) -> list[Bar]:
        """Make sure closed bars for [start, end] are stored. Returns unclosed (live) bars fetched on the way."""
        store = self.store_for(provider, native)
        archive = native in getattr(provider, "archive_intervals", ())
        now = now_ms()
        boundary = bucket_start(now, native)         # bars with ts >= boundary are not closed yet
        lag = getattr(provider, "persist_lag_ms", 0)  # e.g. Alpaca: don't persist bars younger than the SIP lag
        if lag:
            boundary = min(boundary, bucket_start(now - lag, native))
        if archive and self.can_persist(archive=True):  # its own rules: the provider window, coverage never narrowed
            cap = self.backfill_bars
            if cap is not None:
                start = max(start, end - cap * INTERVALS[native].ms)
            return await self._ensure_archive(provider, store, symbol, native, start, end, boundary)
        floor_ts = provider.history_floor(native)
        exhausted_by_floor = False
        if floor_ts is not None and start < floor_ts:
            start, exhausted_by_floor = floor_ts, True
        if end < start:
            return []
        fetch_end = min(end, now)
        live: list[Bar] = []
        ivms = INTERVALS[native].ms
        cap = self.backfill_bars
        if cap is not None:
            start = max(start, end - cap * ivms)
        if not self.can_persist(archive):  # cache full: fetch just the window and hand it back without storing
            return await self._fetch(provider, symbol, native, start, fetch_end)

        async with self._lock(f"{provider.market}:{symbol}:{native}"):
            cov = await asyncio.to_thread(store.coverage, symbol, native)
            if cov is not None and cap is not None and (start > cov.last_ts + ivms or end < cov.first_ts - ivms):
                cov = None  # far away from what we hold: start a new covered range, never bridge the gap
            if cov is None:
                bars = await self._fetch(provider, symbol, native, start, fetch_end)
                closed = [b for b in bars if b[0] < boundary]
                live = [b for b in bars if b[0] >= boundary]
                if closed:
                    await asyncio.to_thread(store.upsert, symbol, native, closed)
                    await asyncio.to_thread(store.set_coverage, symbol, native, start, closed[-1][0], exhausted_by_floor)
                return live

            first, last, exhausted = cov.first_ts, cov.last_ts, cov.exhausted
            if start < first and not exhausted:
                older = await self._fetch(provider, symbol, native, start, first - 1)
                older = [b for b in older if b[0] < boundary]
                if older:
                    await asyncio.to_thread(store.upsert, symbol, native, older)
                    first = start
                else:
                    # Probe further back once before declaring the history exhausted (long holidays etc.).
                    probe_len = PROBE_MS["intraday" if native in INTRADAY else "daily" if native == "1D" else "calendar"]
                    pstart = start - probe_len
                    if cap is not None:
                        pstart = max(pstart, start - cap * ivms)
                    if floor_ts is not None and pstart < floor_ts:
                        pstart = floor_ts
                    probe = await self._fetch(provider, symbol, native, pstart, start - 1) if pstart < start else []
                    probe = [b for b in probe if b[0] < boundary]
                    if probe:
                        await asyncio.to_thread(store.upsert, symbol, native, probe)
                        first = pstart
                    else:
                        first, exhausted = start, True
                exhausted = exhausted or exhausted_by_floor
            if fetch_end > last:
                cached = self._tail_get(provider.market, symbol, native, last, fetch_end)
                newer = cached if cached is not None else await self._fetch(provider, symbol, native, last, fetch_end)
                closed = [b for b in newer if b[0] < boundary]
                live = [b for b in newer if b[0] >= boundary]
                if closed and cached is None:
                    await asyncio.to_thread(store.upsert, symbol, native, closed)
                    last = max(last, closed[-1][0])
                if cached is None and fetch_end >= boundary:  # only a fetch that reached the live bucket
                    self._tail_put(provider.market, symbol, native, last, live)
            if (first, last, exhausted) != (cov.first_ts, cov.last_ts, cov.exhausted):
                await asyncio.to_thread(store.set_coverage, symbol, native, first, last, exhausted)
        return live

    async def _ensure_archive(self, provider: Provider, store, symbol: str, native: str, start: int, end: int,
                              boundary: int) -> list[Bar]:
        """Archived intervals (bars that leave the provider's window for good, e.g. Hyperliquid 1h / 4h / 1D).

        Coverage here is "everything stored" and a chart request never narrows or resets it (older bars that only the
        archive still has must stay reachable). Only the provider's current window can be fetched; "exhausted" means
        we hold everything back to where that window started — the archive job relies on that to skip its bootstrap.
        """
        now = now_ms()
        oldest = provider.window_start(native)       # earliest bar the provider still serves
        fetch_end = min(end, now)
        live: list[Bar] = []

        async def take(lo: int, hi: int) -> list[Bar]:
            bars = await self._fetch(provider, symbol, native, lo, hi)
            closed = [b for b in bars if b[0] < boundary]
            if closed:
                await asyncio.to_thread(store.upsert, symbol, native, closed)
            return bars

        async with self._lock(f"{provider.market}:{symbol}:{native}"):
            cov = await asyncio.to_thread(store.coverage, symbol, native)
            exhausted = cov.exhausted if cov is not None else False
            if cov is None:
                lo = max(start, oldest)
                if fetch_end < lo:
                    return []
                bars = await take(lo, fetch_end)
                live = [b for b in bars if b[0] >= boundary]
                exhausted = lo <= oldest
            else:
                if start < cov.first_ts and not exhausted:
                    lo = max(start, oldest)
                    got = await take(lo, cov.first_ts - 1) if lo < cov.first_ts else []
                    if lo <= oldest:
                        exhausted = True          # fetched from where the provider's window starts
                    elif not got:                 # nothing there (before the listing?): look at the rest of the window once
                        await take(oldest, lo - 1)
                        exhausted = True
                if fetch_end > cov.last_ts:
                    cached = self._tail_get(provider.market, symbol, native, cov.last_ts, fetch_end)
                    newer = cached if cached is not None else await take(max(cov.last_ts, oldest), fetch_end)
                    live = [b for b in newer if b[0] >= boundary]
                    if cached is None and fetch_end >= boundary:
                        last_closed = max([cov.last_ts] + [b[0] for b in newer if b[0] < boundary])
                        self._tail_put(provider.market, symbol, native, last_closed, live)
            first, last = await asyncio.to_thread(store.bounds, symbol, native)
            if first is not None and (cov is None or (first, last, exhausted) != (cov.first_ts, cov.last_ts, cov.exhausted)):
                await asyncio.to_thread(store.set_coverage, symbol, native, first, last, exhausted)
        return live

    def _tail_get(self, market: str, symbol: str, native: str, last: int, fetch_end: int) -> list[Bar] | None:
        if not self.tail_ttl_ms:
            return None
        hit = self._tail.get(f"{market}:{symbol}:{native}")
        if hit is None:
            return None
        fetched_at, hit_last, bars = hit
        now = now_ms()
        # same starting point, still fresh, and the live bucket has not rolled over since
        if hit_last == last and now - fetched_at < self.tail_ttl_ms and bucket_start(now, native) == bucket_start(fetched_at, native):
            return [b for b in bars if b[0] <= fetch_end]
        return None

    def _tail_put(self, market: str, symbol: str, native: str, last: int, bars: list[Bar]) -> None:
        if not self.tail_ttl_ms:
            return
        key = f"{market}:{symbol}:{native}"
        self._tail.pop(key, None)
        self._tail[key] = (now_ms(), last, bars)
        while len(self._tail) > 4000:
            self._tail.popitem(last=False)

    async def _fetch(self, provider: Provider, symbol: str, native: str, start: int, end: int) -> list[Bar]:
        left = _budget.get()
        if left is not None:
            want = max(1, (end - start) // INTERVALS[native].ms + 1)
            if want > left:
                raise Busy("history request too large, try again")
            _budget.set(left - want)
        t0 = time.perf_counter()
        bars = await provider.fetch_bars(symbol, native, start, end)
        log.info("fetch %s:%s %s [%s..%s] -> %d bars in %.2fs", provider.market, symbol, native,
                 start, end, len(bars), time.perf_counter() - t0)
        return bars

    async def latest(self, sym: SymbolInfo, interval: str, n: int = 2) -> list[Bar]:
        """Most recent `n` bars incl. the live one (used by pollers)."""
        page = await self.get_bars(sym, interval, None, n)
        return page.bars
