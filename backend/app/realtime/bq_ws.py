"""biquote.io live ticks over one SignalR WebSocket; chart bars of the biquote market are built from them.

Same interface as BybitStream / HLStream as far as the hub uses it (kline_topic, subscribe, unsubscribe, stop,
connected), plus ``want(symbols)`` for quotes and alerts: a symbol stays on the stream while a chart streams it or
for 90 s after a quote / alert check last asked for it. The connection exists only while something is needed.

SignalR (JSON protocol): POST /hubs/tick/negotiate -> connection token, then the WebSocket; every frame ends with
the record separator 0x1E. Client -> server: ``Subscribe`` / ``Unsubscribe`` with a list of symbols, a ping
(type 6) every 15 s. Server -> client: ``ReceiveTick`` (bid / ask / mid, timestamp), pings, close (type 7).

Bars: each streamed (symbol, interval) keeps its current bar. A tick updates high / low / close; a tick in a later
bucket closes the bar and opens the next one at the tick's price. The closed bar goes out once more in its final
REST version 2 s later, before the new bar is sent (charts update only their newest bar). The REST bar (open and
tick count included) is taken when the stream starts and again about once a minute, so the live bar matches the
chart history; when no tick came for 15 s the REST close wins. A connection silent for 45 s (the hub pings every
15 s) is dropped and opened again.
Updates go out at most twice a second per stream. The catalogue is fixed (70 symbols x 7 intervals), so the
number of streams is bounded whatever clients ask for.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable

import httpx
import websockets

from ..intervals import INTERVALS
from ..markets._util import iso_to_ms
from ..markets.base import Bar
from ..markets.biquote import CATALOG, day_bucket

log = logging.getLogger("lv.bq_ws")

HUB = "https://biquote.io/hubs/tick"
WS_HUB = "wss://biquote.io/hubs/tick"
RS = "\x1e"
WANT_TTL_S = 90          # a quote / alert symbol stays subscribed this long after it was last asked for
IDLE_CLOSE_S = 300       # nothing needed for this long: close the connection
SYNC_S = 1.0             # how often the subscription set is brought in line with what is needed
FLUSH_S = 0.5            # live bar updates per stream: at most one per FLUSH_S
RESEED_S = 60            # the REST bar of each stream is taken again about this often
PING_S = 15
READ_TIMEOUT_S = 45      # no message (ticks or the hub's 15 s pings) for this long: reconnect
FINAL_DELAY_S = 2.0      # after a bar closes, its final REST version is taken this much later
TICK_STALE_S = 15        # a stream whose last tick is older than this takes the close from REST
BarCallback = Callable[[str, str, Bar, bool], Awaitable[None]]


class BiquoteStream:
    market = "bq"

    def __init__(self, provider, on_bar: BarCallback | None = None, max_topics: int = 600):
        self.provider = provider
        self.on_bar = on_bar
        self.max_topics = max_topics
        self._topics: dict[str, int] = {}               # "interval|symbol" -> refcount
        self._bars: dict[str, Bar] = {}                 # topic -> current bar
        self._seeded: dict[str, float] = {}             # topic -> when its REST bar was last taken (monotonic)
        self._dirty: set[str] = set()
        self._tick_at: dict[str, float] = {}            # topic -> monotonic time of its last tick
        self._closing: dict[str, asyncio.Task] = {}     # topic -> task sending the final version of the closed bar
        self._wanted: dict[str, float] = {}             # symbol -> wanted until (monotonic)
        self._subscribed: set[str] = set()              # symbols subscribed on the current connection
        self._task: asyncio.Task | None = None
        self._aux: list[asyncio.Task] = []
        self.connected = False
        self.ticks_seen = 0

    # ------------------------------------------------------------ interface
    @staticmethod
    def kline_topic(symbol: str, interval: str) -> str:
        return f"{interval}|{symbol}"

    async def subscribe(self, topic: str, reserve: int = 0) -> bool:
        interval, _, symbol = topic.partition("|")
        if symbol not in CATALOG or interval not in INTERVALS:
            return False
        n = self._topics.get(topic, 0)
        if n == 0 and len(self._topics) >= self.max_topics - reserve:
            return False
        self._topics[topic] = n + 1
        if n == 0:
            self._seeded.pop(topic, None)     # take the REST bar on the next reseed pass (soon)
            self._ensure_running()
        return True

    async def unsubscribe(self, topic: str) -> None:
        n = self._topics.get(topic, 0)
        if n <= 1:
            self._topics.pop(topic, None)
            self._bars.pop(topic, None)
            self._seeded.pop(topic, None)
            self._tick_at.pop(topic, None)
            self._dirty.discard(topic)
            task = self._closing.pop(topic, None)
            if task is not None:
                task.cancel()
        else:
            self._topics[topic] = n - 1

    def want(self, symbols) -> None:
        until = time.monotonic() + WANT_TTL_S
        for s in symbols:
            if s in CATALOG:
                self._wanted[s] = until
        if self._wanted:
            self._ensure_running()

    async def stop(self) -> None:
        tasks = [self._task, *self._aux, *self._closing.values()]
        self._closing.clear()
        for t in tasks:
            if t:
                t.cancel()
        for t in tasks:
            if t:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._task, self._aux = None, []

    # ------------------------------------------------------------- internals
    def needed(self) -> set[str]:
        now = time.monotonic()
        for s in [s for s, until in self._wanted.items() if until < now]:
            del self._wanted[s]
        return {t.partition("|")[2] for t in self._topics} | set(self._wanted)

    def _ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="bq-ws")
        if not self._aux or any(t.done() for t in self._aux):
            for t in self._aux:
                t.cancel()
            self._aux = [asyncio.create_task(self._flush_loop(), name="bq-ws-flush"),
                         asyncio.create_task(self._reseed_loop(), name="bq-ws-reseed")]

    async def _negotiate(self) -> str:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "fibtrader-view"}) as c:
            r = await c.post(f"{HUB}/negotiate", params={"negotiateVersion": 1})
            r.raise_for_status()
            token = r.json().get("connectionToken")
        if not isinstance(token, str) or not token or len(token) > 200:
            raise RuntimeError("biquote negotiate: no connection token")
        return token

    async def _run(self) -> None:
        backoff = 2.0
        while True:
            if not self.needed():
                return
            try:
                token = await self._negotiate()
                async with websockets.connect(f"{WS_HUB}?id={token}", ping_interval=None, max_size=1024 * 1024,
                                              open_timeout=15, close_timeout=5) as ws:
                    await ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
                    hello = await asyncio.wait_for(ws.recv(), 15)
                    if json.loads(str(hello).split(RS)[0] or "{}").get("error"):
                        raise RuntimeError(f"biquote handshake refused: {str(hello)[:120]}")
                    self.connected, self._subscribed = True, set()
                    backoff = 2.0
                    log.info("biquote ws connected")
                    sync = asyncio.create_task(self._sync_loop(ws))
                    try:
                        while not sync.done():
                            try:
                                # the hub pings every 15 s: silence means a dead connection (no FIN ever came)
                                raw = await asyncio.wait_for(ws.recv(), READ_TIMEOUT_S)
                            except websockets.ConnectionClosed:
                                break
                            except TimeoutError:
                                raise RuntimeError(f"nothing from biquote for {READ_TIMEOUT_S} s") from None
                            if self.handle(raw) == "close":
                                break
                    finally:
                        if not sync.done():
                            sync.cancel()
                        try:
                            await sync
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:  # noqa: BLE001 — a failed send: the reconnect below handles it
                            log.info("biquote ws send failed: %s", exc)
                    if sync.done() and not sync.cancelled() and sync.exception() is None and not self.needed():
                        return        # nothing needed any more: closed on purpose
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("biquote ws error: %s (reconnect in %.0fs)", exc, backoff)
            finally:
                self.connected, self._subscribed = False, set()
            await asyncio.sleep(backoff * (0.75 + random.random() / 2))
            backoff = min(backoff * 2, 60)

    async def _sync_loop(self, ws) -> None:
        """Keeps the subscriptions in line with what is needed and pings; returns when idle long enough."""
        last_ping = time.monotonic()
        idle_since: float | None = None
        while True:
            need = self.needed()
            now = time.monotonic()
            if not need:
                idle_since = idle_since or now
                if now - idle_since >= IDLE_CLOSE_S:
                    await ws.close()
                    return
            else:
                idle_since = None
            add, drop = sorted(need - self._subscribed), sorted(self._subscribed - need)
            if add:
                await ws.send(json.dumps({"type": 1, "target": "Subscribe", "arguments": [add]}) + RS)
                self._subscribed |= set(add)
            if drop:
                await ws.send(json.dumps({"type": 1, "target": "Unsubscribe", "arguments": [drop]}) + RS)
                self._subscribed -= set(drop)
            if now - last_ping >= PING_S:
                await ws.send('{"type":6}' + RS)
                last_ping = now
            await asyncio.sleep(SYNC_S)

    def handle(self, raw: str | bytes) -> str | None:
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        for part in text.split(RS):
            if not part:
                continue
            try:
                msg = json.loads(part)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == 7:
                log.info("biquote ws closed by server: %s", str(msg.get("error"))[:200])
                return "close"
            if kind == 1 and msg.get("target") == "ReceiveTick":
                for tick in msg.get("arguments") or []:
                    if isinstance(tick, dict):
                        self.on_tick(tick)
        return None

    def on_tick(self, tick: dict, now_mono: float | None = None) -> None:
        sym = tick.get("symbol")
        if sym not in CATALOG or tick.get("stale") is True:
            return
        try:
            mid = float(tick["mid"])
            ts = iso_to_ms(tick["timestamp"]) if tick.get("timestamp") else int(time.time() * 1000)
        except (KeyError, TypeError, ValueError):
            return
        if not (mid > 0 and mid < 1e12):
            return
        ch = tick.get("dayDiffPercent")
        ch = float(ch) if isinstance(ch, (int, float)) else None
        self.ticks_seen += 1
        self.provider.ticks[sym] = (mid, ts, now_mono if now_mono is not None else time.monotonic(), ch)
        for interval in self.provider.native_intervals:
            topic = self.kline_topic(sym, interval)
            if topic in self._topics:
                self._apply(topic, interval, ts, mid)

    def _apply(self, topic: str, interval: str, ts: int, price: float) -> None:
        ivms = INTERVALS[interval].ms
        bucket = day_bucket(ts) if interval == "1D" else ts - ts % ivms     # Sunday evening joins Monday
        bar = self._bars.get(topic)
        if bar is None or bucket > bar[0]:
            if bar is not None and topic not in self._closing:
                # the closed bar goes out in its final REST version (volume, missed ticks); the new bar waits for it
                self._closing[topic] = asyncio.get_running_loop().create_task(self._finalize(topic, interval, list(bar)))
            self._bars[topic] = [bucket, price, price, price, price, 0.0]
        elif bucket == bar[0]:
            bar[2] = max(bar[2], price)
            bar[3] = min(bar[3], price)
            bar[4] = price
        else:
            return                      # a late tick of an earlier bar
        self._tick_at[topic] = time.monotonic()
        self._dirty.add(topic)

    async def _finalize(self, topic: str, interval: str, closed: Bar) -> None:
        """Send the closed bar once more as biquote stores it (charts only update their newest bar, so this must
        go out before the next bar does), then let the new bar through."""
        symbol = topic.partition("|")[2]
        try:
            final = closed
            try:
                await asyncio.sleep(FINAL_DELAY_S)
                rows = await asyncio.wait_for(self.provider.fetch_candles(
                    symbol, interval, closed[0], closed[0] + INTERVALS[interval].ms - 1), 5)
                hit = next((r for r in rows if r[0] == closed[0]), None)
                if hit is not None:
                    final = hit
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the streamed version is still a fine closed bar
                log.debug("biquote final bar %s: %s", topic, exc)
            if self.on_bar is not None and topic in self._topics:
                await self.on_bar(f"bq:{symbol}", interval, list(final), True)
        finally:
            if self._closing.get(topic) is asyncio.current_task():
                del self._closing[topic]

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_S)
            if not self._dirty:
                if not self._topics and not self._wanted:
                    return
                continue
            dirty = {t for t in self._dirty if t not in self._closing}   # a closing bar goes out first
            self._dirty -= dirty
            for topic in dirty:
                bar = self._bars.get(topic)
                if bar is not None and self.on_bar is not None:
                    interval, _, symbol = topic.partition("|")
                    try:
                        await self.on_bar(f"bq:{symbol}", interval, list(bar), False)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("bar callback failed: %s", exc)

    async def _reseed_loop(self) -> None:
        """Takes the REST bar of each stream (new streams first), within the background budget."""
        while self._topics or self._wanted:
            now = time.monotonic()
            due = sorted((self._seeded.get(t, 0.0), t) for t in self._topics if now - self._seeded.get(t, 0.0) >= RESEED_S)
            if not due:
                await asyncio.sleep(1.0)
                continue
            for _, topic in due:
                if topic not in self._topics:
                    continue
                try:
                    await self.reseed(topic)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.info("biquote bar refresh %s: %s", topic, exc)
                self._seeded[topic] = time.monotonic() if topic in self._topics else 0.0
                if topic not in self._topics:
                    self._seeded.pop(topic, None)
        return

    async def reseed(self, topic: str) -> None:
        interval, _, symbol = topic.partition("|")
        ivms = INTERVALS[interval].ms
        now = int(time.time() * 1000)
        rows = await self.provider.fetch_candles(symbol, interval, now - 3 * ivms, now, background=True)
        if not rows or topic not in self._topics:
            return
        rest = rows[-1]
        bar = self._bars.get(topic)
        if bar is None or rest[0] > bar[0]:
            self._bars[topic] = list(rest)
        elif rest[0] == bar[0]:
            # REST knows the open and the tick count; the close is the streamed one while ticks keep coming
            live = time.monotonic() - self._tick_at.get(topic, 0.0) < TICK_STALE_S
            self._bars[topic] = [bar[0], rest[1], max(bar[2], rest[2]), min(bar[3], rest[3]),
                                 bar[4] if live else rest[4], rest[5]]
        else:
            return
        self._dirty.add(topic)
