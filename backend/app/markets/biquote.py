"""biquote.io (keyless): CFD prices of a MetaTrader 5 broker for US stocks, indices, commodities and FX majors.

* A fixed, curated list of 70 instruments (``CATALOG``); ids are ``bq:<SYMBOL>`` (``bq:US500``, ``bq:EURUSD``).
  biquote also lists USDT "stocks" and crypto aggregated from crypto exchanges and Turkish gold quotes: we leave
  those out (the crypto ones duplicate Bybit / Hyperliquid).
* Candles: ``GET /api/{symbol}/ohlc`` serves only a fixed window of the newest bars (about 5 h of 1m, 1 day of 5m,
  2 days of 15m, 4 days of 30m, 9 days of 1h, 5–6 weeks of 4h and ~500 daily bars) and cannot be paged back.
  Every interval is kept for good by the archive job (``bq_archive``): 1h / 4h / 1D in ``bq-archive.sqlite``
  (nightly backup), 1m … 30m in ``bq-minutes-archive.sqlite`` (not backed up: big). History on the site starts where
  biquote's window started on the first run and grows from there.
* Volume: biquote has no traded volume for CFDs, the bars carry the tick count (``tickVolume``): that is our volume.
* Live prices: one SignalR WebSocket (``realtime/bq_ws.py``) carries the ticks of the symbols in use (charts, quotes,
  alerts); bar streams are built from those ticks and corrected from REST once a minute.
* Prices are CFD quotes of one broker (mid of bid / ask), not exchange trades: stocks trade only in their session,
  FX / metals / indices around the clock on weekdays.
* biquote's terms allow showing the data in a product (credit appreciated, the raw feed may not be passed on):
  the UI credits "biquote.io" on these charts and we never expose a raw feed.
* Requests: biquote allows 15 000 per minute per IP; ours go through a small budget of their own (charts and
  background jobs separately), far below that.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

import httpx

from .. import limits
from ..intervals import DAY, HOUR, INTERVALS, MINUTE
from ._util import iso_to_ms
from .base import Bar, Quote, SymbolInfo

log = logging.getLogger("lv.bq")

API = "https://biquote.io/api"
IV = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1D": "1d"}
ARCHIVE_INTERVALS = ("1D", "4h", "1h")
MINUTE_ARCHIVE_INTERVALS = ("30m", "15m", "5m", "1m")    # kept for good as well, in a file the backup skips
MAX_LIMIT = 1000
# A safe lower bound of how far back biquote's window reaches (it is a bar count, weekends shorten it in time):
# a chart request that starts at or before this has fetched everything biquote still has.
WINDOW_MS = {"1m": 4 * HOUR, "5m": 20 * HOUR, "15m": 40 * HOUR, "30m": 3 * DAY, "1h": 6 * DAY, "4h": 28 * DAY,
             "1D": 400 * DAY}
TICK_FRESH_S = 30            # a streamed price older than this is not used for quotes / alerts

_S, _I, _C, _F = "stock", "index", "commodity", "forex"
FX_SESSION, US_SESSION = "24x5", "us-equity"

# symbol: (name, type, price precision, aliases)
CATALOG: dict[str, tuple[str, str, int, list[str]]] = {
    # US stocks (CFDs: regular US session)
    "AAPL": ("Apple", _S, 2, []), "MSFT": ("Microsoft", _S, 2, []), "NVDA": ("NVIDIA", _S, 2, []),
    "TSLA": ("Tesla", _S, 2, []), "AMZN": ("Amazon", _S, 2, []), "META": ("Meta Platforms", _S, 2, ["FB"]),
    "NFLX": ("Netflix", _S, 2, []), "AMD": ("AMD", _S, 2, []), "INTC": ("Intel", _S, 2, []),
    "BA": ("Boeing", _S, 2, []), "BAC": ("Bank of America", _S, 2, []), "TSM": ("TSMC", _S, 2, []),
    "BABA": ("Alibaba", _S, 2, []), "PDD": ("PDD Holdings", _S, 2, []), "JD": ("JD.com", _S, 2, []),
    "BEKE": ("KE Holdings", _S, 2, []), "FUTU": ("Futu Holdings", _S, 2, []), "FTNT": ("Fortinet", _S, 2, []),
    "BB": ("BlackBerry", _S, 2, []),
    # indices
    "US500": ("S&P 500", _I, 2, ["SPX", "SP500", "S&P500", "SNP500", "ES"]),
    "USTEC": ("Nasdaq-100", _I, 2, ["NDX", "NAS100", "US100", "NASDAQ", "NASDAQ100", "NQ"]),
    "US30": ("Dow Jones 30", _I, 2, ["DJI", "DOW", "DJIA", "YM"]),
    "DXY": ("US Dollar Index", _I, 3, ["USDX", "DX"]),
    "DE30": ("Germany 40 (DAX)", _I, 2, ["DAX", "GER40", "DE40"]),
    "FR40": ("France 40 (CAC 40)", _I, 2, ["CAC", "CAC40"]),
    "UK100": ("UK 100 (FTSE 100)", _I, 2, ["FTSE", "FTSE100"]),
    "STOXX50": ("Euro Stoxx 50", _I, 2, ["SX5E", "EU50", "ESTX50"]),
    "JP225": ("Japan 225 (Nikkei)", _I, 1, ["NIKKEI", "N225", "NI225"]),
    "HK50": ("Hong Kong 50 (Hang Seng)", _I, 1, ["HSI", "HANGSENG"]),
    "AUS200": ("Australia 200 (ASX 200)", _I, 1, ["ASX200", "XJO"]),
    # commodities
    "XAUUSD": ("Gold", _C, 2, ["GOLD", "XAU"]), "XAGUSD": ("Silver", _C, 3, ["SILVER", "XAG"]),
    "XPTUSD": ("Platinum", _C, 2, ["PLATINUM", "XPT"]), "XPDUSD": ("Palladium", _C, 2, ["PALLADIUM", "XPD"]),
    "XCUUSD": ("Copper", _C, 2, ["COPPER", "XCU"]), "XALUSD": ("Aluminium", _C, 2, ["ALUMINIUM", "ALUMINUM"]),
    "XNIUSD": ("Nickel", _C, 2, ["NICKEL"]), "XZNUSD": ("Zinc", _C, 2, ["ZINC"]), "XPBUSD": ("Lead", _C, 2, ["LEAD"]),
    "XNGUSD": ("Natural gas", _C, 4, ["NATGAS", "NG", "GAS"]),
    "USOIL": ("WTI crude oil", _C, 3, ["WTI", "OIL", "CRUDE", "CL"]),
    "UKOIL": ("Brent crude oil", _C, 3, ["BRENT", "BRENTOIL"]),
    # FX majors and their crosses
    **{p: (f"{p[:3]} / {p[3:]}", _F, 3 if p.endswith("JPY") else 5, [f"{p[:3]}/{p[3:]}"]) for p in (
        "EURUSD GBPUSD USDJPY USDCHF AUDUSD USDCAD NZDUSD EURGBP EURJPY EURCHF EURAUD EURCAD EURNZD GBPJPY GBPCHF "
        "GBPAUD GBPCAD GBPNZD AUDJPY AUDCHF AUDCAD AUDNZD NZDJPY NZDCHF NZDCAD CADJPY CADCHF CHFJPY").split()},
}

# own outbound budget (biquote: 15 000 requests a minute per IP): charts and the background jobs separately
FG_RATE, FG_BURST = 8.0, 40.0
BG_RATE, BG_BURST = 2.0, 10.0


def day_bucket(ts: int) -> int:
    """Open time of the daily bar a moment belongs to: its UTC day, but Sunday counts as Monday. FX, metals, oil
    and index CFDs open on Sunday evening (~22:00 UTC): on its own that hour or two would be a tiny bar between
    the weeks (charts that start the day at 17:00 New York have no such bar either)."""
    d = ts - ts % DAY
    return d + DAY if (d // DAY + 3) % 7 == 6 else d      # 1970-01-01 was a Thursday (Monday = 0)


def merge_sundays(rows: list[Bar]) -> list[Bar]:
    """Daily bars (ascending) with each Sunday bar folded into the Monday after it: Sunday's open, the high and low
    of both, Monday's close, the volumes added (a Sunday without a Monday bar becomes that Monday's bar)."""
    out: list[Bar] = []
    for b in rows:
        t = day_bucket(int(b[0]))
        if out and out[-1][0] == t:       # this is the Monday after a Sunday bar
            prev = out[-1]
            out[-1] = [t, prev[1], max(prev[2], b[2]), min(prev[3], b[3]), b[4], prev[5] + b[5]]
        else:
            out.append([t, *b[1:6]])
    return out


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


class BiquoteProvider:
    market = "bq"
    archive_intervals = frozenset(ARCHIVE_INTERVALS)
    minute_archive_intervals = frozenset(MINUTE_ARCHIVE_INTERVALS)

    @staticmethod
    def archives_minutes(symbol: str) -> bool:
        return True

    def archived(self, symbol: str) -> list[str]:
        """Intervals the archive job keeps, shortest window first (1m leaves biquote's window after ~5 h)."""
        return sorted([*ARCHIVE_INTERVALS, *MINUTE_ARCHIVE_INTERVALS], key=lambda k: INTERVALS[k].ms)

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.native_intervals = frozenset(IV)
        self._client = client or httpx.AsyncClient(timeout=15, headers={"User-Agent": "fibtrader-view"})
        self._fg = limits.AsyncBucket(FG_RATE, FG_BURST)
        self._bg = limits.AsyncBucket(BG_RATE, BG_BURST)
        self._sem = asyncio.Semaphore(4)
        self._infos = {s: self._info(s, *v) for s, v in CATALOG.items()}
        # symbol -> (mid, tick time ms, received monotonic, day change %): written by the stream
        self.ticks: dict[str, tuple[float, int, float, float | None]] = {}
        self.stream = None          # realtime.bq_ws.BiquoteStream, attached by the hub

    @staticmethod
    def _info(symbol: str, name: str, typ: str, prec: int, aliases: list[str]) -> SymbolInfo:
        session = US_SESSION if typ == _S else FX_SESSION
        tz = "America/New_York" if typ == _S else "UTC"
        return SymbolInfo(id=f"bq:{symbol}", market="bq", symbol=symbol, name=f"{name} CFD", type=typ, exchange="biquote.io",
                          currency="USD", price_precision=prec, volume_precision=0, timezone=tz, session=session,
                          aliases=[symbol, *aliases])

    # ------------------------------------------------------------------ http
    async def get(self, path: str, params: dict | list | None = None, background: bool = False):
        bucket = self._bg if background else self._fg
        attempts = 3 if background else 2
        for attempt in range(attempts):
            try:
                async with self._sem:
                    await bucket.acquire(timeout=600 if background else 8)
                    r = await self._client.get(f"{API}{path}", params=params)
                if r.status_code == 404:
                    return None
                if r.status_code == 429:
                    self._fg.drain()
                    self._bg.drain()
                    raise httpx.HTTPStatusError("rate limited", request=r.request, response=r)
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=r.request, response=r)
                if r.status_code >= 400:
                    raise RuntimeError(f"biquote {path}: HTTP {r.status_code}")
                if len(r.content) > 4 * 1024 * 1024:
                    raise RuntimeError(f"biquote {path}: answer too large")
                return r.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == attempts - 1:
                    raise
                log.debug("biquote retry %s: %s", path, exc)
                await asyncio.sleep(1.0 * 2 ** attempt)
        return None

    # --------------------------------------------------------------- symbols
    async def symbols(self) -> list[SymbolInfo]:
        return list(self._infos.values())

    async def resolve(self, symbol: str) -> SymbolInfo | None:
        return self._infos.get(symbol.upper())

    # ------------------------------------------------------------------ bars
    def history_floor(self, interval: str) -> int | None:
        # None: a fetch that starts before the window simply returns what biquote still has, and the chart cache
        # marks the series exhausted when an earlier stretch comes back empty (window_start() is only a safe bound,
        # bars older than it exist and must stay reachable)
        return None

    def window_start(self, interval: str) -> int:
        """A bar open time biquote surely still serves (its window reaches at least this far back)."""
        ivms = INTERVALS[interval].ms
        return (int(time.time() * 1000) - WINDOW_MS[interval]) // ivms * ivms

    async def fetch_bars(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Bar]:
        return await self.fetch_candles(symbol, interval, start_ms, end_ms)

    async def fetch_candles(self, symbol: str, interval: str, start_ms: int, end_ms: int,
                            background: bool = False) -> list[Bar]:
        """Bars with open time in [start, end]. biquote cannot page back: we ask for the newest N bars that reach
        `start` and cut locally. A `start` at or before window_start() means "from the window's edge": then every
        bar biquote still has comes back, older than window_start() included (it is only a safe lower bound)."""
        if symbol not in self._infos or interval not in IV:
            return []
        ivms = INTERVALS[interval].ms
        now = int(time.time() * 1000)
        start, end = int(start_ms), int(end_ms)
        if end < start:
            return []
        if start <= self.window_start(interval):
            start, n = 0, MAX_LIMIT          # from the edge of the window: everything biquote still has
        else:
            n = max(2, min(MAX_LIMIT, (now - start) // ivms + 2))
        data = await self.get(f"/{symbol}/ohlc", {"interval": IV[interval], "limit": n}, background)
        bars: dict[int, Bar] = {}
        for b in (data or {}).get("bars", []) if isinstance(data, dict) else []:
            try:
                t = iso_to_ms(b["openTime"])
                o, h, lo, c = (_f(b[k]) for k in ("open", "high", "low", "close"))
                v = _f(b.get("tickVolume")) or _f(b.get("volume")) or 0.0
            except (KeyError, TypeError, ValueError):
                continue
            key = day_bucket(t) if interval == "1D" else t     # Sunday's evening session belongs to Monday
            if None in (o, h, lo, c) or not (start <= key <= end) or t % ivms:
                continue
            bars[t] = [t, o, max(h, o, c), min(lo, o, c), c, v]
        rows = [bars[k] for k in sorted(bars)]
        return merge_sundays(rows) if interval == "1D" else rows

    # ---------------------------------------------------------------- prices
    def live_price(self, symbol: str) -> tuple[float, float | None] | None:
        """(mid, day change %) from the stream, when it is fresh."""
        hit = self.ticks.get(symbol)
        if hit is None or time.monotonic() - hit[2] > TICK_FRESH_S:
            return None
        return hit[0], hit[3]

    async def prices(self, symbols: set[str] | list[str]) -> dict[str, tuple[float, float | None]]:
        """Fresh streamed prices; the rest in one REST call. Asking also keeps those symbols on the stream."""
        want = [s for s in dict.fromkeys(symbols) if s in self._infos]
        if self.stream is not None:
            self.stream.want(want)
        out: dict[str, tuple[float, float | None]] = {}
        missing = []
        for s in want:
            hit = self.live_price(s)
            if hit is not None:
                out[s] = hit
            else:
                missing.append(s)
        if missing:
            data = await self.get("/latest", [("symbols", s) for s in missing[:100]])
            rows = data if isinstance(data, list) else (data or {}).get("ticks") or (data or {}).get("data") or []
            if isinstance(data, dict) and not rows:
                rows = [v for v in data.values() if isinstance(v, dict)]
            for r in rows:
                if not isinstance(r, dict):
                    continue
                sym, mid = r.get("symbol"), _f(r.get("mid"))
                if sym in self._infos and mid is not None and mid > 0:
                    out[sym] = (mid, _f(r.get("dayDiffPercent")))
        return out

    async def quotes(self, symbols: list[str]) -> list[Quote]:
        now = int(time.time() * 1000)
        return [Quote(id=f"bq:{s}", price=p, change_pct=ch, ts=now) for s, (p, ch) in (await self.prices(symbols)).items()]

    async def close(self) -> None:
        await self._client.aclose()
