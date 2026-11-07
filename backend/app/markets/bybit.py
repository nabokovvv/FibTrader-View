"""Bybit v5 public REST adapter (linear USDT/USDC perpetuals and spot). Keyless."""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .. import limits
from .base import Bar, Quote, SymbolInfo, precision_from_tick

log = logging.getLogger("lv.bybit")

BASE = "https://api.bybit.com"
INTERVAL_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "1D": "D", "1W": "W", "1M": "M",
}
MAX_LIMIT = 1000


# transient server-side errors / rate limits: 10000 timeout, 10002 time window, 10006 rate limit, 10016 svc error
RETRY_CODES = {10000, 10002, 10006, 10016}


# Bybit `symbolType` of linear perpetuals -> our symbol type (anything else is crypto)
SYMBOL_TYPE = {"stock": "stock", "etf": "etf", "commodity": "commodity", "forex": "forex"}


class BybitProvider:
    def __init__(self, category: str = "linear", client: httpx.AsyncClient | None = None):
        assert category in ("linear", "spot")
        self.category = category
        self.market = "bybit" if category == "linear" else "bybit-spot"
        self.native_intervals = frozenset(INTERVAL_MAP)
        self._client = client or httpx.AsyncClient(timeout=15, headers={"User-Agent": "lobsterview/0.1"})
        self._symbols: list[SymbolInfo] | None = None
        self._symbols_ts = 0.0

    # ---------------------------------------------------------------- symbols
    async def symbols(self) -> list[SymbolInfo]:
        if self._symbols is not None and time.time() - self._symbols_ts < 6 * 3600:
            return self._symbols
        out: list[SymbolInfo] = []
        cursor = ""
        while True:
            params = {"category": self.category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            data = await self._get("/v5/market/instruments-info", params)
            for it in data.get("list", []):
                if it.get("status") != "Trading":
                    continue
                quote = it.get("quoteCoin", "")
                if self.category == "linear":
                    if it.get("contractType") != "LinearPerpetual" or quote not in ("USDT", "USDC"):
                        continue
                    name = f"{it['baseCoin']} / {quote} Perpetual"
                    # stock / ETF / commodity / forex perpetuals are tradfi; "" and "innovation" are crypto
                    sym_type = SYMBOL_TYPE.get(str(it.get("symbolType") or "").lower(), "crypto")
                else:
                    if quote not in ("USDT", "USDC", "BTC", "ETH", "EUR"):
                        continue
                    name = f"{it['baseCoin']} / {quote} Spot"
                    sym_type = "crypto"
                pf = it.get("priceFilter", {})
                lf = it.get("lotSizeFilter", {})
                out.append(SymbolInfo(
                    id=f"{self.market}:{it['symbol']}",
                    market=self.market,
                    symbol=it["symbol"],
                    name=name,
                    type=sym_type,
                    exchange="BYBIT",
                    currency=quote,
                    price_precision=precision_from_tick(pf.get("tickSize"), 4),
                    volume_precision=precision_from_tick(lf.get("qtyStep") or lf.get("basePrecision"), 3),
                    timezone="UTC",
                    session="24x7",
                    aliases=[it["baseCoin"]] if quote == "USDT" else [],
                ))
            cursor = data.get("nextPageCursor") or ""
            if not cursor:
                break
        self._symbols, self._symbols_ts = out, time.time()
        log.info("%s: %d instruments", self.market, len(out))
        return out

    async def resolve(self, symbol: str) -> SymbolInfo | None:
        symbol = symbol.upper()
        for s in await self.symbols():
            if s.symbol == symbol:
                return s
        return None

    # ------------------------------------------------------------------- bars
    def history_floor(self, interval: str) -> int | None:
        return None

    async def fetch_bars(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Bar]:
        iv = INTERVAL_MAP[interval]
        bars: dict[int, Bar] = {}
        end = end_ms
        for _ in range(200):  # hard stop: 200k bars per call
            params = {
                "category": self.category, "symbol": symbol, "interval": iv,
                "start": start_ms, "end": end, "limit": MAX_LIMIT,
            }
            data = await self._get("/v5/market/kline", params)
            rows = data.get("list", [])
            if not rows:
                break
            for r in rows:  # newest first
                ts = int(r[0])
                bars[ts] = [ts, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
            oldest = int(rows[-1][0])
            if len(rows) < MAX_LIMIT or oldest <= start_ms:
                break
            end = oldest - 1
        return [bars[k] for k in sorted(bars)]

    # ----------------------------------------------------------------- quotes
    async def tickers(self) -> list[dict]:
        data = await self._get("/v5/market/tickers", {"category": self.category})
        return data.get("list", [])

    async def quotes(self, symbols: list[str]) -> list[Quote]:
        want = set(symbols)
        now = int(time.time() * 1000)
        out = []
        for t in await self.tickers():
            if t["symbol"] in want:
                try:
                    out.append(Quote(
                        id=f"{self.market}:{t['symbol']}",
                        price=float(t["lastPrice"]),
                        change_pct=float(t.get("price24hPcnt") or 0) * 100,
                        ts=now,
                    ))
                except (KeyError, ValueError):
                    continue
        return out

    async def top_by_turnover(self, n: int = 30) -> list[str]:
        rows = await self.tickers()
        rows = [r for r in rows if r["symbol"].endswith("USDT")]
        rows.sort(key=lambda r: float(r.get("turnover24h") or 0), reverse=True)
        return [r["symbol"] for r in rows[:n]]

    # ------------------------------------------------------------------- http
    async def _get(self, path: str, params: dict) -> dict:
        async with limits.bybit_semaphore():
            for attempt in range(4):
                await limits.bybit_bucket().acquire()  # one outbound budget for linear + spot (raises Busy)
                try:
                    r = await self._client.get(BASE + path, params=params)
                    if r.status_code == 429 or r.status_code >= 500:
                        raise httpx.HTTPStatusError("retry", request=r.request, response=r)
                    r.raise_for_status()
                    j = r.json()
                    if j.get("retCode") in RETRY_CODES and attempt < 3:
                        raise httpx.HTTPStatusError(f"bybit busy {j.get('retCode')}", request=r.request, response=r)
                    if j.get("retCode") != 0:
                        raise RuntimeError(f"bybit {path}: {j.get('retCode')} {j.get('retMsg')}")
                    return j.get("result", {})
                except (httpx.HTTPError, ValueError) as exc:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(0.5 * 2 ** attempt)
                    log.debug("bybit retry %s: %s", path, exc)
        return {}
