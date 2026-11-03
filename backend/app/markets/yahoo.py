"""Yahoo Finance via yfinance: indices, commodity futures, FX and non-US stocks. Delayed, unofficial.

Depth limits (per Yahoo): 1m -> last 30 days (7 days per request), 5m/15m/30m -> 60 days,
1h -> 730 days, D/W/M -> full history. yfinance is synchronous -> run in a thread.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore", module="yfinance")

from .. import config
from ..intervals import DAY, INTRADAY
from .base import Bar, Quote, SymbolInfo
from ._util import utc_midnight_of_local_date

log = logging.getLogger("lv.yahoo")

YF_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "1D": "1d", "1W": "1wk", "1M": "1mo"}
DEPTH_DAYS = {"1m": 29, "5m": 59, "15m": 59, "30m": 59, "1h": 729}
CHUNK_DAYS = {"1m": 7, "5m": 59, "15m": 59, "30m": 59, "1h": 729}

# (ticker, name, type, exchange, currency, price_precision, timezone, aliases)
# Familiar exchange-style names for Yahoo's cryptic tickers (indices ^…, continuous futures …=F, FX …=X).
DISPLAY = {
    "^GSPC": "SPX", "^NDX": "NDX", "^IXIC": "IXIC", "^DJI": "DJI", "^RUT": "RUT", "^VIX": "VIX",
    "^GDAXI": "DAX", "^FTSE": "FTSE", "^FCHI": "CAC40", "^STOXX50E": "SX5E", "^N225": "NI225", "^HSI": "HSI",
    "000001.SS": "SHCOMP", "^KS11": "KOSPI", "^BSESN": "SENSEX", "DX-Y.NYB": "DXY",
}


def display_name(ticker: str) -> str:
    if ticker in DISPLAY:
        return DISPLAY[ticker]
    if ticker.endswith("=F"):          # continuous front month, the usual GC1! notation
        return ticker[:-2] + "1!"
    if ticker.endswith("=X"):          # Yahoo FX: EURUSD=X, and USD bases as JPY=X
        base = ticker[:-2]
        return base if len(base) == 6 else "USD" + base
    if ticker.startswith("^"):
        return ticker[1:]
    return ticker


CURATED: list[tuple] = [
    # --- indices
    ("^GSPC", "S&P 500", "index", "SP", "USD", 2, "America/New_York", ["SPX", "SP500", "US500", "S&P", "S&P500", "SNP", "SNP500"]),
    ("^NDX", "Nasdaq 100", "index", "NASDAQ", "USD", 2, "America/New_York", ["NDX", "US100", "NAS100", "NASDAQ100"]),
    ("^IXIC", "Nasdaq Composite", "index", "NASDAQ", "USD", 2, "America/New_York", ["NASDAQ", "COMP"]),
    ("^DJI", "Dow Jones Industrial Average", "index", "DJ", "USD", 2, "America/New_York", ["DJI", "DOW", "US30"]),
    ("^RUT", "Russell 2000", "index", "RUSSELL", "USD", 2, "America/New_York", ["RUT", "US2000"]),
    ("^VIX", "CBOE Volatility Index", "index", "CBOE", "USD", 2, "America/New_York", ["VIX"]),
    ("^GDAXI", "DAX 40", "index", "XETRA", "EUR", 2, "Europe/Berlin", ["DAX", "GER40"]),
    ("^FTSE", "FTSE 100", "index", "LSE", "GBP", 2, "Europe/London", ["FTSE", "UK100"]),
    ("^FCHI", "CAC 40", "index", "EURONEXT", "EUR", 2, "Europe/Paris", ["CAC", "CAC40", "FRA40"]),
    ("^STOXX50E", "Euro Stoxx 50", "index", "STOXX", "EUR", 2, "Europe/Berlin", ["STOXX50", "SX5E"]),
    ("^N225", "Nikkei 225", "index", "TSE", "JPY", 2, "Asia/Tokyo", ["NIKKEI", "N225", "JPN225"]),
    ("^HSI", "Hang Seng", "index", "HKEX", "HKD", 2, "Asia/Hong_Kong", ["HSI", "HK50"]),
    ("000001.SS", "Shanghai Composite", "index", "SSE", "CNY", 2, "Asia/Shanghai", ["SSEC", "SHCOMP"]),
    ("^KS11", "KOSPI", "index", "KRX", "KRW", 2, "Asia/Seoul", ["KOSPI"]),
    ("^BSESN", "BSE Sensex", "index", "BSE", "INR", 2, "Asia/Kolkata", ["SENSEX"]),
    ("DX-Y.NYB", "US Dollar Index", "index", "ICE", "USD", 3, "America/New_York", ["DXY"]),
    # --- commodities (continuous front-month futures)
    ("GC=F", "Gold", "future", "COMEX", "USD", 2, "America/New_York", ["GOLD", "XAUUSD", "XAU"]),
    ("SI=F", "Silver", "future", "COMEX", "USD", 3, "America/New_York", ["SILVER", "XAGUSD", "XAG"]),
    ("PL=F", "Platinum", "future", "NYMEX", "USD", 2, "America/New_York", ["PLATINUM", "XPTUSD"]),
    ("PA=F", "Palladium", "future", "NYMEX", "USD", 2, "America/New_York", ["PALLADIUM", "XPDUSD"]),
    ("HG=F", "Copper", "future", "COMEX", "USD", 4, "America/New_York", ["COPPER"]),
    ("CL=F", "Crude Oil WTI", "future", "NYMEX", "USD", 2, "America/New_York", ["WTI", "OIL", "USOIL"]),
    ("BZ=F", "Brent Crude Oil", "future", "ICE", "USD", 2, "America/New_York", ["BRENT", "UKOIL"]),
    ("NG=F", "Natural Gas", "future", "NYMEX", "USD", 3, "America/New_York", ["NATGAS", "NG"]),
    ("RB=F", "RBOB Gasoline", "future", "NYMEX", "USD", 4, "America/New_York", ["GASOLINE"]),
    ("HO=F", "Heating Oil", "future", "NYMEX", "USD", 4, "America/New_York", ["HEATINGOIL"]),
    ("ZW=F", "Wheat", "future", "CBOT", "USD", 2, "America/Chicago", ["WHEAT"]),
    ("ZC=F", "Corn", "future", "CBOT", "USD", 2, "America/Chicago", ["CORN"]),
    ("ZS=F", "Soybeans", "future", "CBOT", "USD", 2, "America/Chicago", ["SOYBEANS", "SOY"]),
    ("KC=F", "Coffee", "future", "ICE", "USD", 2, "America/New_York", ["COFFEE"]),
    ("SB=F", "Sugar #11", "future", "ICE", "USD", 2, "America/New_York", ["SUGAR"]),
    ("CC=F", "Cocoa", "future", "ICE", "USD", 0, "America/New_York", ["COCOA"]),
    ("CT=F", "Cotton", "future", "ICE", "USD", 2, "America/New_York", ["COTTON"]),
    ("LE=F", "Live Cattle", "future", "CME", "USD", 3, "America/Chicago", ["CATTLE"]),
    ("ZN=F", "10-Year T-Note", "future", "CBOT", "USD", 3, "America/Chicago", ["TNOTE", "ZN"]),
    # --- FX
    ("EURUSD=X", "EUR/USD", "currency", "FX", "USD", 5, "UTC", ["EURUSD"]),
    ("GBPUSD=X", "GBP/USD", "currency", "FX", "USD", 5, "UTC", ["GBPUSD"]),
    ("JPY=X", "USD/JPY", "currency", "FX", "JPY", 3, "UTC", ["USDJPY"]),
    ("CHF=X", "USD/CHF", "currency", "FX", "CHF", 5, "UTC", ["USDCHF"]),
    ("AUDUSD=X", "AUD/USD", "currency", "FX", "USD", 5, "UTC", ["AUDUSD"]),
    ("CAD=X", "USD/CAD", "currency", "FX", "CAD", 5, "UTC", ["USDCAD"]),
    ("CNY=X", "USD/CNY", "currency", "FX", "CNY", 4, "UTC", ["USDCNY"]),
    ("RUB=X", "USD/RUB", "currency", "FX", "RUB", 4, "UTC", ["USDRUB"]),
    ("EURRUB=X", "EUR/RUB", "currency", "FX", "RUB", 4, "UTC", ["EURRUB"]),
    ("CNYRUB=X", "CNY/RUB", "currency", "FX", "RUB", 4, "UTC", ["CNYRUB"]),
    # --- global large caps (non-US)
    ("ASML.AS", "ASML Holding", "stock", "EURONEXT", "EUR", 2, "Europe/Amsterdam", []),
    ("SAP.DE", "SAP", "stock", "XETRA", "EUR", 2, "Europe/Berlin", []),
    ("SIE.DE", "Siemens", "stock", "XETRA", "EUR", 2, "Europe/Berlin", []),
    ("MC.PA", "LVMH", "stock", "EURONEXT", "EUR", 2, "Europe/Paris", ["LVMH"]),
    ("OR.PA", "L'Oreal", "stock", "EURONEXT", "EUR", 2, "Europe/Paris", []),
    ("TTE.PA", "TotalEnergies", "stock", "EURONEXT", "EUR", 2, "Europe/Paris", []),
    ("NESN.SW", "Nestle", "stock", "SIX", "CHF", 2, "Europe/Zurich", []),
    ("NOVN.SW", "Novartis", "stock", "SIX", "CHF", 2, "Europe/Zurich", []),
    ("ROG.SW", "Roche", "stock", "SIX", "CHF", 2, "Europe/Zurich", []),
    ("NOVO-B.CO", "Novo Nordisk", "stock", "OMX", "DKK", 2, "Europe/Copenhagen", ["NOVO"]),
    ("SHEL.L", "Shell", "stock", "LSE", "GBp", 2, "Europe/London", []),
    ("AZN.L", "AstraZeneca", "stock", "LSE", "GBp", 2, "Europe/London", []),
    ("HSBA.L", "HSBC", "stock", "LSE", "GBp", 2, "Europe/London", []),
    ("BP.L", "BP", "stock", "LSE", "GBp", 2, "Europe/London", []),
    ("RIO.L", "Rio Tinto", "stock", "LSE", "GBp", 2, "Europe/London", []),
    ("7203.T", "Toyota Motor", "stock", "TSE", "JPY", 0, "Asia/Tokyo", ["TOYOTA"]),
    ("6758.T", "Sony Group", "stock", "TSE", "JPY", 0, "Asia/Tokyo", ["SONY"]),
    ("8035.T", "Tokyo Electron", "stock", "TSE", "JPY", 0, "Asia/Tokyo", []),
    ("9984.T", "SoftBank Group", "stock", "TSE", "JPY", 0, "Asia/Tokyo", ["SOFTBANK"]),
    ("005930.KS", "Samsung Electronics", "stock", "KRX", "KRW", 0, "Asia/Seoul", ["SAMSUNG"]),
    ("000660.KS", "SK hynix", "stock", "KRX", "KRW", 0, "Asia/Seoul", []),
    ("2330.TW", "TSMC", "stock", "TWSE", "TWD", 1, "Asia/Taipei", ["TSMC"]),
    ("0700.HK", "Tencent", "stock", "HKEX", "HKD", 2, "Asia/Hong_Kong", ["TENCENT"]),
    ("9988.HK", "Alibaba", "stock", "HKEX", "HKD", 2, "Asia/Hong_Kong", []),
    ("3690.HK", "Meituan", "stock", "HKEX", "HKD", 2, "Asia/Hong_Kong", []),
    ("1810.HK", "Xiaomi", "stock", "HKEX", "HKD", 2, "Asia/Hong_Kong", ["XIAOMI"]),
    ("1211.HK", "BYD", "stock", "HKEX", "HKD", 2, "Asia/Hong_Kong", ["BYD"]),
    ("RELIANCE.NS", "Reliance Industries", "stock", "NSE", "INR", 2, "Asia/Kolkata", []),
    ("TCS.NS", "Tata Consultancy", "stock", "NSE", "INR", 2, "Asia/Kolkata", []),
    ("BHP.AX", "BHP Group", "stock", "ASX", "AUD", 2, "Australia/Sydney", []),
    ("VALE3.SA", "Vale", "stock", "B3", "BRL", 2, "America/Sao_Paulo", []),
    ("PETR4.SA", "Petrobras", "stock", "B3", "BRL", 2, "America/Sao_Paulo", []),
    ("ARAMCO.SR", "Saudi Aramco", "stock", "TADAWUL", "SAR", 2, "Asia/Riyadh", ["ARAMCO"]),
]


def _precision_for_price(p: float, quote_type: str) -> int:
    if quote_type == "CURRENCY":
        return 3 if p > 50 else 5
    if p >= 1000:
        return 2
    if p >= 10:
        return 2
    if p >= 1:
        return 3
    return 4


class YahooProvider:
    market = "yahoo"
    native_intervals = frozenset(YF_INTERVAL)

    def __init__(self) -> None:
        import yfinance as yf
        self._yf = yf
        cache = config.DATA_DIR / "yf_cache"
        cache.mkdir(parents=True, exist_ok=True)
        try:
            yf.set_tz_cache_location(str(cache))
        except Exception:  # pragma: no cover - older yfinance
            pass
        self._symbols = [self._info(*row) for row in CURATED]
        self._by_symbol = {s.symbol: s for s in self._symbols}
        self._resolved: dict[str, SymbolInfo | None] = {}

    @staticmethod
    def _info(ticker, name, typ, exchange, currency, prec, tz, aliases) -> SymbolInfo:
        disp = display_name(ticker)
        al = list(aliases) + ([disp] if disp != ticker and disp not in aliases else [])
        return SymbolInfo(id=f"yahoo:{ticker}", market="yahoo", symbol=ticker, name=name, type=typ,
                          exchange=exchange, currency=currency, price_precision=prec, volume_precision=0,
                          timezone=tz, session="24x7" if typ == "currency" else "exchange", aliases=al,
                          display=disp)

    # ---------------------------------------------------------------- symbols
    async def symbols(self) -> list[SymbolInfo]:
        return self._symbols

    async def resolve(self, symbol: str) -> SymbolInfo | None:
        if symbol in self._by_symbol:
            return self._by_symbol[symbol]
        key = symbol.upper()
        if key in self._resolved:
            return self._resolved[key]
        info = await asyncio.to_thread(self._resolve_sync, symbol)
        self._resolved[key] = info
        if info:
            self._by_symbol[info.symbol] = info
        return info

    def _resolve_sync(self, symbol: str) -> SymbolInfo | None:
        try:
            t = self._yf.Ticker(symbol)
            fi = t.fast_info
            price = fi.get("lastPrice") if hasattr(fi, "get") else getattr(fi, "last_price", None)
            if price is None or (isinstance(price, float) and math.isnan(price)):
                return None
            qt = (fi.get("quoteType") if hasattr(fi, "get") else getattr(fi, "quote_type", "")) or ""
            tzname = (fi.get("timezone") if hasattr(fi, "get") else getattr(fi, "timezone", "UTC")) or "UTC"
            cur = (fi.get("currency") if hasattr(fi, "get") else getattr(fi, "currency", "USD")) or "USD"
            exch = (fi.get("exchange") if hasattr(fi, "get") else getattr(fi, "exchange", "")) or "YAHOO"
            name = symbol
            try:
                info = t.get_info()
                name = info.get("shortName") or info.get("longName") or symbol
            except Exception:
                pass
            typ = {"EQUITY": "stock", "ETF": "etf", "INDEX": "index", "FUTURE": "future",
                   "CURRENCY": "currency", "MUTUALFUND": "fund", "CRYPTOCURRENCY": "crypto"}.get(qt.upper(), "stock")
            return SymbolInfo(id=f"yahoo:{symbol}", market="yahoo", symbol=symbol, name=name, type=typ,
                              exchange=str(exch), currency=str(cur), price_precision=_precision_for_price(float(price), qt.upper()),
                              volume_precision=0, timezone=str(tzname), session="24x7" if typ == "currency" else "exchange",
                              display=display_name(symbol))
        except Exception as exc:
            log.info("yahoo resolve %s failed: %s", symbol, exc)
            return None

    async def search(self, query: str, limit: int = 10) -> list[SymbolInfo]:
        """Name/ticker search through Yahoo's search endpoint (for global stocks not in the curated list)."""
        def _run() -> list[SymbolInfo]:
            try:
                res = self._yf.Search(query, max_results=limit, news_count=0)
                quotes = res.quotes or []
            except Exception as exc:
                log.info("yahoo search '%s' failed: %s", query, exc)
                return []
            out = []
            for q in quotes:
                sym = q.get("symbol")
                if not sym:
                    continue
                qt = (q.get("quoteType") or "").upper()
                typ = {"EQUITY": "stock", "ETF": "etf", "INDEX": "index", "FUTURE": "future",
                       "CURRENCY": "currency", "MUTUALFUND": "fund", "CRYPTOCURRENCY": "crypto"}.get(qt, "stock")
                if typ == "crypto":
                    continue
                out.append(SymbolInfo(id=f"yahoo:{sym}", market="yahoo", symbol=sym,
                                      name=q.get("shortname") or q.get("longname") or sym, type=typ,
                                      exchange=q.get("exchDisp") or q.get("exchange") or "YAHOO",
                                      currency="", price_precision=2, volume_precision=0, timezone="UTC",
                                      session="exchange", display=display_name(sym)))
            return out
        return await asyncio.to_thread(_run)

    # ------------------------------------------------------------------- bars
    def history_floor(self, interval: str) -> int | None:
        days = DEPTH_DAYS.get(interval)
        if days is None:
            return None
        return int(time.time() * 1000) - days * DAY

    async def fetch_bars(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Bar]:
        info = await self.resolve(symbol)
        tzname = info.timezone if info else "UTC"
        return await asyncio.to_thread(self._fetch_sync, symbol, interval, start_ms, end_ms, tzname)

    def _fetch_sync(self, symbol: str, interval: str, start_ms: int, end_ms: int, tzname: str) -> list[Bar]:
        import pandas as pd
        yf_iv = YF_INTERVAL[interval]
        chunk_days = CHUNK_DAYS.get(interval)
        bars: dict[int, Bar] = {}
        t = self._yf.Ticker(symbol)
        intraday = interval in INTRADAY
        # Yahoo's `end` is exclusive and daily bars need a full extra day.
        s, e = start_ms, end_ms + (DAY if not intraday else 60_000)
        windows = []
        if chunk_days:
            step = chunk_days * DAY
            cur = s
            while cur < e:
                windows.append((cur, min(cur + step, e)))
                cur += step
        else:
            windows.append((s, e))
        for ws, we in windows:
            try:
                df = t.history(start=datetime.fromtimestamp(ws / 1000, tz=timezone.utc),
                               end=datetime.fromtimestamp(we / 1000, tz=timezone.utc),
                               interval=yf_iv, auto_adjust=False, prepost=False, actions=False, raise_errors=False)
            except Exception as exc:
                log.warning("yahoo %s %s failed: %s", symbol, yf_iv, exc)
                continue
            if df is None or df.empty:
                continue
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            idx = df.index
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
            for ts, row in zip(idx, df.itertuples(index=False)):
                ms = int(pd.Timestamp(ts).timestamp() * 1000)
                if not intraday:
                    ms = utc_midnight_of_local_date(ms, tzname)
                vol = getattr(row, "Volume", 0.0)
                if vol is None or (isinstance(vol, float) and math.isnan(vol)):
                    vol = 0.0
                bars[ms] = [ms, float(row.Open), float(row.High), float(row.Low), float(row.Close), float(vol)]
        return [bars[k] for k in sorted(bars) if start_ms <= k <= end_ms + (DAY if not intraday else 0)]

    # ----------------------------------------------------------------- quotes
    async def quotes(self, symbols: list[str]) -> list[Quote]:
        def _run() -> list[Quote]:
            out = []
            now = int(time.time() * 1000)
            for sym in symbols:
                try:
                    fi = self._yf.Ticker(sym).fast_info
                    price = fi.get("lastPrice") if hasattr(fi, "get") else getattr(fi, "last_price", None)
                    prev = fi.get("previousClose") if hasattr(fi, "get") else getattr(fi, "previous_close", None)
                    if price is None or math.isnan(price):
                        continue
                    ch = (price / prev - 1) * 100 if prev else None
                    out.append(Quote(id=f"yahoo:{sym}", price=float(price), change_pct=ch, ts=now))
                except Exception as exc:
                    log.debug("yahoo quote %s failed: %s", sym, exc)
            return out
        return await asyncio.to_thread(_run)
