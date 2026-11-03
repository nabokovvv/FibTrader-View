"""Hyperliquid public WebSocket: live candles for open charts, with auto-reconnect and ref-counted subscriptions.

Same interface as BybitStream as far as the hub uses it (kline_topic, subscribe, unsubscribe, stop, connected).
Hyperliquid allows 1000 subscriptions and 2000 sent messages per minute per IP; the hub caps topics at the profile's
`upstream_topics` and commands go through one budget.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable

import websockets

from .. import limits
from ..markets.base import Bar
from ..markets.hyperliquid import IV

log = logging.getLogger("lv.hl_ws")

URL = "wss://api.hyperliquid.xyz/ws"
FROM_HL = {v: k for k, v in IV.items()}
BarCallback = Callable[[str, str, Bar, bool], Awaitable[None]]


class HLStream:
    market = "hl"

    def __init__(self, on_bar: BarCallback, max_topics: int = 400, cmd_rate: float = 20, cmd_burst: float = 64):
        self.on_bar = on_bar
        self.max_topics = min(max_topics, 900)          # Hyperliquid: 1000 subscriptions per IP
        self._budget = limits.AsyncBucket(min(cmd_rate, 25), min(cmd_burst, 200))  # ≤ 2000 messages a minute
        self._topics: dict[str, int] = {}                # "interval|coin" -> refcount
        self._pending: dict[str, str] = {}               # topic -> "subscribe" | "unsubscribe" (latest wins)
        self._wake = asyncio.Event()
        self._ws = None
        self._task: asyncio.Task | None = None
        self._sender: asyncio.Task | None = None
        self.connected = False

    @staticmethod
    def kline_topic(symbol: str, interval: str) -> str:
        return f"{interval}|{symbol}"

    async def subscribe(self, topic: str, reserve: int = 0) -> bool:
        n = self._topics.get(topic, 0)
        if n == 0 and len(self._topics) >= self.max_topics - reserve:
            return False
        self._topics[topic] = n + 1
        if n == 0:
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run(), name="hl-ws")
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
            del self._pending[topic]          # subscribe + unsubscribe before sending = nothing to do
        else:
            self._pending[topic] = op
        self._wake.set()
        if self._sender is None or self._sender.done():
            self._sender = asyncio.create_task(self._send_loop(), name="hl-ws-cmd")

    @staticmethod
    def _msg(topic: str, op: str) -> dict:
        interval, _, coin = topic.partition("|")
        return {"method": op, "subscription": {"type": "candle", "coin": coin, "interval": IV[interval]}}

    async def _send_loop(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._pending and self.connected:
                topic, op = next(iter(self._pending.items()))
                await self._budget.acquire(timeout=3600)
                if self._pending.get(topic) != op:
                    continue                  # changed while we waited
                del self._pending[topic]
                await self._send(self._msg(topic, op))
            if not self.connected:
                self._pending.clear()         # the reconnect subscribes everything in _topics anyway

    async def _send(self, msg: dict) -> None:
        ws = self._ws
        if ws is None or not self.connected:
            return
        try:
            await ws.send(json.dumps(msg))
        except Exception as exc:  # noqa: BLE001
            log.debug("send failed: %s", exc)

    async def stop(self) -> None:
        for t in (self._sender, self._task):
            if t:
                t.cancel()
        if self._task:
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(URL, ping_interval=None, max_size=4 * 1024 * 1024, open_timeout=15) as ws:
                    self._ws, self.connected = ws, True
                    backoff = 1.0
                    # a fresh connection has no subscriptions: queue the wanted ones for the single sender, which
                    # re-checks the wanted state after every budget wait (an unsubscribe meanwhile cancels it)
                    self._pending = {t: "subscribe" for t in self._topics}
                    self._wake.set()
                    if self._sender is None or self._sender.done():
                        self._sender = asyncio.create_task(self._send_loop(), name="hl-ws-cmd")
                    log.info("hyperliquid ws connected (%d topics)", len(self._topics))
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        async for raw in ws:
                            await self.handle(raw)
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("hyperliquid ws error: %s (reconnect in %.0fs)", exc, backoff)
            finally:
                self.connected, self._ws = False, None
            if not self._topics:
                return  # nothing to stream; reconnect on the next subscribe
            await asyncio.sleep(backoff * (0.75 + random.random() / 2))
            backoff = min(backoff * 2, 30)

    async def _ping(self, ws) -> None:
        while True:
            await asyncio.sleep(30)  # Hyperliquid drops connections that send nothing for 60 s
            try:
                await ws.send('{"method":"ping"}')
            except Exception:  # noqa: BLE001
                return

    async def handle(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        ch = msg.get("channel")
        if ch == "error":
            log.info("hyperliquid ws: %s", str(msg.get("data"))[:200])
            return
        if ch != "candle":
            return
        d = msg.get("data") or {}
        interval = FROM_HL.get(d.get("i"))
        coin = d.get("s")
        if interval is None or not isinstance(coin, str) or self.kline_topic(coin, interval) not in self._topics:
            return
        try:
            bar: Bar = [int(d["t"]), float(d["o"]), float(d["h"]), float(d["l"]), float(d["c"]), float(d["v"])]
        except (KeyError, TypeError, ValueError):
            return
        await self.on_bar(f"hl:{coin}", interval, bar, False)
