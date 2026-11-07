"""Older history on the public instance: a soft guard against bulk downloading (Astra reviews 2026-11-06).

Candles (GET /api/bars) and derivatives series (GET /api/derivs/*) of the newest FRESH_BARS bars of a series — the
"fresh window", now − FRESH_BARS × the bar size actually sent — are open to everyone. What is older:

* Guests (no session: on the public site that is people opening a shared chart, and scripts) get nothing older: an
  answer reaching back is cut at the window's edge (it says ``locked: "login"`` and ``floor``), a request lying
  wholly before it is refused (403 ``login_required``). A shared chart also opens FRESH_BARS on either side of its
  own moment — only when that moment was fresh when the link was made (a link cannot open history its author
  could not have shown a guest anyway).
* Signed-in accounts pay for older bars from a wallet: WALLET bars, refilled at REFILL bars a second; only bars
  older than the fresh window that were actually sent are charged (refusals and errors are free). An empty wallet
  paces the account (one page per SLOW_S per 500 bars: 429 ``slow`` with Retry-After; the UI waits and goes on; the
  paced pages are charged too, so the wallet climbs back only as fast as it refills). More than STOP bars in 24 h
  (an amount nobody scrolls by hand) closes older history for STOP_MS: 429 ``stopped`` with the moment it opens
  again, and the fresh window keeps working. Each network (an IPv4 address, an IPv6 /64) has a wallet of its own,
  NET_FACTOR times larger, so an office behind one address is not cut but a script spreading over many accounts
  from one machine is.

Sizes: two years of 1-minute crypto are ~1 M bars, so a trader scrolling for hours, with bar replay and several
tabs, never meets the pace, let alone the stop; a script pulling everything meets both within the first day.

State lives in memory (a bounded LRU; a wallet with a request in flight is never pushed out) and is written to
``data/history.sqlite`` once a minute and at shutdown, read back on demand: a restart keeps a stop, a crash may
forget the last minute.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .intervals import INTERVALS

log = logging.getLogger("lv.history")

FRESH_BARS = 1200               # open to everyone: the newest this many bars of a series (two screens and more)
WALLET = 5_000_000              # bars older than the fresh window an account may take at full speed …
REFILL = 100.0                  # … refilled at this many bars a second (~8.6 M a day)
SLOW_S = 5.0                    # empty wallet: one page of 500 bars per this many seconds
STOP = 15_000_000               # bars in 24 h that close older history …
STOP_MS = 24 * 3_600_000        # … for this long
NET_FACTOR = 5                  # wallet / stop of a network compared with an account
HOUR_MS = 3_600_000
FLUSH_S = 60
KEEP_MS = 8 * 86_400_000        # stored state of keys unused this long is dropped


def _now() -> int:
    return int(time.time() * 1000)


def fresh_cutoff(interval: str, now_ms: int | None = None, point_ms: int | None = None) -> int:
    """Open time of the oldest bar in the fresh window of a chart of `interval`. `point_ms`: the size of what is
    actually sent when it is finer than the chart's bars (daily liquidations on a monthly chart, 8-hour funding): the
    window then counts those, so a coarse chart interval cannot open years of fine points."""
    ms = INTERVALS[interval].ms
    now = _now() if now_ms is None else now_ms
    cut = (now - FRESH_BARS * ms) // ms * ms
    if point_ms and point_ms < ms:
        cut = max(cut, (now - FRESH_BARS * point_ms) // point_ms * point_ms)
    return cut


def share_window(interval: str, moment_ms: int, created_ms: int) -> tuple[int, int] | None:
    """[lo, hi) a guest may see around a shared chart's moment, or None: FRESH_BARS on either side, and only if the
    moment was inside the fresh window when the link was made."""
    ms = INTERVALS[interval].ms
    moment = min(int(moment_ms), int(created_ms))       # (the right edge may sit a little in the future)
    if moment < created_ms - FRESH_BARS * ms:
        return None
    return (moment - FRESH_BARS * ms) // ms * ms, moment + FRESH_BARS * ms


def network_key(ip: str) -> str:
    """The network an address belongs to for the network wallet: an IPv4 address, an IPv6 /64."""
    try:
        addr = ipaddress.ip_address(ip.strip().split("%")[0])
    except ValueError:
        return "?" + ip[:64]
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


class Refused(Exception):
    """An older-history request the guard refuses: answered as JSON with a machine-readable `code`."""

    def __init__(self, status: int, code: str, detail: str, retry_after: int | None = None, until: int | None = None):
        super().__init__(detail)
        self.status, self.code, self.detail, self.retry_after, self.until = status, code, detail, retry_after, until

    def body(self) -> dict:
        out: dict = {"detail": self.detail, "code": self.code}
        if self.retry_after is not None:
            out["retry_after"] = self.retry_after
        if self.until is not None:
            out["until"] = self.until
        return out

    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)} if self.retry_after is not None else {}


@dataclass
class Lock:
    """Older history is closed for this request: only `windows` ([lo, hi), hi None = up to now) may be sent."""
    kind: str                   # "login" (guest) or "stopped" (the account's daily amount)
    windows: list[tuple[int, int | None]]
    until: int | None = None

    def window_for(self, to: int | None) -> tuple[int, int | None] | None:
        """The window a request for bars before `to` (None: the newest) falls in; None: none (refuse)."""
        for lo, hi in self.windows:
            if (hi is None) if to is None else (to > lo and (hi is None or to <= hi)):
                return lo, hi
        return None

    def window_of_range(self, start: int, end: int) -> tuple[int, int | None] | None:
        """For a [start, end) request: the window holding `start`, else the one holding `end`."""
        for lo, hi in self.windows:
            if lo <= start and (hi is None or start < hi):
                return lo, hi
        return self.window_for(end)

    def refuse(self) -> Refused:
        if self.kind == "login":
            return Refused(403, "login_required", "sign in to see older history")
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime((self.until or 0) / 1000))
        return Refused(429, "stopped", f"history limit reached: older bars are available again after {when}",
                       retry_after=max(1, math.ceil(((self.until or 0) - _now()) / 1000)), until=self.until)

    def mark(self, answer: dict, floor: int) -> dict:
        answer["locked"] = self.kind
        answer["floor"] = floor
        if self.until is not None:
            answer["until"] = self.until
        return answer


@dataclass
class Wallet:
    tokens: float
    ts: int                     # wall-clock ms of the last refill
    slow_next: int = 0          # paced: the next older page not before this
    stop_until: int = 0
    hours: dict[int, int] = field(default_factory=dict)   # hour number -> bars sent (the last 24 hours)

    def refill(self, cap: float, now: int) -> None:
        self.tokens = min(cap, self.tokens + max(0, now - self.ts) / 1000 * REFILL * (cap / WALLET))
        self.ts = now

    def to_row(self, key: str, now: int) -> tuple:
        return (key, self.tokens, self.ts, self.slow_next, self.stop_until, json.dumps(self.hours, separators=(",", ":")), now)

    @staticmethod
    def from_row(r) -> Wallet:
        try:
            hours = {int(k): int(v) for k, v in json.loads(r[5] or "{}").items()}
        except (ValueError, TypeError, AttributeError):
            hours = {}
        return Wallet(float(r[1]), int(r[2]), int(r[3]), int(r[4]), hours)


@dataclass
class Ticket:
    """What an admitted request took: (key, wallet, tokens reserved, factor) per wallet."""
    items: list[tuple[str, Wallet, float, int]]
    done: bool = False


@dataclass
class Gate:
    lock: Lock | None = None
    ticket: Ticket | None = None


class HistoryQuota:
    def __init__(self, path: Path | None, *, unlimited: frozenset[str] = frozenset(), max_keys: int = 20_000,
                 clock=None):
        self.unlimited = unlimited
        self.max_keys = max_keys
        self._clock = clock or _now
        self._mem: OrderedDict[str, Wallet] = OrderedDict()
        self._dirty: set[str] = set()
        self._evicted: OrderedDict[str, Wallet] = OrderedDict()   # dirty wallets pushed out of memory, not written yet
        self._writing: dict[str, Wallet] = {}      # being written right now (the file may still hold older values)
        self._active: dict[str, int] = {}          # open tickets per key: such a wallet stays in memory
        self._flush_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._last_prune = 0
        self._db_lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
            with self._db_lock:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute("CREATE TABLE IF NOT EXISTS wallets(key TEXT PRIMARY KEY, tokens REAL NOT NULL, ts INTEGER NOT NULL, "
                                   "slow_next INTEGER NOT NULL, stop_until INTEGER NOT NULL, hours TEXT NOT NULL, "
                                   "updated INTEGER NOT NULL) WITHOUT ROWID")

    # ------------------------------------------------------------------ admission
    @staticmethod
    def _keys(user: str, ip: str) -> list[tuple[str, int]]:
        return [("u:" + user, 1), ("n:" + network_key(ip), NET_FACTOR)]

    async def admit(self, user: str, ip: str, reserve: int) -> Gate:
        """For a signed-in request that may send older bars: a lock (stopped: `windows` is set by the caller), a
        ticket (go on, settle afterwards) or Refused("slow"). `reserve` is the most the request can send."""
        if user in self.unlimited:
            return Gate()
        keys = self._keys(user, ip)
        wallets = await self._wallets([k for k, _ in keys])
        # from here on nothing awaits: two requests of one account cannot both spend the same tokens
        now = self._clock()
        for (k, f), w in zip(keys, wallets):
            w.refill(WALLET * f, now)
            self._dirty.add(k)
        until = max(w.stop_until for w in wallets)
        if until > now:
            return Gate(lock=Lock("stopped", [], until))
        enough = [w.tokens >= reserve for w in wallets]
        if not all(enough):
            wait = max(w.slow_next - now for w, ok in zip(wallets, enough) if not ok)
            if wait > 0:
                raise Refused(429, "slow", "loading older history more slowly: try again in a few seconds",
                              retry_after=max(1, math.ceil(wait / 1000)))
            gap = int(SLOW_S * 1000 * min(2.0, max(1.0, reserve / 500)))
            for w, ok in zip(wallets, enough):
                if not ok:
                    w.slow_next = now + gap
        items = []
        for (k, f), w, ok in zip(keys, wallets, enough):
            taken = float(reserve) if ok else 0.0
            w.tokens -= taken
            items.append((k, w, taken, f))
        return Gate(ticket=self._open(items))

    async def charge(self, user: str, ip: str, cost: int) -> Lock | None:
        """Afterwards: an answer that was not expected to reach back (the newest page) sent `cost` older bars. A
        stopped account gets a lock instead (the caller cuts the answer); otherwise the bars are charged, never paced."""
        if user in self.unlimited or cost <= 0:
            return None
        keys = self._keys(user, ip)
        wallets = await self._wallets([k for k, _ in keys])
        now = self._clock()
        for (k, f), w in zip(keys, wallets):
            w.refill(WALLET * f, now)
            self._dirty.add(k)
        until = max(w.stop_until for w in wallets)
        if until > now:
            return Lock("stopped", [], until)
        self.settle(self._open([(k, w, 0.0, f) for (k, f), w in zip(keys, wallets)]), cost)
        return None

    def _open(self, items: list[tuple[str, Wallet, float, int]]) -> Ticket:
        for k, *_ in items:
            self._active[k] = self._active.get(k, 0) + 1
        return Ticket(items)

    def settle(self, ticket: Ticket | None, cost: int) -> None:
        """The request sent `cost` bars older than the fresh window: charge them (the reservation is returned).
        Paced requests reserved nothing and are charged all the same."""
        if ticket is None or ticket.done:
            return
        ticket.done = True
        now = self._clock()
        hour = now // HOUR_MS
        for k, w, taken, f in ticket.items:
            w.tokens += taken - cost           # may go below zero: paid back by the refill (meanwhile: paced)
            if cost > 0:
                w.hours = {h: n for h, n in w.hours.items() if h > hour - 24}
                w.hours[hour] = w.hours.get(hour, 0) + cost
                if sum(w.hours.values()) > STOP * f:
                    w.stop_until = now + STOP_MS
                    w.hours = {}
                    log.info("older history closed for %s until %s", k if k.startswith("n:") else "an account",
                             time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(w.stop_until / 1000)))
            self._dirty.add(k)                 # (still in memory: a wallet with an open ticket is never pushed out)
            n = self._active.get(k, 0) - 1
            if n > 0:
                self._active[k] = n
            else:
                self._active.pop(k, None)

    def refund(self, ticket: Ticket | None) -> None:
        self.settle(ticket, 0)

    # ------------------------------------------------------------------ state
    async def _wallets(self, keys: list[str]) -> list[Wallet]:
        """The wallets of `keys`, in memory and most recently used. Nothing awaits after they are placed, so the
        caller's synchronous use of them cannot race with an eviction."""
        if len(self._evicted) > self.max_keys and self._conn is not None:
            await self.flush()                      # many accounts at once: write the pushed-out ones now
        missing = [k for k in keys if k not in self._mem and k not in self._evicted and k not in self._writing]
        loaded: dict[str, Wallet] = {}
        if missing and self._conn is not None:
            try:
                loaded = await asyncio.to_thread(self._load, missing)
            except Exception as exc:  # noqa: BLE001 — a broken state file must not take charts down
                log.warning("history quota: cannot read stored state: %s", exc)
        now = self._clock()
        out = []
        for k in keys:
            w = self._mem.get(k)       # (a request that awaited the same load meanwhile may have put it here)
            if w is None:
                w = (self._evicted.pop(k, None) or self._writing.get(k) or loaded.get(k)
                     or Wallet(WALLET * (NET_FACTOR if k.startswith("n:") else 1), now))
            self._mem[k] = w
            self._mem.move_to_end(k)
            out.append(w)
        keep = set(keys)
        while len(self._mem) > self.max_keys:
            victim = next((k for k in self._mem if k not in keep and k not in self._active), None)
            if victim is None:         # everything left is in use right now: allow the overshoot
                break
            w = self._mem.pop(victim)
            if victim in self._dirty:
                self._dirty.discard(victim)
                self._evicted[victim] = w
        while len(self._evicted) > 4 * self.max_keys:   # bounded even if the disk is gone: lose the oldest counters
            self._evicted.popitem(last=False)
        return out

    def _load(self, keys: list[str]) -> dict[str, Wallet]:
        assert self._conn is not None
        q = f"SELECT key, tokens, ts, slow_next, stop_until, hours FROM wallets WHERE key IN ({','.join('?' * len(keys))})"
        with self._db_lock:
            rows = self._conn.execute(q, keys).fetchall()
        return {r[0]: Wallet.from_row(r) for r in rows}

    def _write(self, rows: list[tuple], prune_before: int | None) -> None:
        assert self._conn is not None
        with self._db_lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO wallets(key, tokens, ts, slow_next, stop_until, hours, updated) VALUES(?,?,?,?,?,?,?)", rows)
                if prune_before is not None:
                    self._conn.execute("DELETE FROM wallets WHERE updated<? AND stop_until<?", (prune_before, prune_before))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    async def flush(self) -> None:
        """Write the changed wallets (one at a time: the periodic task and an overflow may both ask)."""
        if self._conn is None:     # memory only (tests): nothing to write, evicted wallets stay until the bound
            self._dirty.clear()
            return
        async with self._flush_lock:
            now = self._clock()
            pending = {k: self._mem[k] for k in self._dirty if k in self._mem}
            pending.update(self._evicted)
            prune = now - KEEP_MS if now - self._last_prune > HOUR_MS else None
            if not pending and prune is None:
                return
            rows = [w.to_row(k, now) for k, w in pending.items()]
            dirty, evicted = set(self._dirty), OrderedDict(self._evicted)
            self._dirty.clear()
            self._evicted.clear()
            self._writing = pending
            try:
                await asyncio.to_thread(self._write, rows, prune)
                if prune is not None:
                    self._last_prune = now
            except Exception as exc:  # noqa: BLE001 — keep them for the next try
                log.warning("history quota: cannot write state: %s", exc)
                self._dirty |= dirty
                for k, w in evicted.items():
                    if k not in self._mem:
                        self._evicted.setdefault(k, w)
            finally:
                self._writing = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="history-quota")

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_S)
            await self.flush()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:  # noqa: BLE001
                pass
        await self.flush()
        if self._conn is not None:
            with self._db_lock:
                self._conn.close()
            self._conn = None


