"""Moscow Exchange via the public ISS API (keyless). Shares (TQBR) and main indices.

Docs: https://iss.moex.com/iss/reference/  (candles: /reference/155, /reference/156)
Timestamps from ISS are Moscow time; we convert to UTC ms. D/W/M bars are normalised to UTC midnight.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ..intervals import INTRADAY
from .base import Bar, Quote, SymbolInfo
from ._util import local_naive_to_ms, ms_to_local_str, utc_midnight_of_local_date

log = logging.getLogger("lv.moex")

ISS = "https://iss.moex.com/iss"
MSK = "Europe/Moscow"
INTERVAL_MAP = {"1m": 1, "1h": 60, "1D": 24, "1W": 7, "1M": 31}
PAGE = 500

SHARES_PATH = "engines/stock/markets/shares/boards/TQBR"
SHARES_CANDLES_PATH = "engines/stock/markets/shares"
INDEX_PATH = "engines/stock/markets/index/boards/SNDX"
RTSI_PATH = "engines/stock/markets/index/boards/RTSI"

# Curated indices (SECID -> board path).
INDICES = {
    "IMOEX": (INDEX_PATH, "Индекс МосБиржи"),
    "IMOEX2": (INDEX_PATH, "Индекс МосБиржи (расш. сессия)"),
    "RTSI": (RTSI_PATH, "Индекс РТС"),
    "MOEXBC": (INDEX_PATH, "Индекс голубых фишек"),
    "MCFTR": (INDEX_PATH, "Индекс МосБиржи полной доходности"),
    "MOEXOG": (INDEX_PATH, "Нефть и газ"),
    "MOEXFN": (INDEX_PATH, "Финансы"),
    "MOEXMM": (INDEX_PATH, "Металлы и добыча"),
    "MOEXIT": (INDEX_PATH, "Информационные технологии"),
    "MOEXTL": (INDEX_PATH, "Телекоммуникации"),
    "MOEXCN": (INDEX_PATH, "Потребительский сектор"),
    "MOEXEU": (INDEX_PATH, "Электроэнергетика"),
    "MOEXTN": (INDEX_PATH, "Транспорт"),
    "MOEXRE": (INDEX_PATH, "Строительство"),
    "MOEXCH": (INDEX_PATH, "Химия и нефтехимия"),
}


class MoexProvider:
    market = "moex"
    native_intervals = frozenset(INTERVAL_MAP)

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=20, headers={"User-Agent": "lobsterview/0.1"})
        self._sem = asyncio.Semaphore(6)
        self._symbols: list[SymbolInfo] | None = None
        self._symbols_ts = 0.0
        self._by_symbol: dict[str, SymbolInfo] = {}

    # ---------------------------------------------------------------- symbols
    async def symbols(self) -> list[SymbolInfo]:
        if self._symbols is not None and time.time() - self._symbols_ts < 6 * 3600:
            return self._symbols
        out: list[SymbolInfo] = []
        data = await self._get(f"{SHARES_PATH}/securities.json", {
            "iss.only": "securities",
            "securities.columns": "SECID,SHORTNAME,SECNAME,LATNAME,DECIMALS,CURRENCYID,ISSUESIZE,PREVPRICE,LISTLEVEL",
        })
        cols, rows = data["securities"]["columns"], data["securities"]["data"]
        ci = {c: i for i, c in enumerate(cols)}
        for r in rows:
            secid = r[ci["SECID"]]
            cap = float(r[ci["ISSUESIZE"]] or 0) * float(r[ci["PREVPRICE"]] or 0)
            aliases = [a for a in (r[ci["SHORTNAME"]], r[ci["SECNAME"]]) if a]
            out.append(SymbolInfo(
                id=f"moex:{secid}", market="moex", symbol=secid,
                name=r[ci["LATNAME"]] or r[ci["SECNAME"]] or r[ci["SHORTNAME"]] or secid,
                type="stock", exchange="MOEX", currency=(r[ci["CURRENCYID"]] or "RUB").replace("SUR", "RUB"),
                price_precision=int(r[ci["DECIMALS"]] or 2), volume_precision=0,
                timezone=MSK, session="0700-2350",
                aliases=aliases,
                extra={"path": SHARES_PATH, "cap": cap, "list_level": r[ci["LISTLEVEL"]]},
            ))
        for secid, (path, name) in INDICES.items():
            out.append(SymbolInfo(
                id=f"moex:{secid}", market="moex", symbol=secid, name=name, type="index", exchange="MOEX",
                currency="RUB" if secid != "RTSI" else "USD", price_precision=2, volume_precision=0,
                timezone=MSK, session="1000-1850", extra={"path": path},
            ))
        self._symbols, self._symbols_ts = out, time.time()
        self._by_symbol = {s.symbol: s for s in out}
        log.info("moex: %d instruments", len(out))
        return out

    async def resolve(self, symbol: str) -> SymbolInfo | None:
        await self.symbols()
        return self._by_symbol.get(symbol.upper())

    async def top_by_cap(self, n: int = 30) -> list[str]:
        syms = [s for s in await self.symbols() if s.type == "stock"]
        syms.sort(key=lambda s: s.extra.get("cap") or 0, reverse=True)
        return [s.symbol for s in syms[:n]]

    # ------------------------------------------------------------------- bars
    def history_floor(self, interval: str) -> int | None:
        return None

    async def fetch_bars(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Bar]:
        info = await self.resolve(symbol)
        if info is None:
            raise ValueError(f"unknown MOEX symbol {symbol}")
        # Shares: market-level candles = the security's primary board (TQBR for stocks, TQTF for funds such as
        # CASH/BCSD) (the main board series). Indices keep their explicit board.
        path = SHARES_CANDLES_PATH if info.type == "stock" else info.extra["path"]
        iv = INTERVAL_MAP[interval]
        frm = ms_to_local_str(start_ms, MSK)
        till = ms_to_local_str(end_ms, MSK)
        out: list[Bar] = []
        offset = 0
        for _ in range(400):
            data = await self._get(f"{path}/securities/{symbol}/candles.json",
                                   {"interval": iv, "from": frm, "till": till, "start": offset})
            block = data.get("candles", {})
            cols, rows = block.get("columns", []), block.get("data", [])
            if not rows:
                break
            ci = {c: i for i, c in enumerate(cols)}
            for r in rows:
                try:
                    ts = local_naive_to_ms(r[ci["begin"]], MSK)
                    if interval not in INTRADAY:
                        ts = utc_midnight_of_local_date(ts, MSK)
                    vol = r[ci["volume"]]
                    if vol is None:
                        vol = r[ci["value"]] or 0
                    out.append([ts, float(r[ci["open"]]), float(r[ci["high"]]), float(r[ci["low"]]),
                                float(r[ci["close"]]), float(vol)])
                except (TypeError, ValueError, KeyError):
                    continue
            if len(rows) < PAGE:
                break
            offset += len(rows)
        out = [b for b in out if start_ms <= b[0] <= end_ms + (0 if interval in INTRADAY else 86_400_000)]
        out.sort(key=lambda b: b[0])
        return out

    # ----------------------------------------------------------------- quotes
    async def quotes(self, symbols: list[str]) -> list[Quote]:
        await self.symbols()
        shares = [s for s in symbols if s in self._by_symbol and self._by_symbol[s].type == "stock"]
        idx = [s for s in symbols if s in self._by_symbol and self._by_symbol[s].type == "index"]
        out: list[Quote] = []
        now = int(time.time() * 1000)
        if shares:
            data = await self._get(f"{SHARES_PATH}/securities.json", {
                "securities": ",".join(shares), "iss.only": "marketdata",
                "marketdata.columns": "SECID,LAST,LASTTOPREVPRICE,UPDATETIME",
            })
            cols, rows = data["marketdata"]["columns"], data["marketdata"]["data"]
            ci = {c: i for i, c in enumerate(cols)}
            for r in rows:
                if r[ci["LAST"]] is None:
                    continue
                out.append(Quote(id=f"moex:{r[ci['SECID']]}", price=float(r[ci["LAST"]]),
                                 change_pct=r[ci["LASTTOPREVPRICE"]], ts=now))
        for board in {self._by_symbol[s].extra["path"] for s in idx}:
            secs = [s for s in idx if self._by_symbol[s].extra["path"] == board]
            data = await self._get(f"{board}/securities.json", {
                "securities": ",".join(secs), "iss.only": "marketdata",
                "marketdata.columns": "SECID,CURRENTVALUE,LASTCHANGEPRC",
            })
            cols, rows = data["marketdata"]["columns"], data["marketdata"]["data"]
            ci = {c: i for i, c in enumerate(cols)}
            for r in rows:
                if r[ci["CURRENTVALUE"]] is None:
                    continue
                out.append(Quote(id=f"moex:{r[ci['SECID']]}", price=float(r[ci["CURRENTVALUE"]]),
                                 change_pct=r[ci["LASTCHANGEPRC"]], ts=now))
        return out

    # ------------------------------------------------------------------- http
    async def _get(self, path: str, params: dict) -> dict:
        params = {"iss.meta": "off", **params}
        async with self._sem:
            for attempt in range(4):
                try:
                    r = await self._client.get(f"{ISS}/{path}", params=params)
                    if r.status_code == 429 or r.status_code >= 500:
                        raise httpx.HTTPStatusError("retry", request=r.request, response=r)
                    r.raise_for_status()
                    return r.json()
                except (httpx.HTTPError, ValueError) as exc:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(0.7 * 2 ** attempt)
                    log.debug("moex retry %s: %s", path, exc)
        return {}
