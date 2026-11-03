"""Bybit public WebSocket (kline + tickers) with auto-reconnect and ref-counted topic subscriptions."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable

import websockets

from .. import limits
from ..markets.base import Bar, Quote
from ..markets.bybit import INTERVAL_MAP

log = logging.getLogger("lv.bybit_ws")

URL = {"linear": "wss://stream.bybit.com/v5/public/linear", "spot": "wss://stream.bybit.com/v5/public/spot"}

BarCallback = Callable[[str, str, Bar, bool], Awaitable[None]]      # (symbol_id, interval, bar, confirmed)
QuoteCallback = Callable[[list[Quote]], Awaitable[None]]


RejectCallback = Callable[[str, str], Awaitable[None]]               # (market, topic)


class BybitStream:
    """Upstream (un)subscribe commands go through one queue per market: changes to the same topic coalesce, and
    they are sent in batches of 10 within a command budget that no client can reset by reconnecting."""

    def __init__(self, category: str, on_bar: BarCallback, on_quotes: QuoteCallback, max_topics: int = 2000,
                 cmd_rate: float = 200, cmd_burst: float = 2000, on_reject: RejectCallback | None = None):
        self.category = category
        self.max_topics = max_topics
        self.on_reject = on_reject
        self._budget = limits.AsyncBucket(cmd_rate, cmd_burst)
        self._pending: dict[str, str] = {}         # topic -> "subscribe" | "unsubscribe" (latest wins)
        self._wake = asyncio.Event()
        self._sender: asyncio.Task | None = None
        self._req: dict[str, list[str]] = {}       # req_id -> topics of a subscribe batch
        self._req_n = 0
        self.market = "bybit" if category == "linear" else "bybit-spot"
        self.on_bar = on_bar
        self.on_quotes = on_quotes
        self._topics: dict[str, int] = {}          # topic -> refcount
        self._ws: websockets.ClientConnection | None = None
        self._task: asyncio.Task | None = None
        self._last_ticker: dict[str, dict] = {}
        self._send_lock = asyncio.Lock()
        self.connected = False

    # ----------------------------------------------------------- subscriptions
    @staticmethod
    def kline_topic(symbol: str, interval: str) -> str:
        return f"kline.{INTERVAL_MAP[interval]}.{symbol}"

    @staticmethod
    def ticker_topic(symbol: str) -> str:
        return f"tickers.{symbol}"

    async def subscribe(self, topic: str, reserve: int = 0) -> bool:
        """False when the topic is new and the per-market topic cap (minus `reserve` kept for others) is reached."""
        n = self._topics.get(topic, 0)
        if n == 0 and len(self._topics) >= self.max_topics - reserve:
            return False
        self._topics[topic] = n + 1
        if n == 0:
            self._ensure_running()
            self._queue(topic, "subscribe")
        return True

    async def unsubscribe(self, topic: str) -> None:
        n = self._topics.get(topic, 0)
        if n <= 1:
            self._topics.pop(topic, None)
            self._queue(topic, "unsubscribe")
        else:
            self._topics[topic] = n - 1

    def _queue(self, topic: str, op: str) -> None:
        prev = self._pending.get(topic)
        if prev is not None and prev != op:
            del self._pending[topic]  # subscribe + unsubscribe before sending = nothing to do
        else:
            self._pending[topic] = op
        self._wake.set()
        if self._sender is None or self._sender.done():
            self._sender = asyncio.create_task(self._send_loop(), name=f"bybit-cmd-{self.category}")

    async def _send_loop(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._pending:
                if not self.connected:
                    break  # the reconnect resubscribes everything in _topics anyway
                op = next(iter(self._pending.values()))
                picked = [t for t, o in self._pending.items() if o == op][:10]
                for _ in picked:
                    await self._budget.acquire(timeout=3600)
                # the queue may have changed while we waited: send only what is still wanted, exactly as wanted
                batch = [t for t in picked if self._pending.get(t) == op]
                for t in batch:
                    del self._pending[t]
                if not batch:
                    continue
                msg: dict = {"op": op, "args": batch}
                if op == "subscribe":
                    self._req_n += 1
                    rid = f"s{self._req_n}"
                    msg["req_id"] = rid
                    self._req[rid] = batch
                    while len(self._req) > 512:
                        self._req.pop(next(iter(self._req)))
                await self._send(msg)
            if not self.connected:
                self._pending.clear()

    # ------------------------------------------------------------------ loop
    def _ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"bybit-ws-{self.category}")

    async def stop(self) -> None:
        if self._sender:
            self._sender.cancel()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _send(self, msg: dict) -> None:
        ws = self._ws
        if ws is None or not self.connected:
            return
        try:
            async with self._send_lock:
                await ws.send(json.dumps(msg))
        except Exception as exc:  # noqa: BLE001
            log.debug("send failed: %s", exc)

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(URL[self.category], ping_interval=None, max_size=4 * 1024 * 1024) as ws:
                    self._ws = ws
                    self.connected = True
                    backoff = 1.0
                    topics = list(self._topics)
                    self._pending.clear()
                    for i in range(0, len(topics), 10):  # Bybit accepts at most 10 args per request
                        for _ in topics[i:i + 10]:
                            await self._budget.acquire(timeout=3600)  # a reconnect does not skip the budget
                        self._req_n += 1
                        rid = f"s{self._req_n}"
                        self._req[rid] = topics[i:i + 10]
                        await self._send({"op": "subscribe", "args": topics[i:i + 10], "req_id": rid})
                    log.info("bybit %s ws connected (%d topics)", self.category, len(self._topics))
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        async for raw in ws:
                            await self._handle(raw)
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("bybit %s ws error: %s (reconnect in %.0fs)", self.category, exc, backoff)
            finally:
                self.connected = False
                self._ws = None
            if not self._topics:
                return  # nothing to stream; reconnect lazily on next subscribe
            await asyncio.sleep(backoff * (0.75 + random.random() / 2))  # jitter
            backoff = min(backoff * 2, 30)

    async def _ping(self, ws) -> None:
        while True:
            await asyncio.sleep(20)
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except Exception:  # noqa: BLE001
                return

    async def _handle(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if msg.get("op") == "subscribe":
            topics = self._req.pop(str(msg.get("req_id")), [])
            if msg.get("success") is False:
                log.warning("bybit %s subscribe rejected (%s): %s", self.category, ", ".join(topics), msg.get("ret_msg"))
                for t in topics:  # forget them so the next subscriber tries again, and tell current ones
                    self._topics.pop(t, None)
                    if self.on_reject is not None:
                        await self.on_reject(self.market, t)
            return
        topic = msg.get("topic", "")
        if topic.startswith("kline."):
            _, iv, symbol = topic.split(".", 2)
            interval = next((k for k, v in INTERVAL_MAP.items() if v == iv), None)
            if interval is None:
                return
            for k in msg.get("data", []):
                try:
                    bar: Bar = [int(k["start"]), float(k["open"]), float(k["high"]), float(k["low"]),
                                float(k["close"]), float(k["volume"])]
                except (KeyError, ValueError):
                    continue
                await self.on_bar(f"{self.market}:{symbol}", interval, bar, bool(k.get("confirm")))
        elif topic.startswith("tickers."):
            data = msg.get("data") or {}
            symbol = data.get("symbol") or topic.split(".", 1)[1]
            merged = {**self._last_ticker.get(symbol, {}), **data}
            self._last_ticker[symbol] = merged
            try:
                q = Quote(id=f"{self.market}:{symbol}", price=float(merged["lastPrice"]),
                          change_pct=float(merged.get("price24hPcnt") or 0) * 100, ts=int(time.time() * 1000))
            except (KeyError, ValueError):
                return
            await self.on_quotes([q])