# ------------------------------------------------------------------ request helpers
_SID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_shares: OrderedDict[str, tuple[float, dict | None]] = OrderedDict()   # share id -> (looked up at, share)
SHARE_CACHE_S = 300


async def _share(request, sid: str) -> dict | None:
    hit = _shares.get(sid)
    if hit is not None and time.monotonic() - hit[0] < SHARE_CACHE_S:
        return hit[1]
    from .userdata.routes import call
    try:
        share = await call(request.app.state.userdb, "share", sid)
    except Exception as exc:  # noqa: BLE001 — no link, no extra window
        log.info("history: share lookup failed: %s", exc)
        return None
    _shares[sid] = (time.monotonic(), share)
    _shares.move_to_end(sid)
    while len(_shares) > 1000:
        _shares.popitem(last=False)
    return share


async def guest_windows(request, info, interval: str, cutoff: int, share: str | None) -> list[tuple[int, int | None]]:
    """What a guest may see: the fresh window and, on a shared chart of this very series, the stretch around its
    moment (merged into one when they touch)."""
    windows: list[tuple[int, int | None]] = [(cutoff, None)]
    if not share or not 8 <= len(share) <= 32 or not set(share) <= _SID_CHARS:
        return windows
    snap = await _share(request, share)
    st = (snap or {}).get("state") or {}
    if not isinstance(st, dict) or st.get("interval") != interval or not isinstance(st.get("symbol"), str):
        return windows
    sym = await request.app.state.symbols.get(st["symbol"])
    if sym is None or sym.id != info.id:
        return windows
    created = int((snap or {}).get("created_at") or 0)
    to = st.get("to")
    moment = to if isinstance(to, (int, float)) and not isinstance(to, bool) else created
    w = share_window(interval, int(moment), created)
    if w is None:
        return windows
    lo, hi = w
    if hi >= cutoff:
        return [(min(lo, cutoff), None)]
    return [(cutoff, None), (lo, hi)]


