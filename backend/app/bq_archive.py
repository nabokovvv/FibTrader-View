"""biquote candle archive: keeps every interval (1m … 1D) of the 70 biquote instruments for good.

biquote serves only a short window of the newest bars (1m ≈ 5 h, 5m ≈ 1 day, 1h ≈ 9 days, 1D ≈ 500 bars) and
cannot be paged back, so history exists only if we store it in time. Every hour this job asks, per instrument and
interval, for the bars since the last stored one (the whole window on the first run) and stores the closed ones.
~490 small requests a pass inside the provider's background budget (2 a second): about 4 minutes.

1h / 4h / 1D go to ``data/candles/bq-archive.sqlite`` (nightly R2 backup), 1m … 30m to
``bq-minutes-archive.sqlite`` (~1.6 GB a year, not backed up); the cache cleanup touches neither. A gap appears only
if the server is off longer than an interval's window.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .candles.resample import bucket_start

log = logging.getLogger("lv.bq_archive")

CYCLE_S = 3600          # 1m leaves biquote's window after ~5 h: a missed pass or a restart must not leave a gap


class BQArchiver:
    def __init__(self, provider, candles):
        self.provider, self.candles = provider, candles
        self._task: asyncio.Task | None = None
        self.last_cycle: dict = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="bq-archive")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:  # noqa: BLE001
                pass

    async def _run(self) -> None:
        await asyncio.sleep(45)  # let the app settle
        while True:
            t0 = time.monotonic()
            try:
                self.last_cycle = await self.cycle()
                log.info("biquote archive pass: %s in %.1f min", self.last_cycle, (time.monotonic() - t0) / 60)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("biquote archive pass failed")
            await asyncio.sleep(max(300, CYCLE_S - (time.monotonic() - t0)))

    async def cycle(self) -> dict:
        stats = {"series": 0, "bars": 0, "failed": 0}
        for info in await self.provider.symbols():
            for iv in self.provider.archived(info.symbol):  # short windows first
                try:
                    n = await self.update(info.symbol, iv)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    stats["failed"] += 1
                    log.info("biquote archive %s %s: %s", info.symbol, iv, exc)
                    continue
                if n is not None:
                    stats["series"] += 1
                    stats["bars"] += n
        return stats

    async def update(self, symbol: str, iv: str, now: int | None = None) -> int | None:
        """Bars stored for one series (None: the disk is almost full)."""
        candles, p = self.candles, self.provider
        if not candles.can_persist(archive=True):
            log.warning("biquote archive paused: the disk is almost full")
            return None
        store = candles.store_for(p, iv, symbol)
        now = now or int(time.time() * 1000)
        boundary = bucket_start(now, iv)               # bars at or after this are still forming
        cov = await asyncio.to_thread(store.coverage, symbol, iv)
        # from the last stored bar on (it is fetched again: harmless); everything biquote has on the first run
        start = cov.last_ts if cov is not None and cov.exhausted else 1
        bars = await p.fetch_candles(symbol, iv, start, now, background=True)
        closed = [b for b in bars if b[0] < boundary]
        if not closed:
            return 0
        if start > 1 and closed[0][0] > cov.last_ts:   # the last stored bar has left biquote's window
            log.info("biquote archive %s %s: possible gap after %d (server was off longer than the window?)",
                     symbol, iv, cov.last_ts)
        async with candles._lock(f"{p.market}:{symbol}:{iv}"):  # the same lock chart requests take for this series
            await asyncio.to_thread(store.upsert, symbol, iv, closed)
            first, last = await asyncio.to_thread(store.bounds, symbol, iv)
            # "exhausted": nothing older can be fetched any more — true once biquote's whole window is in
            await asyncio.to_thread(store.set_coverage, symbol, iv, first, last, True)
        return len(closed)
