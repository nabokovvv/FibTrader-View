"""Hyperliquid candle archive: keeps 1h, 4h and 1D bars of every live Hyperliquid market for good, and 1m … 30m
of the HIP-3 markets (stocks, indices, commodities, FX; ~8 GB a year) in ``hl-minutes-archive.sqlite``, which the
nightly backup skips (crypto minutes have their full history on Bybit).

Hyperliquid serves only the newest 5000 bars of an interval (1h ≈ 208 days, 4h ≈ 2.3 years), so history older than
that exists only if somebody stored it in time. Every 6 hours this job walks all live markets (stocks, indices and
commodities first, then crypto) and, per interval, either fills the whole window once or adds the bars since the
last stored one. Series a chart refreshed recently are skipped. It uses the background Hyperliquid budget, so charts
are not slowed down; the first full pass takes a few hours, later passes minutes.

The bars go to ``data/candles/hl-archive.sqlite`` (see CandleService.store_for), which the cache cleanup never
touches and the nightly R2 backup uploads. Delisted markets keep their bars.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .candles.resample import bucket_start
from .intervals import HOUR, INTERVALS
from .markets.hyperliquid import WINDOW

log = logging.getLogger("lv.hl_archive")

CYCLE_S = 6 * 3600
FRESH_MS = 18 * HOUR      # a series whose last stored bar ended less than this ago is left alone


class HLArchiver:
    def __init__(self, provider, candles):
        self.provider, self.candles = provider, candles
        self._task: asyncio.Task | None = None
        self.last_cycle: dict = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="hl-archive")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:  # noqa: BLE001
                pass

    async def _run(self) -> None:
        await asyncio.sleep(60)  # let the app settle (symbol lists, first chart requests)
        while True:
            t0 = time.monotonic()
            try:
                self.last_cycle = await self.cycle()
                log.info("archive pass: %s in %.0f min", self.last_cycle, (time.monotonic() - t0) / 60)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("archive pass failed")
            await asyncio.sleep(max(600, CYCLE_S - (time.monotonic() - t0)))

    async def cycle(self) -> dict:
        await self.provider.symbols()
        stats = {"series": 0, "bars": 0, "skipped": 0, "failed": 0}
        for coin in self.provider.archive_coins():
            for iv in self.provider.archived(coin):
                try:
                    n = await self.update(coin, iv)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    stats["failed"] += 1
                    log.info("archive %s %s: %s", coin, iv, exc)
                    continue
                if n is None:
                    stats["skipped"] += 1
                else:
                    stats["series"] += 1
                    stats["bars"] += n
        return stats

    async def update(self, coin: str, iv: str, now: int | None = None) -> int | None:
        """Bars stored for one series, or None when it was fresh already."""
        candles, p = self.candles, self.provider
        store = candles.store_for(p, iv, coin)
        ivms = INTERVALS[iv].ms
        now = now or int(time.time() * 1000)
        boundary = bucket_start(now, iv)               # bars at or after this are still forming
        if not candles.can_persist(archive=True):
            log.warning("archive paused: the disk is almost full")
            return None
        cov = await asyncio.to_thread(store.coverage, coin, iv)
        seeded = cov is not None and cov.exhausted
        if seeded and now - (cov.last_ts + ivms) < FRESH_MS:
            return None
        # not seeded yet: the whole window (everything Hyperliquid still has); else from the last stored bar on.
        # Fetched outside the series lock: waiting for the background budget must not hold up a chart.
        start = cov.last_ts + ivms if seeded else 1
        bars = await p.fetch_candles(coin, iv, start, now, background=True)
        closed = [b for b in bars if b[0] < boundary]
        if not closed:
            return 0
        if seeded and closed[0][0] > cov.last_ts + ivms and len(bars) >= WINDOW - 10:
            log.warning("archive %s %s: bars between %d and %d are gone (server was off too long)",
                        coin, iv, cov.last_ts, closed[0][0])
        async with candles._lock(f"{p.market}:{coin}:{iv}"):  # the same lock chart requests take for this series
            await asyncio.to_thread(store.upsert, coin, iv, closed)
            # coverage = everything stored (a chart request may have narrowed it meanwhile); "exhausted": nothing
            # older can be fetched any more — true once the full window is in
            first, last = await asyncio.to_thread(store.bounds, coin, iv)
            await asyncio.to_thread(store.set_coverage, coin, iv, first, last, True)
        return len(closed)
