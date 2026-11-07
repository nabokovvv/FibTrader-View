"""Hyperliquid perpetuals (keyless): the main exchange (crypto) and the HIP-3 exchanges built on it, where stocks,
indices, commodities and FX trade as 24/7 perpetuals (trade.xyz, Paragon, …).

* Ids: ``hl:BTC`` (main exchange), ``hl:xyz:TSLA`` (HIP-3 exchange "xyz"); the provider symbol is Hyperliquid's coin.
* Candles: Hyperliquid serves only the newest 5000 bars of each interval. 1h, 4h and 1D are kept for good by the
  archive job (``hl_archive``) in a store the cache cleanup never touches; shorter intervals have the rolling window.
  2h is built from 1h, 1W / 1M from 1D (Hyperliquid weeks start on Thursday and its "month" is 30 days).
* Prices are perpetual-contract trades on Hyperliquid, not exchange quotes of the underlying: they move at night and
  on weekends too.
* Requests are weighted (limits.HL_*): charts, quotes and alerts share one budget, background jobs have another.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

import httpx

from .. import limits
from ..intervals import INTERVALS
from .base import Bar, Quote, SymbolInfo
from .hl_catalog import describe

log = logging.getLogger("lv.hl")

API = "https://api.hyperliquid.xyz/info"
IV = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1D": "1d"}
WINDOW = 5000                      # newest bars Hyperliquid keeps per coin and interval
ARCHIVE_INTERVALS = ("1D", "4h", "1h")
# kept for good too, but only for the HIP-3 markets (stocks, indices, commodities, FX): crypto has its full minute
# history on Bybit, and minutes of every market would grow the disk by ~20 GB a year. 3m is not offered in the UI.
MINUTE_ARCHIVE_INTERVALS = ("30m", "15m", "5m", "1m")
DEX_LABEL = {"": "Hyperliquid", "xyz": "trade.xyz", "para": "Paragon", "io": "EntropyIO"}
SYMBOLS_TTL_S = 3600
DEAD_DEX_RECHECK_S = 24 * 3600     # an exchange with no live markets is looked at again once a day


def candle_weight(n: int) -> float:
    return 20 + math.ceil(max(0, n) / 60)


def price_decimals(px: float | None, sz_decimals: int) -> int:
    """Hyperliquid prices have at most 5 significant figures and at most 6 - szDecimals decimals."""
    cap = max(0, 6 - sz_decimals)
    if not px or px <= 0 or not math.isfinite(px):
        return min(cap, 4)
    dec = 5 - (math.floor(math.log10(px)) + 1)
    return max(0, min(dec, cap))


def split(coin: str) -> tuple[str, str]:
    """'xyz:TSLA' -> ('xyz', 'TSLA'); 'BTC' -> ('', 'BTC')."""
    dex, sep, name = coin.partition(":")
    return (dex, name) if sep else ("", coin)


class HyperliquidProvider:
    market = "hl"
    archive_intervals = frozenset(ARCHIVE_INTERVALS)
    minute_archive_intervals = frozenset(MINUTE_ARCHIVE_INTERVALS)

    @staticmethod
    def archives_minutes(coin: str) -> bool:
        return ":" in coin          # HIP-3 exchange ("xyz:TSLA"), not the main crypto exchange

    def archived(self, coin: str) -> list[str]:
        """Intervals the archive job keeps for this market, longest first."""
        ivs = list(ARCHIVE_INTERVALS) + (list(MINUTE_ARCHIVE_INTERVALS) if self.archives_minutes(coin) else [])
        return sorted(ivs, key=lambda k: -INTERVALS[k].ms)

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.native_intervals = frozenset(IV)
        self._client = client or httpx.AsyncClient(timeout=20, headers={"User-Agent": "fibtrader-view"})
        self._all: dict[str, SymbolInfo] = {}          # coin -> info, delisted ones too (old charts, drawings, archive)
        self._listed: list[SymbolInfo] = []
        self._ctx: dict[str, tuple[float, dict[str, dict]]] = {}   # dex -> (fetched at, coin -> asset context)
        self._meta: dict[str, list[dict]] = {}          # dex -> asset list of its last metaAndAssetCtxs
        self._dead: dict[str, float] = {}               # dex -> when it last had no live market
        self._sym_ts = 0.0
        self._lock = asyncio.Lock()
        self._bg: asyncio.Task | None = None

    # ------------------------------------------------------------------ http
    async def post(self, body: dict, cost: float, background: bool = False) -> object:
        bucket = limits.hl_bucket(background)
        attempts = 4 if background else 2       # a chart request does not keep a failing upstream busy
        for attempt in range(attempts):
            try:
                async with limits.hl_semaphore(background):
                    await bucket.acquire(timeout=3600 if background else 10, cost=cost)  # charts give up fast (503)
                    r = await self._client.post(API, json=body)
                if r.status_code == 429:
                    limits.hl_bucket(False).drain()  # the whole IP is over the limit: everybody waits a bit
                    limits.hl_bucket(True).drain()
                    raise httpx.HTTPStatusError("rate limited", request=r.request, response=r)
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=r.request, response=r)
                if r.status_code >= 400:
                    raise RuntimeError(f"hyperliquid {body.get('type')}: HTTP {r.status_code} {r.text[:120]}")
                return r.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == attempts - 1:
                    raise
                log.debug("hyperliquid retry %s: %s", body.get("type"), exc)
                await asyncio.sleep(1.0 * 2 ** attempt)
        return None

    # --------------------------------------------------------------- symbols
    async def symbols(self) -> list[SymbolInfo]:
        """The first load waits (charts budget); later refreshes run in the background (background budget) while
        callers keep getting the previous list — a refresh costs ~100-250 weight."""
        if not self._listed:
            async with self._lock:
                if not self._listed:
                    await self._refresh(background=False)
        elif time.time() - self._sym_ts >= SYMBOLS_TTL_S and (self._bg is None or self._bg.done()):
            self._bg = asyncio.create_task(self._refresh_locked(), name="hl-symbols")
        return self._listed

    async def _refresh_locked(self) -> None:
        async with self._lock:
            try:
                await self._refresh(background=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("hyperliquid markets refresh failed: %s", exc)
                self._sym_ts = time.time() - SYMBOLS_TTL_S + 300   # try again in 5 minutes

    async def _refresh(self, background: bool) -> None:
        dexes = await self.post({"type": "perpDexs"}, 20, background)
        full = {d["name"]: d.get("fullName") or d["name"] for d in (dexes or []) if isinstance(d, dict) and d.get("name")}
        now = time.time()
        rows: list[tuple[str, dict, dict]] = []          # (dex, asset meta, asset ctx)
        failed = False
        for dex in ["", *full]:
            if dex and now - self._dead.get(dex, 0) < DEAD_DEX_RECHECK_S:
                continue
            try:
                ctxs = await self._load_ctx(dex, background)
            except Exception as exc:  # noqa: BLE001 — one exchange failing must not hide the others (kept as known)
                log.warning("hyperliquid %s markets failed: %s", dex or "main", exc)
                failed = True
                continue
            assets = self._meta.get(dex, [])
            if dex and assets and all(a.get("isDelisted") for a in assets):
                self._dead[dex] = now
            rows += [(dex, a, ctxs.get(a.get("name"), {})) for a in assets]
        # one ticker on several HIP-3 exchanges: the most traded one is the "primary" (search, aliases)
        best: dict[str, tuple[float, str]] = {}
        for dex, a, c in rows:
            if dex and not a.get("isDelisted"):
                tick = split(a["name"])[1]
                vol = _f(c.get("dayNtlVlm")) or 0.0
                if tick not in best or vol > best[tick][0]:
                    best[tick] = (vol, a["name"])
        out: dict[str, SymbolInfo] = {}
        for dex, a, c in rows:
            coin = a.get("name")
            if not isinstance(coin, str) or not coin:
                continue
            out[coin] = self._info(dex, full.get(dex, dex), a, c, primary=best.get(split(coin)[1], (0, ""))[1] == coin)
        if out:
            # keep what we knew about exchanges we skipped (dead) or could not load this time
            self._all = {**{k: v for k, v in self._all.items() if k not in out}, **out}
            self._listed = [i for i in self._all.values() if not i.extra.get("delisted")]
            self._sym_ts = now if not failed else now - SYMBOLS_TTL_S + 60  # retry soon after a failure
            log.info("hyperliquid: %d markets listed (%d known)", len(self._listed), len(self._all))

    async def _load_ctx(self, dex: str, background: bool = False) -> dict[str, dict]:
        body = {"type": "metaAndAssetCtxs", **({"dex": dex} if dex else {})}
        res = await self.post(body, 20, background)
        meta, ctxs = res[0], res[1]
        assets = meta.get("universe", [])
        self._meta[dex] = assets
        by = {a.get("name"): c for a, c in zip(assets, ctxs) if isinstance(c, dict)}
        self._ctx[dex] = (time.time(), by)
        return by

    def _info(self, dex: str, dex_full: str, a: dict, c: dict, primary: bool) -> SymbolInfo:
        coin = a["name"]
        sz = int(a.get("szDecimals") or 0)
        delisted = bool(a.get("isDelisted"))
        extra = {"dex": dex, "vol": _f(c.get("dayNtlVlm")) or 0.0, "primary": primary, "delisted": delisted}
        px = _f(c.get("markPx")) or _f(c.get("midPx"))
        tail = " (delisted)" if delisted else ""
        if not dex:
            return SymbolInfo(id=f"hl:{coin}", market="hl", symbol=coin, name=f"{coin} / USDC Perpetual{tail}", type="crypto",
                              exchange="Hyperliquid", currency="USDC", price_precision=price_decimals(px, sz),
                              volume_precision=sz, timezone="UTC", session="24x7", aliases=[], extra=extra,
                              display=f"{coin}-USDC")
        tick = split(coin)[1]
        name, typ, aliases, display = describe(tick)
        if primary:
            aliases = list(dict.fromkeys([tick, display, *aliases]))
        else:
            aliases = []
        return SymbolInfo(id=f"hl:{coin}", market="hl", symbol=coin, name=f"{name} Perpetual{tail}", type=typ,
                          exchange=DEX_LABEL.get(dex, dex_full), currency="USDC", price_precision=price_decimals(px, sz),
                          volume_precision=sz, timezone="UTC", session="24x7", aliases=aliases, extra=extra,
                          display=display)

    async def resolve(self, symbol: str) -> SymbolInfo | None:
        """Only coins from the catalogue (delisted ones included): no network call per request."""
        await self.symbols()
        return self._all.get(symbol)

    def archive_coins(self) -> list[str]:
        """Live markets to archive: stocks / indices / commodities first, then crypto, each by volume."""
        live = [i for i in self._all.values() if not i.extra.get("delisted")]
        live.sort(key=lambda i: (i.extra.get("dex") == "", -(i.extra.get("vol") or 0.0)))
        return [i.symbol for i in live]

    def dexes(self) -> list[str]:
        """Exchanges with live markets ("" = main)."""
        return sorted({i.extra.get("dex", "") for i in self._all.values() if not i.extra.get("delisted")})

    # ------------------------------------------------------------------ bars
    def history_floor(self, interval: str) -> int | None:
        ivms = INTERVALS[interval].ms
        now = int(time.time() * 1000)
        return (now - (WINDOW - 10) * ivms) // ivms * ivms

    def window_start(self, interval: str) -> int:
        """Open time of the oldest bar Hyperliquid still serves (anything older is gone for good)."""
        ivms = INTERVALS[interval].ms
        return (int(time.time() * 1000) - WINDOW * ivms) // ivms * ivms

    async def fetch_bars(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Bar]:
        return await self.fetch_candles(symbol, interval, start_ms, end_ms)

    async def fetch_candles(self, coin: str, interval: str, start_ms: int, end_ms: int,
                            background: bool = False) -> list[Bar]:
        ivms = INTERVALS[interval].ms
        oldest = (int(time.time() * 1000) - (WINDOW + 5) * ivms) // ivms * ivms   # Hyperliquid has nothing older
        start = max(int(start_ms), oldest, 1)
        end = int(end_ms)
        if end < start:
            return []
        n = min(WINDOW, (end - start) // ivms + 1)
        body = {"type": "candleSnapshot", "req": {"coin": coin, "interval": IV[interval], "startTime": start, "endTime": end}}
        rows = await self.post(body, candle_weight(n), background) or []
        bars: dict[int, Bar] = {}
        for r in rows:
            try:
                t = int(r["t"])
                if start <= t <= end:
                    bars[t] = [t, float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"])]
            except (KeyError, TypeError, ValueError):
                continue
        return [bars[k] for k in sorted(bars)]

    # ---------------------------------------------------------------- prices
    async def mids(self, coins: set[str] | None = None, background: bool = False) -> dict[str, float]:
        """Mid prices of every coin on the exchanges that `coins` live on (all live exchanges if None)."""
        dexes = sorted({split(c)[0] for c in coins}) if coins is not None else self.dexes()
        out: dict[str, float] = {}
        for dex in dexes:
            try:
                res = await self.post({"type": "allMids", **({"dex": dex} if dex else {})}, 2, background)
            except limits.Busy:
                raise
            except Exception as exc:  # noqa: BLE001 — one exchange failing must not hide the others
                log.info("hyperliquid mids %s: %s", dex or "main", exc)
                continue
            for k, v in (res or {}).items():
                p = _f(v)
                if p is not None and (dex or not k.startswith("@")):  # "@123" = spot pairs on the main exchange
                    out[k] = p
        return out

    def cached_contexts(self, dex: str) -> tuple[float, dict[str, dict]] | None:
        """(fetched at, coin -> asset context) of the last load, without any request."""
        return self._ctx.get(dex)

    async def contexts(self, dex: str, max_age_s: float, background: bool = False) -> dict[str, dict]:
        hit = self._ctx.get(dex)
        if hit is not None and time.time() - hit[0] < max_age_s:
            return hit[1]
        return await self._load_ctx(dex, background)

    async def quotes(self, symbols: list[str]) -> list[Quote]:
        want = set(symbols)
        mids = await self.mids(want)
        now = int(time.time() * 1000)
        out: list[Quote] = []
        prev: dict[str, dict] = {}
        for dex in sorted({split(c)[0] for c in want}):
            try:
                prev.update(await self.contexts(dex, 600))
            except Exception as exc:  # noqa: BLE001 — prices without the daily change are still useful
                log.info("hyperliquid contexts %s: %s", dex or "main", exc)
        for coin in want:
            p = mids.get(coin)
            if p is None:
                continue
            pd = _f((prev.get(coin) or {}).get("prevDayPx"))
            out.append(Quote(id=f"hl:{coin}", price=p, change_pct=(p / pd - 1) * 100 if pd else None, ts=now))
        return out

    async def close(self) -> None:
        await self._client.aclose()


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None
