"""Scheduled maintenance: Yahoo intraday accumulation, nightly user-data backup."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config
from .intervals import DAY

log = logging.getLogger("lv.jobs")
KEEP_BACKUPS = 14


class Jobs:
    def __init__(self, state) -> None:
        self.state = state
        self.sched = AsyncIOScheduler(timezone=config.DEFAULT_TIMEZONE)

    def start(self) -> None:
        self.sched.add_job(self.backup_userdata, CronTrigger(hour=3, minute=45), id="backup", replace_existing=True)
        self.sched.add_job(self.accumulate_yahoo, CronTrigger(hour=4, minute=0), id="yahoo-acc", replace_existing=True)
        self.sched.add_job(self.extra_upkeep, CronTrigger(hour=4, minute=40), id="extra-upkeep", replace_existing=True)
        if config.MAINTENANCE:  # candle-cache upkeep: on by default on the public instance only
            from apscheduler.triggers.interval import IntervalTrigger
            from .maintenance import Prewarm
            self.prewarm = Prewarm(self.state)
            self.sched.add_job(self.candle_retention, CronTrigger(hour=4, minute=30), id="retention", replace_existing=True)
            self.sched.add_job(self.disk_guard, IntervalTrigger(minutes=10), id="disk-guard", replace_existing=True,
                               max_instances=1, coalesce=True)
            self.sched.add_job(self.prewarm_tick, IntervalTrigger(seconds=5), id="prewarm", replace_existing=True,
                               max_instances=1, coalesce=True)
            self.sched.add_job(self.db_upkeep, CronTrigger(day_of_week="sun", hour=5, minute=0), id="db-upkeep",
                               replace_existing=True)
        self.sched.start()

    def stop(self) -> None:
        try:
            self.sched.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ jobs
    async def backup_userdata(self) -> None:
        dest = config.BACKUPS_DIR / f"app-{datetime.now().strftime('%Y%m%d')}.sqlite"
        await asyncio.to_thread(self.state.userdb.backup_to, dest)  # off the event loop
        files = sorted(config.BACKUPS_DIR.glob("app-*.sqlite"))
        for old in files[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)
        log.info("userdata backup -> %s", dest.name)

    async def extra_upkeep(self) -> None:
        """Expired share links / codes, old fired alerts, 30-day liquidations, unused funding / OI pages."""
        res = await asyncio.to_thread(self.state.userdb.prune_extra)
        derivs = getattr(self.state, "derivs", None)
        if derivs is not None:
            res.update(await asyncio.to_thread(derivs.store.prune))
        log.info("extra upkeep: %s", res)

    async def candle_retention(self) -> None:
        from .maintenance import retention
        res = await asyncio.to_thread(retention, self.state.stores, self.state.providers)
        log.info("candle retention: %s", res)

    async def disk_guard(self) -> None:
        from .maintenance import emergency
        res = await asyncio.to_thread(emergency, self.state.stores, self.state.providers)
        if res:
            log.warning("disk guard acted: %s", res)

    async def prewarm_tick(self) -> None:
        try:
            await self.prewarm.tick()
        except Exception as exc:  # noqa: BLE001
            log.info("prewarm tick failed: %s", exc)

    async def db_upkeep(self) -> None:
        """Weekly: planner statistics and WAL checkpoint for every database."""
        def run():
            for m, p in self.state.providers.items():
                self.state.stores.get(m).compact()
                if getattr(p, "archive_intervals", None):
                    self.state.stores.get(f"{m}-archive").compact()
            self.state.userdb.optimize()
        await asyncio.to_thread(run)
        log.info("weekly db upkeep done")

    async def accumulate_yahoo(self) -> None:
        """Keep intraday Yahoo history for watch-listed symbols (Yahoo only serves 1m for 30 days)."""
        yahoo = self.state.providers.get("yahoo")
        if yahoo is None:
            return
        ids = {i for i in self.state.userdb.all_watchlist_items() if i.startswith("yahoo:")}
        now = int(time.time() * 1000)
        n = 0
        for sid in sorted(ids):
            info = await self.state.symbols.get(sid)
            if info is None:
                continue
            for native, days in (("1m", 7), ("5m", 30), ("15m", 30), ("30m", 30), ("1h", 60)):
                try:
                    await self.state.candles._ensure(yahoo, info.symbol, native, now - days * DAY, now)
                    n += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("accumulate %s %s failed: %s", sid, native, exc)
        log.info("yahoo accumulation done: %d symbol/interval pairs", n)
