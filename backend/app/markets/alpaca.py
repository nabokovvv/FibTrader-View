"""US stocks & ETFs via Alpaca Market Data (free plan).

Free plan rules: `sip` (consolidated) bars are available only for windows ending >= 15 min ago;
`iex` bars are real-time but IEX-only volume. We fetch history from sip and the last ~16 minutes from
iex, and tell the candle service not to persist bars younger than that (persist_lag_ms), so the store
only ever holds consolidated bars.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

from .. import config
from ..intervals import INTERVALS, INTRADAY
from .base import Bar, Quote, SymbolInfo
from ._util import iso_to_ms, ms_to_iso, tz, utc_midnight_of_local_date

log = logging.getLogger("lv.alpaca")

DATA = "https://data.alpaca.markets"
TRADING_HOSTS = ("https://paper-api.alpaca.markets", "https://api.alpaca.markets")
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
NY = "America/New_York"
SIP_LAG_MS = 16 * 60_000
# 2h/4h are resampled from 1h so that buckets line up with the session filter below.
TF = {"1m": "1Min", "3m": "3Min", "5m": "5Min", "15m": "15Min", "30m": "30Min", "1h": "1Hour",
      "1D": "1Day", "1W": "1Week", "1M": "1Month"}
# Regular session 09:30-16:00 ET; set LV_US_EXTENDED=1 for 04:00-20:00 (pre/post market).
_EXT = os.getenv("LV_US_EXTENDED", "0") == "1"
SESSION_START_MIN = 4 * 60 if _EXT else 9 * 60 + 30
SESSION_END_MIN = 20 * 60 if _EXT else 16 * 60
KNOWN_ETFS = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "TLT", "HYG", "LQD", "EEM", "EFA", "XLF", "XLK", "XLE", "XLV",
    "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "ARKK", "SMH", "SOXX", "GLD", "IAU", "SLV", "USO",
    "BNO", "UNG", "DBC", "PDBC", "GDX", "GDXJ", "URA", "CPER", "WEAT", "CORN", "SOYB", "DBA", "TQQQ", "SQQQ",
    "UVXY", "VXX", "BITO", "IBIT", "FBTC", "ETHA", "KWEB", "FXI", "EWZ", "EWJ", "RSX",
}


class AlpacaProvider:
    market = "us"
    native_intervals = frozenset(TF)
    persist_lag_ms = SIP_LAG_MS

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=20, headers={
            "APCA-API-KEY-ID": config.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": config.ALPACA_API_SECRET,
            "User-Agent": "lobsterview/0.1",
        })
        self._sem = asyncio.Semaphore(4)
        self._symbols: list[SymbolInfo] | None = None
        self._by_symbol: dict[str, SymbolInfo] = {}
        self._cache_file: Path = config.SYMBOLS_CACHE_DIR / "us.json"
        self._sip_ok = config.ALPACA_FEED.lower() == "sip"

    # ---------------------------------------------------------------- symbols
    async def symbols(self) -> list[SymbolInfo]:
        if self._symbols is not None:
            return self._symbols
        raw = None
        if self._cache_file.exists() and time.time() - self._cache_file.stat().st_mtime < 24 * 3600:
            raw = json.loads(self._cache_file.read_text())
        if raw is None:
            raw = await self._download_assets()
            if raw:
                self._cache_file.parent.mkdir(parents=True, exist_ok=True)
                self._cache_file.write_text(json.dumps(raw))
        out = []
        for a in raw or []:
            sym = a["symbol"]
            out.append(SymbolInfo(
                id=f"us:{sym}", market="us", symbol=sym, name=a.get("name") or sym,
                type="etf" if sym in KNOWN_ETFS else "stock", exchange=a.get("exchange") or "US",
                currency="USD", price_precision=2, volume_precision=0, timezone=NY, session="0930-1600",
            ))
        self._symbols = out
        self._by_symbol = {s.symbol: s for s in out}
        log.info("us: %d assets", len(out))
        return out

    async def _download_assets(self) -> list[dict]:
        for host in TRADING_HOSTS:
            try:
                r = await self._client.get(f"{host}/v2/assets", params={"status": "active", "asset_class": "us_equity"})
                if r.status_code == 200:
                    rows = [a for a in r.json() if a.get("tradable")]
                    return [{"symbol": a["symbol"], "name": a.get("name", ""), "exchange": a.get("exchange", "")}
                            for a in rows]
                log.warning("alpaca assets %s -> %s", host, r.status_code)
            except httpx.HTTPError as exc:
                log.warning("alpaca assets %s failed: %s", host, exc)
        try:  # keyless fallback: SEC company list (names + tickers, no exchange)
            r = await self._client.get(SEC_TICKERS, headers={"User-Agent": "lobsterview admin@example.com"})
            r.raise_for_status()
            return [{"symbol": v["ticker"], "name": v["title"], "exchange": "US"} for v in r.json().values()]
        except httpx.HTTPError as exc:
            log.error("SEC tickers failed: %s", exc)
            return []

    async def resolve(self, symbol: str) -> SymbolInfo | None:
        await self.symbols()
        return self._by_symbol.get(symbol.upper())

    # ------------------------------------------------------------------- bars
    def history_floor(self, interval: str) -> int | None:
        return int(time.mktime((2016, 1, 1, 0, 0, 0, 0, 0, 0)) * 1000)

    async def fetch_bars(self, symbol: str, interval: str, start_ms: int, end_ms: int, *, ext: bool = False) -> list[Bar]:
        # Intraday pages hold only ~200 bars on the free plan: split long ranges and fetch the chunks in parallel.
        chunk = 180 * 86_400_000
        if interval in INTRADAY and end_ms - start_ms > chunk:
            edges = list(range(start_ms, end_ms, chunk))
            parts = await asyncio.gather(*(self._fetch_range(symbol, interval, s, min(s + chunk - 1, end_ms), ext)
                                           for s in edges))
            merged: dict[int, Bar] = {}
            for part in parts:
                for b in part:
                    merged[b[0]] = b
            return [merged[k] for k in sorted(merged)]
        return await self._fetch_range(symbol, interval, start_ms, end_ms, ext)

    async def _fetch_range(self, symbol: str, interval: str, start_ms: int, end_ms: int, ext: bool) -> list[Bar]:
        now = int(time.time() * 1000)
        cut = now - SIP_LAG_MS
        bars: dict[int, Bar] = {}
        if self._sip_ok and start_ms <= cut:
            for b in await self._bars(symbol, interval, start_ms, min(end_ms, cut), "sip", ext):
                bars[b[0]] = b
        if end_ms > cut or not self._sip_ok:
            s = start_ms if not self._sip_ok else max(start_ms, cut - 2 * 60_000)
            for b in await self._bars(symbol, interval, s, end_ms, "iex", ext):
                bars.setdefault(b[0], b)
        return [bars[k] for k in sorted(bars)]

    async def _bars(self, symbol: str, interval: str, start_ms: int, end_ms: int, feed: str, ext: bool = False) -> list[Bar]:
        out: list[Bar] = []
        token = None
        for _ in range(2000):
            params = {"timeframe": TF[interval], "start": ms_to_iso(start_ms), "end": ms_to_iso(end_ms),
                      "limit": 10000, "adjustment": "all" if ext else "split", "feed": feed, "sort": "asc"}
            if token:
                params["page_token"] = token
            data = await self._get(f"{DATA}/v2/stocks/{symbol}/bars", params)
            if data is None:
                break
            iv_min = INTERVALS[interval].ms // 60_000
            ny = tz(NY)
            for b in data.get("bars") or []:
                ts = iso_to_ms(b["t"])
                if interval not in INTRADAY:
                    ts = utc_midnight_of_local_date(ts, NY)
                elif not ext:
                    local = datetime.fromtimestamp(ts / 1000, tz=ny)
                    m = local.hour * 60 + local.minute
                    # keep bars whose window overlaps the trading session
                    if not (m < SESSION_END_MIN and m + iv_min > SESSION_START_MIN):
                        continue
                out.append([ts, float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]), float(b.get("v") or 0)])
            token = data.get("next_page_token")
            if not token:
                break
        return out

    # ----------------------------------------------------------------- quotes
    async def quotes(self, symbols: list[str]) -> list[Quote]:
        out: list[Quote] = []
        now = int(time.time() * 1000)
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            data = await self._get(f"{DATA}/v2/stocks/snapshots", {"symbols": ",".join(chunk), "feed": "iex"})
            for sym, snap in (data or {}).items():
                try:
                    price = snap["latestTrade"]["p"]
                    prev = (snap.get("prevDailyBar") or {}).get("c")
                    daily = (snap.get("dailyBar") or {}).get("c")
                    ref = prev if prev else daily
                    ch = (price / ref - 1) * 100 if ref else None
                    out.append(Quote(id=f"us:{sym}", price=float(price), change_pct=ch, ts=now))
                except (KeyError, TypeError, ZeroDivisionError):
                    continue
        return out

    # ------------------------------------------------------------------- http
    async def _get(self, url: str, params: dict) -> dict | None:
        async with self._sem:
            for attempt in range(4):
                try:
                    r = await self._client.get(url, params=params)
                    if r.status_code == 403 and params.get("feed") == "sip":
                        log.warning("alpaca: sip not permitted for this window, falling back to iex")
                        params = {**params, "feed": "iex"}
                        continue
                    if r.status_code == 429 or r.status_code >= 500:
                        raise httpx.HTTPStatusError("retry", request=r.request, response=r)
                    if r.status_code == 404:
                        return None
                    r.raise_for_status()
                    return r.json()
                except (httpx.HTTPError, ValueError) as exc:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(1.0 * 2 ** attempt)
                    log.debug("alpaca retry %s: %s", url, exc)
        return None


class AlpacaExtProvider:
    """1h bars incl. pre/post market with adjustment=all (extended-hours series for plugins).

    Stored as market "us-ext" for plugins only; never listed or charted directly.
    """
    market = "us-ext"
    native_intervals = frozenset({"1h"})
    persist_lag_ms = SIP_LAG_MS

    def __init__(self, base: AlpacaProvider):
        self.base = base

    async def symbols(self) -> list[SymbolInfo]:
        return []

    async def resolve(self, symbol: str) -> SymbolInfo | None:
        return None

    async def quotes(self, symbols: list[str]) -> list[Quote]:
        return []

    def history_floor(self, interval: str) -> int | None:
        return self.base.history_floor(interval)

    async def fetch_bars(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Bar]:
        return await self.base.fetch_bars(symbol, interval, start_ms, end_ms, ext=True)