async def gate(request, info, interval: str, cutoff: int, reaches_back: bool, reserve: int,
               share: str | None = None) -> Gate:
    """Older-history check before a request that may send bars older than `cutoff`: nothing to do for the fresh
    window or on the private instance; a guest gets a lock with its windows; an account goes through its wallets
    (a lock when stopped, a ticket to settle, or Refused("slow"))."""
    quota: HistoryQuota | None = getattr(request.app.state, "history", None)
    if quota is None or not reaches_back:
        return Gate()
    from .auth import optional_user_id
    user = await optional_user_id(request)
    if user is None:
        return Gate(lock=Lock("login", await guest_windows(request, info, interval, cutoff, share)))
    ip = request.scope.get("state", {}).get("client_ip", "unknown")
    g = await quota.admit(user, ip, reserve)
    if g.lock is not None:
        g.lock.windows = [(cutoff, None)]
    return g


async def after(request, info, interval: str, cutoff: int, cost: int, share: str | None = None) -> Lock | None:
    """After an answer that was not expected to reach back (the newest page of a sparse, session or long-closed
    market can be older than the window): a lock to cut it with (guest, stopped account), or None (charged)."""
    quota: HistoryQuota | None = getattr(request.app.state, "history", None)
    if quota is None or cost <= 0:
        return None
    from .auth import optional_user_id
    user = await optional_user_id(request)
    if user is None:
        return Lock("login", await guest_windows(request, info, interval, cutoff, share))
    ip = request.scope.get("state", {}).get("client_ip", "unknown")
    lock = await quota.charge(user, ip, cost)
    if lock is not None:
        lock.windows = [(cutoff, None)]
    return lock


def finish(request, g: Gate, cost: int | None) -> None:
    """Charge what an admitted request sent (`cost` bars older than the fresh window), or None: it failed."""
    quota: HistoryQuota | None = getattr(request.app.state, "history", None)
    if quota is not None and g.ticket is not None:
        quota.settle(g.ticket, cost or 0)
