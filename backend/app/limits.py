"""Abuse limits. The public instance (LV_AUTH=supabase) runs on a 1 GB VM behind a Cloudflare Tunnel, so every
cost a single client can trigger is capped here; the private instance gets generous values.

Numbers come from the 2026-11-03 Astra review (.reviews/astra-public-security-2026-11-03.md), sized for normal use:
open a chart, scroll history, keep a watchlist, draw a few lines.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Profile:
    # HTTP
    body_bytes: int            # max request body (checked before JSON parsing)
    body_deadline_s: float     # the whole body must arrive within this time
    uploads_per_ip: int        # request bodies being received at once, per IP …
    uploads_global: int        # … and in total
    api_rate: float            # requests/s per IP to /api/*
    api_burst: float
    bars_rate: float           # /api/bars requests/s per IP
    bars_burst: float
    bars_per_ip: int           # concurrent /api/bars per IP
    bars_global: int           # concurrent /api/bars in total
    bars_wait_s: float         # how long a request may queue for a global slot
    auth_fail_per_min: int     # rejected tokens per IP and minute
    # candles
    backfill_bars: int | None  # max bars one request may make the server fetch (None = unlimited)
    bybit_rps: float           # outbound Bybit REST budget, shared by linear + spot
    bybit_burst: float
    bybit_concurrency: int
    candle_cache_bytes: int | None  # stop persisting candles above this size
    min_free_disk: int         # ... or when the disk has less free space than this
    # websocket
    ws_per_ip: int
    ws_global: int
    ws_handshake_rate: float   # new WS connections/s per IP after the burst
    ws_handshake_burst: float
    ws_msg_bytes: int
    ws_msg_rate: float         # client messages/s per connection
    ws_msg_burst: float
    ws_bar_subs: int           # (symbol, interval) streams per connection
    ws_quote_subs: int         # quote symbols per connection
    ws_ids_per_msg: int
    ws_queue: int              # outgoing messages buffered per client before it is dropped
    ws_send_timeout_s: float
    ws_ip_bar_subs: int        # bar streams per IP across all its connections
    ws_ip_quote_subs: int      # quote symbols per IP across all its connections
    ws_change_rate: float      # subscription changes/s per connection (a 'set' of 200 ids costs 200)
    ws_change_burst: float
    upstream_topics: int       # Bybit WS topics per market
    upstream_cmd_rate: float   # Bybit WS (un)subscribe commands/s per market, shared by all clients
    upstream_cmd_burst: float
    upstream_bar_reserve: int  # of those, kept free for chart (kline) streams
    # user data (per account)
    drawings: int
    drawings_per_symbol: int
    drawings_page_rows: int    # GET /api/drawings returns at most this many …
    drawings_page_bytes: int   # … and this many bytes per page
    drawing_reads: int         # concurrent drawing reads in total
    drawing_bytes: int
    watchlists: int
    watchlist_items: int
    layouts: int
    layout_bytes: int
    settings_keys: int
    settings_bytes: int
    user_bytes: int            # all stored JSON of one account
    app_db_bytes: int | None   # whole user database
    # alerts, feedback, shared charts, derivatives data
    alerts_active: int         # armed alerts per account
    alerts_total: int          # armed + paused + fired (history; the oldest fired make room) per account
    feedback_per_day: int
    shares: int                # shared chart links per account
    share_bytes: int
    derivs_pages: int          # Bybit pages one funding / OI request may fetch
    # older history (history.py)
    bars_page: int = 2000      # most bars one /api/bars answer may hold
    history_quota: bool = False  # guests: the fresh window only; accounts: wallet, pace, daily stop


PUBLIC = Profile(
    body_bytes=512 * 1024, body_deadline_s=10, uploads_per_ip=4, uploads_global=16, ws_handshake_rate=0.2,
    ws_handshake_burst=4, upstream_cmd_rate=20, upstream_cmd_burst=64, api_rate=15, api_burst=60, bars_rate=2, bars_burst=20, bars_per_ip=3, bars_global=4,
    bars_wait_s=15, auth_fail_per_min=30,
    backfill_bars=3000, bybit_rps=5, bybit_burst=10, bybit_concurrency=2,
    candle_cache_bytes=8 * 1024**3, min_free_disk=5 * 1024**3,
    ws_per_ip=4, ws_global=64, ws_msg_bytes=16 * 1024, ws_msg_rate=10, ws_msg_burst=30, ws_bar_subs=4,
    ws_quote_subs=51, ws_ids_per_msg=51, ws_queue=256, ws_send_timeout_s=5, ws_ip_bar_subs=6, ws_ip_quote_subs=120,
    ws_change_rate=20, ws_change_burst=250, upstream_topics=400, upstream_bar_reserve=64,
    drawings=300, drawings_per_symbol=100, drawings_page_rows=100, drawings_page_bytes=1024 * 1024, drawing_reads=2,
    drawing_bytes=16 * 1024, watchlists=10, watchlist_items=50,
    layouts=10, layout_bytes=256 * 1024, settings_keys=64, settings_bytes=64 * 1024, user_bytes=10 * 1024**2,
    app_db_bytes=1024**3,
    alerts_active=50, alerts_total=150, feedback_per_day=5, shares=50, share_bytes=128 * 1024, derivs_pages=4,
    bars_page=1000, history_quota=True,
)
PRIVATE = Profile(
    body_bytes=4 * 1024**2, body_deadline_s=60, uploads_per_ip=64, uploads_global=128, ws_handshake_rate=50,
    ws_handshake_burst=200, upstream_cmd_rate=200, upstream_cmd_burst=2000, api_rate=200, api_burst=1000, bars_rate=100, bars_burst=500, bars_per_ip=64,
    bars_global=64, bars_wait_s=60, auth_fail_per_min=10_000,
    backfill_bars=None, bybit_rps=20, bybit_burst=40, bybit_concurrency=8,
    candle_cache_bytes=None, min_free_disk=2 * 1024**3,
    ws_per_ip=64, ws_global=256, ws_msg_bytes=256 * 1024, ws_msg_rate=200, ws_msg_burst=1000, ws_bar_subs=64,
    ws_quote_subs=2001, ws_ids_per_msg=2001, ws_queue=2048, ws_send_timeout_s=10, ws_ip_bar_subs=256,
    ws_ip_quote_subs=8000, ws_change_rate=2000, ws_change_burst=10_000, upstream_topics=2000, upstream_bar_reserve=64,
    drawings=50_000, drawings_per_symbol=10_000, drawings_page_rows=2000, drawings_page_bytes=16 * 1024**2,
    drawing_reads=8, drawing_bytes=256 * 1024, watchlists=200, watchlist_items=2000,
    layouts=500, layout_bytes=4 * 1024**2, settings_keys=500, settings_bytes=1024**2, user_bytes=512 * 1024**2,
    app_db_bytes=None,
    alerts_active=500, alerts_total=2000, feedback_per_day=100, shares=1000, share_bytes=1024**2, derivs_pages=10,
)


def profile() -> Profile:
    """Public limits with Supabase auth (or LV_PROFILE=public, for testing them locally without signing in)."""
    return PUBLIC if config.AUTH == "supabase" or config.PROFILE == "public" else PRIVATE


class Busy(Exception):
    """A shared budget is exhausted: answer 503 and let the client retry."""


# ------------------------------------------------------------------ client ip
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def client_ip(headers: dict[str, str] | list[tuple[bytes, bytes]], peer: str | None) -> str:
    """Behind the tunnel the peer is cloudflared on loopback; then (and only then) trust CF-Connecting-IP."""
    if isinstance(headers, list):
        hv = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in headers}
    else:
        hv = {k.lower(): v for k, v in headers.items()}
    if peer in LOOPBACK:
        cf = hv.get("cf-connecting-ip", "").strip()
        if cf:
            return cf[:64]
    return peer or "unknown"


# --------------------------------------------------------------- rate limits
class RateLimiter:
    """Token bucket per key; the key table is an LRU with a hard size, so many IPs cannot grow memory."""

    def __init__(self, rate: float, burst: float, max_keys: int = 50_000):
        self.rate, self.burst, self.max_keys = rate, burst, max_keys
        self._b: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def has(self, key: str, cost: float = 1.0) -> bool:
        """Would `allow` succeed? (does not consume)"""
        tokens, ts = self._b.get(key, (self.burst, time.monotonic()))
        return min(self.burst, tokens + (time.monotonic() - ts) * self.rate) >= cost

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        tokens, ts = self._b.pop(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - ts) * self.rate)
        ok = tokens >= cost
        if ok:
            tokens -= cost
        self._b[key] = (tokens, now)
        if len(self._b) > self.max_keys:
            self._b.popitem(last=False)
        return ok


class KeyedGate:
    """At most `per_key` concurrent holders per key."""

    def __init__(self, per_key: int):
        self.per_key = per_key
        self._n: dict[str, int] = {}

    def enter(self, key: str) -> bool:
        n = self._n.get(key, 0)
        if n >= self.per_key:
            return False
        self._n[key] = n + 1
        return True

    def leave(self, key: str) -> None:
        n = self._n.get(key, 0) - 1
        if n <= 0:
            self._n.pop(key, None)
        else:
            self._n[key] = n

    def count(self, key: str) -> int:
        return self._n.get(key, 0)


class AsyncBucket:
    """Shared outbound budget: waits for a token up to `timeout` seconds, then raises Busy."""

    def __init__(self, rate: float, burst: float):
        self.rate, self.burst = rate, burst
        self._tokens, self._ts = burst, time.monotonic()

    async def acquire(self, timeout: float = 10.0, cost: float = 1.0) -> None:
        """Take `cost` tokens (weighted APIs: Hyperliquid charges by request type and response size)."""
        cost = min(cost, self.burst)
        deadline = time.monotonic() + timeout
        while True:
            now = time.monotonic()
            self._tokens = min(self.burst, self._tokens + (now - self._ts) * self.rate)
            self._ts = now
            if self._tokens >= cost:
                self._tokens -= cost
                return
            wait = (cost - self._tokens) / self.rate
            if now + wait > deadline:
                raise Busy("upstream budget exhausted")
            await asyncio.sleep(wait)

    def drain(self) -> None:
        """The upstream said "too many requests": spend everything so the next calls wait."""
        self._tokens, self._ts = 0.0, time.monotonic()


_bybit_bucket: AsyncBucket | None = None
_bybit_sem: asyncio.Semaphore | None = None


def bybit_semaphore() -> asyncio.Semaphore:
    """Concurrent Bybit REST calls, shared by linear + spot."""
    global _bybit_sem
    if _bybit_sem is None:
        _bybit_sem = asyncio.Semaphore(profile().bybit_concurrency)
    return _bybit_sem


def bybit_bucket() -> AsyncBucket:
    global _bybit_bucket
    if _bybit_bucket is None:
        p = profile()
        _bybit_bucket = AsyncBucket(p.bybit_rps, p.bybit_burst)
    return _bybit_bucket


# Hyperliquid limits REST by weight per IP: 1200 per minute (candles cost 20 + 1 per 60 bars, allMids 2, most other
# info requests 20). Charts, quotes and alerts share one budget; the archive, the OI recorder and catalogue refreshes
# have their own, smaller one. Both bursts hold a full 5000-bar request (weight 104) so its cost is never clamped.
# Worst minute: 300 + 9*60 + 110 + 3.5*60 = 1160 < 1200. Tokens are taken right before sending (inside the
# semaphore), so waiting for a free connection cannot bunch requests up.
HL_RATE, HL_BURST = 9.0, 300.0
HL_BG_RATE, HL_BG_BURST = 3.5, 110.0
_hl: dict[str, AsyncBucket] = {}
_hl_sem: dict[str, asyncio.Semaphore] = {}


def hl_bucket(background: bool = False) -> AsyncBucket:
    key = "bg" if background else "fg"
    b = _hl.get(key)
    if b is None:
        b = _hl[key] = AsyncBucket(HL_BG_RATE, HL_BG_BURST) if background else AsyncBucket(HL_RATE, HL_BURST)
    return b


def hl_semaphore(background: bool = False) -> asyncio.Semaphore:
    """Requests in flight: 3 for charts, 1 for background jobs (a background request waiting for its budget holds
    only its own slot)."""
    key = "bg" if background else "fg"
    s = _hl_sem.get(key)
    if s is None:
        s = _hl_sem[key] = asyncio.Semaphore(1 if background else 3)
    return s


def public_limits() -> dict:
    """What the UI shows (counters, disabled buttons) before the server would refuse."""
    p = profile()
    return {"watchlists": p.watchlists, "watchlist_items": p.watchlist_items, "drawings": p.drawings,
            "drawings_per_symbol": p.drawings_per_symbol, "layouts": p.layouts, "alerts": p.alerts_active,
            "feedback_per_day": p.feedback_per_day}


# ------------------------------------------------------------------ helpers
def dumps(value) -> str:
    """The one serialisation used to both measure and store user JSON (sizes are UTF-8 bytes of this)."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def json_size(value) -> int:
    return len(dumps(value).encode())


def json_depth_ok(value, max_depth: int = 32, max_nodes: int = 50_000) -> bool:
    """Iterative walk (no recursion limit games): nesting depth and node count."""
    stack = [(value, 1)]
    nodes = 0
    while stack:
        v, d = stack.pop()
        nodes += 1
        if d > max_depth or nodes > max_nodes:
            return False
        if isinstance(v, dict):
            stack.extend((x, d + 1) for x in v.values())
        elif isinstance(v, list):
            stack.extend((x, d + 1) for x in v)
    return True
