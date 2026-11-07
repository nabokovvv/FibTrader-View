"""WebSocket hub: clients subscribe to bar streams (symbol+interval) and quote streams (symbols).

Upstream: Bybit, Hyperliquid and biquote via push WebSockets; everything else via per-subscription pollers that re-read the
last two bars through the candle service (which also persists newly closed bars).

Abuse limits (see app/limits.py): connections per IP and in total, message size and rate, subscriptions per
connection, upstream topics per market. Every subscription is validated (known symbol, known interval) before
any state is created. Each client has its own bounded send queue and writer task, so a slow client is dropped
instead of holding up the shared upstream stream.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket

from .. import config, limits
from ..candles.service import CandleService
from ..intervals import INTERVALS
from ..markets.base import Bar, Provider, Quote
from ..symbols import SymbolRegistry
from ..markets.bybit import INTERVAL_MAP
from .bybit_ws import BybitStream
from .hl_ws import HLStream

log = logging.getLogger("lv.hub")


@dataclass(eq=False)
class Client:
    ws: WebSocket
    ip: str
    queue: asyncio.Queue
    bars: set[tuple[str, str]] = field(default_factory=set)
    quotes: set[str] = field(default_factory=set)
    closed: bool = False
    writer: asyncio.Task | None = None
    changes: limits.RateLimiter | None = None   # subscription-change budget

    def send(self, msg: dict) -> bool:
        """Queue a message; False (and the client is marked closed) when its queue is full."""
        if self.closed:
            return False
        try:
            self.queue.put_nowait(json.dumps(msg, separators=(",", ":")))
            return True
        except asyncio.QueueFull:
            self.closed = True
            return False


class Hub:
    def __init__(self, candles: CandleService, symbols: SymbolRegistry, providers: dict[str, Provider]):
        self.candles = candles
        self.symbols = symbols
        self.providers = providers
        self.lim = limits.profile()
        self.clients: set[Client] = set()
        self._per_ip = limits.KeyedGate(self.lim.ws_per_ip)
        self._ip_bars = limits.KeyedGate(self.lim.ws_ip_bar_subs)      # across all connections of an IP
        self._ip_quotes: dict[str, dict[str, int]] = {}                 # ip -> unique quote symbol -> tabs using it
        self._handshakes = limits.RateLimiter(self.lim.ws_handshake_rate, self.lim.ws_handshake_burst)
        self._bar_refs: dict[tuple[str, str], int] = {}
        self._bar_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._quote_refs: dict[str, int] = {}
        self._quote_tasks: dict[str, asyncio.Task] = {}       # market -> poller task
        kw = dict(max_topics=self.lim.upstream_topics, cmd_rate=self.lim.upstream_cmd_rate,
                  cmd_burst=self.lim.upstream_cmd_burst, on_reject=self._on_reject)
        self._bybit = {
            "bybit": BybitStream("linear", self._on_bybit_bar, self._on_quotes, **kw),
            "bybit-spot": BybitStream("spot", self._on_bybit_bar, self._on_quotes, **kw),
        }
        # push streams for chart bars (quotes of Hyperliquid are polled: one request covers a whole exchange)
        self._bar_streams: dict = dict(self._bybit)
        if "hl" in providers:
            self._bar_streams["hl"] = HLStream(self._on_bybit_bar, max_topics=self.lim.upstream_topics,
                                               cmd_rate=self.lim.upstream_cmd_rate, cmd_burst=self.lim.upstream_cmd_burst)
        if "bq" in providers:  # biquote: one tick stream builds the bars; its quotes are read from the same ticks
            from .bq_ws import BiquoteStream
            bq = providers["bq"]
            bq.stream = BiquoteStream(bq, self._on_bybit_bar, max_topics=self.lim.upstream_topics)
            self._bar_streams["bq"] = bq.stream

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        for t in list(self._bar_tasks.values()) + list(self._quote_tasks.values()):
            t.cancel()
        for s in self._bar_streams.values():
            await s.stop()

    # ---------------------------------------------------------------- clients
    async def handle(self, ws: WebSocket) -> None:
        peer = ws.client.host if ws.client else None
        ip = limits.client_ip(dict(ws.headers), peer)
        if not self._handshakes.allow(ip) or len(self.clients) >= self.lim.ws_global or not self._per_ip.enter(ip):
            await ws.close(code=1013)  # try again later
            return
        client = Client(ws, ip, asyncio.Queue(maxsize=self.lim.ws_queue),
                        changes=limits.RateLimiter(self.lim.ws_change_rate, self.lim.ws_change_burst, max_keys=1))
        try:
            await ws.accept()
            self.clients.add(client)
            client.writer = asyncio.create_task(self._writer(client), name="ws-writer")
            rate = limits.RateLimiter(self.lim.ws_msg_rate, self.lim.ws_msg_burst, max_keys=1)
            while not client.closed:
                raw = await ws.receive_text()
                if len(raw) > self.lim.ws_msg_bytes or not rate.allow("c"):
                    await ws.close(code=1008)  # policy violation
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    await self._on_message(client, msg)
        except Exception as exc:  # disconnects raise WebSocketDisconnect (a subclass of Exception)
            log.debug("ws closed: %s", type(exc).__name__)
        finally:
            client.closed = True
            self.clients.discard(client)
            self._per_ip.leave(ip)
            if client.writer:
                client.writer.cancel()
            for key in list(client.bars):
                await self._unsub_bars(client, key)
            for sid in list(client.quotes):
                await self._unsub_quote(client, sid)

    async def _writer(self, client: Client) -> None:
        try:
            while True:
                text = await client.queue.get()
                await asyncio.wait_for(client.ws.send_text(text), timeout=self.lim.ws_send_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001  slow or gone: drop the client
            client.closed = True
            try:
                await client.ws.close(code=1011)
            except Exception:  # noqa: BLE001
                pass

    def _deliver(self, client: Client, msg: dict) -> None:
        if not client.send(msg):
            # queue overflow: the writer is stuck on this client; closing ends its receive loop
            asyncio.get_running_loop().create_task(self._close(client))

    @staticmethod
    async def _close(client: Client) -> None:
        try:
            await client.ws.close(code=1011)
        except Exception:  # noqa: BLE001
            pass

    async def _on_message(self, client: Client, msg: dict) -> None:
        op, ch = msg.get("op"), msg.get("ch")
        if op == "ping":
            t = msg.get("t")
            self._deliver(client, {"ch": "pong", "t": t if isinstance(t, (int, float)) else None})
        elif ch == "bars" and op in ("sub", "unsub"):
            sid, interval = msg.get("id"), msg.get("interval")
            if not isinstance(sid, str) or not isinstance(interval, str) or len(sid) > 64 or interval not in INTERVALS:
                return
            if client.changes is not None and not client.changes.allow("c"):
                self._error(client, "too many subscription changes, slow down")
                return
            if op == "sub":
                await self._sub_bars(client, sid, interval)
            else:
                info = await self.symbols.get(sid)
                if info is not None:
                    await self._unsub_bars(client, (info.id, interval))
        elif ch == "quotes" and op in ("sub", "unsub", "set"):
            raw = msg.get("ids")
            if not isinstance(raw, list):
                return
            ids = [i for i in raw[: self.lim.ws_ids_per_msg] if isinstance(i, str) and len(i) <= 64]
            if op == "unsub":
                for sid in ids:
                    info = await self.symbols.get(sid)
                    if info is not None:
                        await self._unsub_quote(client, info.id)
                return
            wanted: list[str] = []
            for sid in ids:
                info = await self.symbols.get(sid)
                if info is not None and info.id not in wanted:
                    wanted.append(info.id)
            drop = list(client.quotes - set(wanted)) if op == "set" else []
            cost = len(drop) + sum(1 for sid in wanted if sid not in client.quotes)
            if cost and client.changes is not None and not client.changes.allow("c", cost):
                self._error(client, "too many subscription changes, slow down")
                return
            if op == "set":  # replace the whole quote subscription set
                for sid in drop:
                    await self._unsub_quote(client, sid)
            for sid in wanted:
                if sid not in client.quotes and not await self._sub_quote(client, sid):
                    break

    def _error(self, client: Client, text: str) -> None:
        self._deliver(client, {"ch": "error", "msg": text})

    # ------------------------------------------------------------------- bars
    async def _sub_bars(self, client: Client, sid: str, interval: str) -> None:
        info = await self.symbols.get(sid)
        if info is None or client.closed:
            return
        key = (info.id, interval)
        if key in client.bars:
            return
        if len(client.bars) >= self.lim.ws_bar_subs:
            self._error(client, "too many chart streams on this connection")
            return
        if not self._ip_bars.enter(client.ip):
            self._error(client, "too many chart streams from your network")
            return
        first = self._bar_refs.get(key, 0) == 0
        self._bar_refs[key] = self._bar_refs.get(key, 0) + 1   # reserve before awaiting, see _unsub_bars
        client.bars.add(key)
        if not first:
            return
        stream = self._bar_streams.get(info.market)
        if stream is not None and interval in self.providers[info.market].native_intervals:
            if not await stream.subscribe(stream.kline_topic(info.symbol, interval)):
                self._drop_bar_ref(client, key)
                self._error(client, "live updates are at capacity, try again later")
        else:
            self._bar_tasks[key] = asyncio.create_task(self._poll_bars(info, interval), name=f"poll-{info.id}-{interval}")

    def _drop_bar_ref(self, client: Client, key: tuple[str, str]) -> int:
        if key in client.bars:
            self._ip_bars.leave(client.ip)
        client.bars.discard(key)
        n = self._bar_refs.get(key, 0) - 1
        if n <= 0:
            self._bar_refs.pop(key, None)
            return 0
        self._bar_refs[key] = n
        return n

    async def _unsub_bars(self, client: Client, key: tuple[str, str]) -> None:
        if key not in client.bars:
            return
        if self._drop_bar_ref(client, key) > 0:
            return
        task = self._bar_tasks.pop(key, None)
        if task:
            task.cancel()
            return
        sid, interval = key
        info = await self.symbols.get(sid)
        if info is None:
            return
        stream = self._bar_streams.get(info.market)
        if stream is not None and interval in self.providers[info.market].native_intervals:
            await stream.unsubscribe(stream.kline_topic(info.symbol, interval))

    async def _poll_bars(self, info, interval: str) -> None:
        period = config.POLL_BARS_S.get(info.market, 10)
        last_sent: dict[int, Bar] = {}
        while True:
            try:
                bars = await self.candles.latest(info, interval, 2)
                for b in bars:
                    prev = last_sent.get(b[0])
                    if prev != b:
                        last_sent[b[0]] = b
                        await self._broadcast_bar(info.id, interval, b, closed=False)
                if len(last_sent) > 8:
                    for k in sorted(last_sent)[:-4]:
                        last_sent.pop(k, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("poll %s %s failed: %s", info.id, interval, exc)
            await asyncio.sleep(period)

    async def _on_bybit_bar(self, sid: str, interval: str, bar: Bar, confirmed: bool) -> None:
        await self._broadcast_bar(sid, interval, bar, closed=confirmed)

    async def _broadcast_bar(self, sid: str, interval: str, bar: Bar, closed: bool) -> None:
        key = (sid, interval)
        msg = {"ch": "bars", "id": sid, "interval": interval, "bar": bar, "closed": closed}
        for c in list(self.clients):
            if key in c.bars:
                self._deliver(c, msg)   # never awaits the network: the upstream reader is not held up

    # ----------------------------------------------------------------- quotes
    async def _sub_quote(self, client: Client, sid: str) -> bool:
        """False when this connection hit its quote cap."""
        if sid in client.quotes or client.closed:
            return True
        if len(client.quotes) >= self.lim.ws_quote_subs:
            self._error(client, "too many live quotes on this connection")
            return False
        info = await self.symbols.get(sid)
        if info is None:
            return True
        mine = self._ip_quotes.setdefault(client.ip, {})
        if sid not in mine and len(mine) >= self.lim.ws_ip_quote_subs:  # unique symbols: two tabs of a list count once
            self._error(client, "too many live quotes from your network")
            return False
        mine[sid] = mine.get(sid, 0) + 1
        first = self._quote_refs.get(sid, 0) == 0
        self._quote_refs[sid] = self._quote_refs.get(sid, 0) + 1
        client.quotes.add(sid)
        if not first:
            return True
        stream = self._bybit.get(info.market)
        if stream is not None:
            # quotes may not take the topics kept for charts
            if not await stream.subscribe(stream.ticker_topic(info.symbol), reserve=self.lim.upstream_bar_reserve):
                self._drop_quote_ref(client, sid)
                self._error(client, "live quotes are at capacity, try again later")
                return False
        else:
            self._ensure_quote_poller(info.market)
        return True

    def _drop_quote_ref(self, client: Client, sid: str) -> int:
        if sid in client.quotes:
            mine = self._ip_quotes.get(client.ip, {})
            if mine.get(sid, 0) <= 1:
                mine.pop(sid, None)
                if not mine:
                    self._ip_quotes.pop(client.ip, None)
            else:
                mine[sid] -= 1
        client.quotes.discard(sid)
        n = self._quote_refs.get(sid, 0) - 1
        if n <= 0:
            self._quote_refs.pop(sid, None)
            return 0
        self._quote_refs[sid] = n
        return n

    async def _unsub_quote(self, client: Client, sid: str) -> None:
        if sid not in client.quotes:
            return
        if self._drop_quote_ref(client, sid) > 0:
            return
        info = await self.symbols.get(sid)
        if info is None:
            return
        stream = self._bybit.get(info.market)
        if stream is not None:
            await stream.unsubscribe(stream.ticker_topic(info.symbol))

    def _ensure_quote_poller(self, market: str) -> None:
        t = self._quote_tasks.get(market)
        if t is None or t.done():
            self._quote_tasks[market] = asyncio.create_task(self._poll_quotes(market), name=f"quotes-{market}")

    async def _poll_quotes(self, market: str) -> None:
        period = config.POLL_QUOTES_S.get(market, 30)
        provider = self.providers[market]
        while True:
            ids = [sid for sid in self._quote_refs if sid.startswith(market + ":")]
            if not ids:
                return
            try:
                symbols = [sid.split(":", 1)[1] for sid in ids]
                quotes = await provider.quotes(symbols)
                if quotes:
                    await self._on_quotes(quotes)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("quotes %s failed: %s", market, exc)
            await asyncio.sleep(period)

    async def _on_quotes(self, quotes: list[Quote]) -> None:
        by_id = {q.id: q.to_dict() for q in quotes}
        for c in list(self.clients):
            mine = {k: v for k, v in by_id.items() if k in c.quotes}
            if mine:
                self._deliver(c, {"ch": "quotes", "q": mine})

    async def _on_reject(self, market: str, topic: str) -> None:
        """Bybit refused a topic: drop it everywhere so the next subscriber retries, and tell who was waiting."""
        kind, _, rest = topic.partition(".")
        if kind == "kline":
            iv_code, _, symbol = rest.partition(".")
            interval = next((k for k, v in INTERVAL_MAP.items() if v == iv_code), None)
            key = (f"{market}:{symbol}", interval)
            for c in list(self.clients):
                if key in c.bars:
                    self._drop_bar_ref(c, key)
                    self._error(c, "live updates for this chart are unavailable right now")
            self._bar_refs.pop(key, None)
        elif kind == "tickers":
            sid = f"{market}:{rest}"
            for c in list(self.clients):
                if sid in c.quotes:
                    self._drop_quote_ref(c, sid)
            self._quote_refs.pop(sid, None)

    def status(self) -> dict:
        return {
            "clients": len(self.clients),
            "bar_streams": [f"{k[0]}@{k[1]}" for k in self._bar_refs],
            "quote_subs": len(self._quote_refs),
            "bybit": {m: s.connected for m, s in self._bybit.items()},
            "streams": {m: s.connected for m, s in self._bar_streams.items()},
        }
