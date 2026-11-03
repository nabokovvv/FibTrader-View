"""Derivatives data for the crypto panels: funding rate, open interest and liquidations (Bybit perpetuals only).

* Funding and open interest come from Bybit REST in fixed pages (200 points each). Closed pages are kept in
  ``derivs.sqlite`` and never fetched again; the current page lives in memory for a minute. One API request may
  fetch only a few pages (``derivs_pages``) inside the shared Bybit budget; the chart asks again for older data.
* Liquidations have no history on Bybit (WS only), so a recorder listens to ``allLiquidation`` for the top coins
  by turnover, sums them per coin and minute in memory and writes one row per coin-minute once a minute: the
  database load is the same in a calm market and in a liquidation cascade. Rows older than 30 days are deleted.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from . import limits
from .intervals import DAY, INTERVALS, MINUTE

log = logging.getLogger("lv.derivs")

PAGE = 200
OI_INTERVAL = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "2h": "1h", "4h": "4h",
               "1D": "1d", "1W": "1d", "1M": "1d"}
OI_STEP = {"5min": 5 * MINUTE, "15min": 15 * MINUTE, "30min": 30 * MINUTE, "1h": 60 * MINUTE, "4h": 240 * MINUTE, "1d": DAY}
FUNDING_STEP = 8 * 60 * MINUTE   # page span = 200 x 8 h; shorter funding intervals take extra requests per page
LIQ_TOP = 50
LIQ_KEEP_DAYS = 30
WS_URL = "wss://stream.bybit.com/v5/public/linear"


class DerivStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript("""
CREATE TABLE IF NOT EXISTS pages(kind TEXT NOT NULL, symbol TEXT NOT NULL, iv TEXT NOT NULL, start INTEGER NOT NULL,
  data TEXT NOT NULL, accessed INTEGER NOT NULL, PRIMARY KEY(kind, symbol, iv, start)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS liq(symbol TEXT NOT NULL, ts INTEGER NOT NULL, long_usd REAL NOT NULL,
  short_usd REAL NOT NULL, PRIMARY KEY(symbol, ts)) WITHOUT ROWID;""")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def page(self, kind: str, symbol: str, iv: str, start: int) -> list | None:
        with self._lock:
            r = self._conn.execute("SELECT data FROM pages WHERE kind=? AND symbol=? AND iv=? AND start=?",
                                   (kind, symbol, iv, start)).fetchone()
            if r is None:
                return None
            self._conn.execute("UPDATE pages SET accessed=? WHERE kind=? AND symbol=? AND iv=? AND start=?",
                               (int(time.time()), kind, symbol, iv, start))
        return json.loads(r[0])

    def put_page(self, kind: str, symbol: str, iv: str, start: int, points: list) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO pages(kind, symbol, iv, start, data, accessed) VALUES(?,?,?,?,?,?)",
                               (kind, symbol, iv, start, json.dumps(points, separators=(",", ":")), int(time.time())))

    def add_liq(self, rows: list[tuple[str, int, float, float]]) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(
                    "INSERT INTO liq(symbol, ts, long_usd, short_usd) VALUES(?,?,?,?) ON CONFLICT(symbol, ts) DO UPDATE "
                    "SET long_usd=long_usd+excluded.long_usd, short_usd=short_usd+excluded.short_usd", rows)
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def liq(self, symbol: str, start: int, end: int, bucket: int) -> list[list[float]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT (ts / ?) * ? AS b, SUM(long_usd), SUM(short_usd) FROM liq WHERE symbol=? AND ts>=? AND ts<? "
                "GROUP BY b ORDER BY b", (bucket, bucket, symbol, start, end)).fetchall()
        return [[r[0], round(r[1], 2), round(r[2], 2)] for r in rows]

    def prune(self, unused_days: int = 60) -> dict:
        """Liquidations older than 30 days; cached pages nobody opened for `unused_days`."""
        now = time.time()
        with self._lock:
            liq = self._conn.execute("DELETE FROM liq WHERE ts<?", (int((now - LIQ_KEEP_DAYS * 86400) * 1000),)).rowcount
            pages = self._conn.execute("DELETE FROM pages WHERE accessed<?", (int(now - unused_days * 86400),)).rowcount
        return {"liq_rows": liq, "pages": pages}


class Derivs:
    def __init__(self, store: DerivStore, provider):
        self.store, self.provider = store, provider
        self._hot: OrderedDict[tuple, tuple[float, list]] = OrderedDict()  # open (current) pages, 60 s
        self._sem = asyncio.Semaphore(2)
        self.recorded: set[str] = set()   # symbols the liquidation recorder listens to

    # --------------------------------------------------------- paged series
    async def series(self, kind: str, symbol: str, iv: str, start: int, end: int, max_pages: int,
                     max_read: int = 12) -> dict:
        """Newest pages first; at most `max_pages` fetched from Bybit and `max_read` pages in total (cached or not),
        so one answer stays small (~2400 points); `covered_from` is the cursor for the next, older request."""
        step = OI_STEP[iv] if kind == "oi" else FUNDING_STEP
        span = PAGE * step
        now = int(time.time() * 1000)
        end = min(end, now + step)
        first = start // span * span
        pages = list(range(end // span * span, first - 1, -span))  # newest first
        out: list = []
        covered, fetched, exhausted = end, 0, False
        for p0 in pages[:max_read]:
            closed = p0 + span <= now - step
            pts = await asyncio.to_thread(self.store.page, kind, symbol, iv, p0) if closed else self._hot_get((kind, symbol, iv, p0))
            if pts is None:
                if fetched >= max_pages:
                    break
                async with self._sem:
                    pts = await self._fetch(kind, symbol, iv, p0, p0 + span - 1)
                fetched += 1
                if closed:
                    await asyncio.to_thread(self.store.put_page, kind, symbol, iv, p0, pts)
                else:
                    self._hot_put((kind, symbol, iv, p0), pts)
            out[:0] = pts
            covered = p0
            if closed and not pts and p0 < now - 30 * DAY:  # before the listing: nothing older exists
                exhausted = True
                break
        out = [x for x in out if start <= x[0] < end]
        return {"points": out, "covered_from": max(covered, start), "exhausted": exhausted}

    def _hot_get(self, key: tuple) -> list | None:
        v = self._hot.get(key)
        if v is None or time.monotonic() - v[0] > 60:
            return None
        return v[1]

    def _hot_put(self, key: tuple, pts: list) -> None:
        self._hot[key] = (time.monotonic(), pts)
        self._hot.move_to_end(key)
        while len(self._hot) > 500:
            self._hot.popitem(last=False)

    async def _fetch(self, kind: str, symbol: str, iv: str, start: int, end: int) -> list:
        points: dict[int, float] = {}
        cur_end = end
        for _ in range(8):  # funding every 1 h needs 8 requests for one 1600 h page; 8 h funding needs one
            if kind == "oi":
                res = await self.provider._get("/v5/market/open-interest", {
                    "category": "linear", "symbol": symbol, "intervalTime": iv, "startTime": start, "endTime": cur_end, "limit": PAGE})
                rows = [(int(r["timestamp"]), float(r["openInterest"])) for r in res.get("list", [])]
            else:
                res = await self.provider._get("/v5/market/funding/history", {
                    "category": "linear", "symbol": symbol, "startTime": start, "endTime": cur_end, "limit": PAGE})
                rows = [(int(r["fundingRateTimestamp"]), float(r["fundingRate"])) for r in res.get("list", [])]
            for ts, v in rows:
                if start <= ts <= end:
                    points[ts] = v
            if len(rows) < PAGE:
                break
            oldest = min(ts for ts, _ in rows)
            if oldest <= start:
                break
            cur_end = oldest - 1
        return [[ts, points[ts]] for ts in sorted(points)]


# ------------------------------------------------------------------ liquidations
class LiqRecorder:
    def __init__(self, derivs: Derivs, top_n: int = LIQ_TOP):
        self.derivs, self.top_n = derivs, top_n
        self._acc: dict[tuple[str, int], list[float]] = {}
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._listen(), name="liq-ws"), asyncio.create_task(self._flusher(), name="liq-flush")]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except BaseException:  # noqa: BLE001
                pass
        await self.flush()

    def on_message(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not str(msg.get("topic", "")).startswith("allLiquidation."):
            return
        for d in msg.get("data") or []:
            try:
                sym, side, ts = d["s"], d["S"], int(d["T"])
                usd = float(d["v"]) * float(d["p"])
            except (KeyError, TypeError, ValueError):
                continue
            if sym not in self.derivs.recorded:
                continue
            slot = self._acc.setdefault((sym, ts // MINUTE * MINUTE), [0.0, 0.0])
            slot[0 if side == "Buy" else 1] += usd  # "Buy" = a long position was liquidated (Bybit docs)

    async def flush(self) -> None:
        if not self._acc:
            return
        acc, self._acc = self._acc, {}
        rows = [(s, ts, v[0], v[1]) for (s, ts), v in acc.items()]
        try:
            await asyncio.to_thread(self.derivs.store.add_liq, rows)
        except Exception:  # noqa: BLE001
            log.exception("liquidations flush failed (%d rows)", len(rows))

    async def _flusher(self) -> None:
        while True:
            await asyncio.sleep(60)
            await self.flush()

    async def _listen(self) -> None:
        import websockets
        backoff = 2.0
        while True:
            try:
                symbols = await self.derivs.provider.top_by_turnover(self.top_n)
                self.derivs.recorded = set(symbols)
                async with websockets.connect(WS_URL, ping_interval=20, max_size=2**20, open_timeout=15) as ws:
                    for i in range(0, len(symbols), 10):
                        await ws.send(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{s}" for s in symbols[i:i + 10]]}))
                    backoff = 2.0
                    started = time.monotonic()
                    async for raw in ws:
                        self.on_message(raw)
                        if time.monotonic() - started > 6 * 3600:
                            break  # refresh the top list
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.info("liquidations stream: %s (retry in %.0fs)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)


# ------------------------------------------------------------------------- API
router = APIRouter()
_rate = limits.RateLimiter(2, 20)
_per_ip = limits.KeyedGate(2)      # requests in flight per IP
_running: asyncio.Semaphore | None = None  # requests in flight in total (memory: each answer ≤ ~2400 points)


async def _admit(ip: str):
    global _running
    if _running is None:
        _running = asyncio.Semaphore(4)
    if not _per_ip.enter(ip):
        raise HTTPException(429, "too many requests at once")
    try:
        await asyncio.wait_for(_running.acquire(), timeout=10)
    except TimeoutError as exc:
        _per_ip.leave(ip)
        raise HTTPException(503, "server is busy, try again in a moment") from exc


def _release(ip: str) -> None:
    _per_ip.leave(ip)
    if _running is not None:
        _running.release()


def _derivs(request: Request, id: str) -> tuple[Derivs, str]:
    d: Derivs | None = getattr(request.app.state, "derivs", None)
    market, _, sym = id.partition(":")
    if d is None or market != "bybit" or not sym:
        raise HTTPException(400, "available for Bybit perpetuals only")
    ip = request.scope.get("state", {}).get("client_ip", "unknown")
    if not _rate.allow(ip):
        raise HTTPException(429, "too many requests, slow down")
    return d, sym


def _range(start: int, end: int) -> None:
    if not (0 <= start < end <= int(time.time() * 1000) + 2 * DAY):
        raise HTTPException(400, "bad time range")


@router.get("/derivs/{kind}")
async def derivs(request: Request, kind: str, id: str = Query(..., max_length=64), interval: str = Query("1h", max_length=4),
                 start: int = Query(..., alias="from"), end: int = Query(..., alias="to")) -> dict:
    if interval not in INTERVALS:
        raise HTTPException(400, "unknown interval")
    _range(start, end)
    d, sym = _derivs(request, id)
    if kind not in ("oi", "funding", "liq"):
        raise HTTPException(404)
    ip = request.scope.get("state", {}).get("client_ip", "unknown")
    await _admit(ip)
    try:
        return await _answer(d, kind, sym, interval, start, end)
    finally:
        _release(ip)


async def _answer(d: Derivs, kind: str, sym: str, interval: str, start: int, end: int) -> dict:
    if kind == "liq":
        span = INTERVALS[interval].ms
        bucket = span if span <= DAY else DAY  # weeks / months: daily buckets, the chart sums them into bars
        start = max(start, end - 5000 * bucket)
        pts = await asyncio.to_thread(d.store.liq, sym, start, end, bucket)
        # nothing older than the retention window exists; otherwise the chart may ask again further back
        oldest_kept = int(time.time() * 1000) - LIQ_KEEP_DAYS * DAY
        return {"points": pts, "covered_from": start, "exhausted": start <= oldest_kept, "recorded": sym in d.recorded}
    iv = "funding" if kind == "funding" else OI_INTERVAL.get(interval)
    if iv is None:
        return {"points": [], "covered_from": start, "exhausted": True, "unavailable": "interval"}
    try:
        return await d.series(kind, sym, iv, start, end, limits.profile().derivs_pages)
    except limits.Busy as exc:
        raise HTTPException(503, "data source is busy, try again in a moment") from exc
    except Exception as exc:  # noqa: BLE001
        log.info("derivs %s %s %s: %s", kind, sym, iv, exc)
        raise HTTPException(502, "data source error") from exc
