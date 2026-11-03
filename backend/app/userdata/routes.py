"""User data API: drawings (per symbol), watchlists, layouts, settings — always scoped to the caller's user id.

Limits per account come from app/limits.py (public instance: small; private: generous). The request body is
already capped by the Guard middleware before JSON parsing. SQLite work runs in a small dedicated thread pool so it
never blocks the event loop (or the candle reads in the default pool).
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, StringConstraints

from .. import limits
from ..auth import user_id
from .db import LimitError, Quota

log = logging.getLogger("lv.userdata")
router = APIRouter()

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="userdb")
SymbolId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ShortName = Annotated[str, StringConstraints(max_length=80)]


async def db(request: Request, fn: str, *args, **kw):
    try:
        return await call(request.app.state.userdb, fn, *args, **kw)
    except LimitError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


async def call(userdb, fn: str, *args, **kw):
    """Run a UserDB method in the user-data thread pool (for background tasks without a request)."""
    return await asyncio.get_running_loop().run_in_executor(_pool, partial(getattr(userdb, fn), *args, **kw))


def quota() -> Quota:
    p = lim()
    return Quota(user_bytes=p.user_bytes, db_bytes=p.app_db_bytes)


def lim() -> limits.Profile:
    return limits.profile()


def _payload(value: Any, limit: int, what: str) -> str:
    """Validated JSON text of `value` (exactly what gets stored, so sizes match)."""
    if not limits.json_depth_ok(value):
        raise HTTPException(400, f"{what} is nested too deeply")
    text = limits.dumps(value)
    if len(text.encode()) > limit:
        raise HTTPException(413, f"{what} is too large")
    return text


DEFAULT_WATCHLISTS: list[tuple[str, list[str]]] = [
    ("Crypto", []),  # filled from Bybit top-30 by turnover on first run
    ("US Stocks", [f"us:{s}" for s in ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK.B", "JPM",
                                        "LLY", "V", "UNH", "XOM", "MA", "COST", "NFLX", "AMD", "PLTR", "COIN"]]),
    ("Indices", [f"yahoo:{s}" for s in ["^GSPC", "^NDX", "^DJI", "^RUT", "^VIX", "^GDAXI", "^FTSE", "^STOXX50E", "^N225",
                                        "^HSI", "DX-Y.NYB"]] + ["moex:IMOEX", "moex:RTSI"]),
    ("Commodities", [f"yahoo:{s}" for s in ["GC=F", "SI=F", "PL=F", "HG=F", "CL=F", "BZ=F", "NG=F", "ZW=F", "ZC=F", "ZS=F",
                                            "KC=F", "SB=F", "CC=F"]]),
    ("MOEX", [f"moex:{s}" for s in ["SBER", "GAZP", "LKOH", "GMKN", "YDEX", "ROSN", "NVTK", "TATN", "SNGS", "PLZL", "MGNT",
                                    "VTBR", "ALRS", "CHMF", "NLMK", "MTSS", "MOEX", "OZON", "T", "AFLT", "PHOR", "RUAL",
                                    "IRAO", "HYDR", "POSI"]]),
    ("FX", [f"yahoo:{s}" for s in ["EURUSD=X", "GBPUSD=X", "JPY=X", "CHF=X", "AUDUSD=X", "CAD=X", "CNY=X", "RUB=X",
                                   "EURRUB=X", "CNYRUB=X"]]),
]
# Where the exchange-data markets are off (the public instance), the same lists come from Hyperliquid perpetuals.
HL_LISTS: dict[str, list[str]] = {
    "US Stocks": [f"hl:xyz:{s}" for s in ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "TSM", "ORCL",
                                          "AMD", "MU", "PLTR", "NFLX", "LLY", "COST", "COIN", "HOOD", "MSTR", "INTC"]],
    "Indices": [f"hl:xyz:{s}" for s in ["SP500", "XYZ100", "JP225", "KR200", "NIFTY"]] + ["hl:para:10Y", "hl:para:2Y"],
    "Commodities": [f"hl:xyz:{s}" for s in ["GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER", "CL", "BRENTOIL",
                                            "NATGAS"]],
    "FX": [f"hl:xyz:{s}" for s in ["EUR", "GBP", "JPY"]],
}
FALLBACK_CRYPTO = [f"bybit:{s}USDT" for s in ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "SUI", "LINK", "AVAX",
                                               "HYPE", "TON", "TRX", "LTC", "DOT", "BCH", "PEPE", "WIF", "ARB", "OP"]]

_top: tuple[float, list[str]] = (0.0, [])          # shared Bybit top-30, refreshed hourly
_seed_locks: dict[str, asyncio.Lock] = {}


async def _top_crypto(request: Request) -> list[str]:
    global _top
    if _top[1] and time.monotonic() - _top[0] < 3600:
        return _top[1]
    bybit = request.app.state.providers.get("bybit")
    crypto = FALLBACK_CRYPTO
    if bybit is not None:
        try:
            crypto = [f"bybit:{s}" for s in await bybit.top_by_turnover(30)]
        except Exception as exc:  # noqa: BLE001
            log.warning("seed: bybit top failed: %s", exc)
            return _top[1] or FALLBACK_CRYPTO
    _top = (time.monotonic(), crypto)
    return crypto


async def _seed_watchlists(request: Request, uid: str) -> None:
    if await db(request, "watchlists", uid):
        return
    lock = _seed_locks.setdefault(uid, asyncio.Lock())
    try:
        async with lock:
            if await db(request, "watchlists", uid):  # another request seeded meanwhile
                return
            crypto = await _top_crypto(request)
            markets = request.app.state.providers
            hl_listed: set[str] = set()
            if "hl" in markets:
                try:
                    hl_listed = {i.id for i in await markets["hl"].symbols()}
                except Exception as exc:  # noqa: BLE001
                    log.warning("seed: hyperliquid markets failed: %s", exc)
            lists = []
            for name, items in DEFAULT_WATCHLISTS:
                if name == "Crypto":
                    items = crypto
                items = [i for i in items if i.split(":", 1)[0] in markets]
                if not items:
                    items = [i for i in HL_LISTS.get(name, []) if i in hl_listed]
                items = items[: lim().watchlist_items]
                if items:
                    lists.append((name, items))
            if await db(request, "seed_watchlists_checked", uid, lists, max_lists=lim().watchlists, quota=quota()):
                log.info("seeded default watchlists for %s", uid)
    finally:
        if not lock.locked():
            _seed_locks.pop(uid, None)


# --------------------------------------------------------------------- drawings
DrawingIdPath = Annotated[str, Path(min_length=1, max_length=128)]


class DrawingIn(BaseModel):
    symbol: SymbolId
    name: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    data: dict[str, Any] = Field(default_factory=dict)


_reads: asyncio.Semaphore | None = None
_reads_by_user = limits.KeyedGate(1)


@router.get("/drawings")
async def list_drawings(request: Request, symbol: SymbolId, offset: int = Query(0, ge=0, le=1_000_000),
                        uid: str = Depends(user_id)) -> dict:
    """One page of the symbol's drawings; follow `next_offset` until it is null."""
    global _reads
    p = lim()
    if _reads is None:
        _reads = asyncio.Semaphore(p.drawing_reads)
    if not _reads_by_user.enter(uid):
        raise HTTPException(429, "drawings are already loading")
    try:
        try:
            await asyncio.wait_for(_reads.acquire(), timeout=15)
        except TimeoutError as exc:
            raise HTTPException(503, "server is busy, try again in a moment") from exc
        try:
            page, nxt = await db(request, "drawings_page", uid, symbol, offset, p.drawings_page_rows, p.drawings_page_bytes)
        finally:
            _reads.release()
    finally:
        _reads_by_user.leave(uid)
    return {"symbol": symbol, "drawings": page, "next_offset": nxt}


@router.put("/drawings/{drawing_id}")
async def put_drawing(request: Request, drawing_id: DrawingIdPath, body: DrawingIn, uid: str = Depends(user_id)) -> dict:
    p = lim()
    data_json = _payload(body.data, p.drawing_bytes, "drawing")
    await db(request, "put_drawing_checked", uid, drawing_id, body.symbol, body.name, data_json,
             max_total=p.drawings, max_per_symbol=p.drawings_per_symbol, quota=quota())
    return {"ok": True}


class DrawingsBatchIn(BaseModel):
    symbol: SymbolId
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


@router.put("/drawings")
async def put_drawings(request: Request, body: DrawingsBatchIn, uid: str = Depends(user_id)) -> dict:
    """Update up to 100 existing drawings of one symbol at once (lock all / hide all)."""
    p = lim()
    items = []
    for it in body.items:
        did, name, data = it.get("id"), it.get("name"), it.get("data")
        if not (isinstance(did, str) and 0 < len(did) <= 128 and isinstance(name, str) and 0 < len(name) <= 64
                and isinstance(data, dict)):
            raise HTTPException(400, "bad drawing in batch")
        items.append((did, body.symbol, name, _payload(data, p.drawing_bytes, "drawing")))
    return {"updated": await db(request, "update_drawings_checked", uid, items, quota=quota())}


@router.delete("/drawings/{drawing_id}")
async def delete_drawing(request: Request, drawing_id: DrawingIdPath, uid: str = Depends(user_id)) -> dict:
    return {"deleted": await db(request, "delete_drawing", uid, drawing_id)}


@router.delete("/drawings")
async def delete_drawings(request: Request, symbol: SymbolId, uid: str = Depends(user_id)) -> dict:
    return {"deleted": await db(request, "delete_drawings", uid, symbol)}


# ------------------------------------------------------------------- watchlists
class WatchlistIn(BaseModel):
    name: ShortName | None = None
    items: list[SymbolId] | None = None
    position: int | None = Field(None, ge=0, le=10_000)


def _check_items(items: list[str] | None) -> None:
    if items is not None and len(items) > lim().watchlist_items:
        raise HTTPException(413, f"a watchlist holds at most {lim().watchlist_items} symbols")


@router.get("/watchlists")
async def list_watchlists(request: Request, uid: str = Depends(user_id)) -> dict:
    await _seed_watchlists(request, uid)
    return {"watchlists": await db(request, "watchlists", uid)}


@router.post("/watchlists")
async def create_watchlist(request: Request, body: WatchlistIn, uid: str = Depends(user_id)) -> dict:
    _check_items(body.items)
    return await db(request, "create_watchlist_checked", uid, body.name or "New list", body.items or [],
                    max_lists=lim().watchlists, quota=quota())


@router.put("/watchlists/{wid}")
async def update_watchlist(request: Request, wid: int, body: WatchlistIn, uid: str = Depends(user_id)) -> dict:
    _check_items(body.items)
    if not await db(request, "update_watchlist_checked", uid, wid, body.name, body.items, body.position, quota=quota()):
        raise HTTPException(404, "watchlist not found")
    return {"ok": True}


@router.delete("/watchlists/{wid}")
async def delete_watchlist(request: Request, wid: int, uid: str = Depends(user_id)) -> dict:
    if not await db(request, "delete_watchlist", uid, wid):
        raise HTTPException(404, "watchlist not found")
    return {"ok": True}


# ---------------------------------------------------------------------- layouts
LayoutName = Annotated[str, Path(min_length=1, max_length=80)]


class LayoutIn(BaseModel):
    state: dict[str, Any]


@router.get("/layouts")
async def list_layouts(request: Request, uid: str = Depends(user_id)) -> dict:
    return {"layouts": await db(request, "layouts", uid)}


@router.get("/layouts/{name}")
async def get_layout(request: Request, name: LayoutName, uid: str = Depends(user_id)) -> dict:
    lay = await db(request, "layout", uid, name)
    if lay is None:
        raise HTTPException(404, "layout not found")
    return lay


@router.put("/layouts/{name}")
async def put_layout(request: Request, name: LayoutName, body: LayoutIn, uid: str = Depends(user_id)) -> dict:
    p = lim()
    state_json = _payload(body.state, p.layout_bytes, "layout")
    await db(request, "put_layout_checked", uid, name, state_json, max_layouts=p.layouts, quota=quota())
    return {"ok": True}


@router.delete("/layouts/{name}")
async def delete_layout(request: Request, name: LayoutName, uid: str = Depends(user_id)) -> dict:
    if not await db(request, "delete_layout", uid, name):
        raise HTTPException(404, "layout not found")
    return {"ok": True}


# --------------------------------------------------------------------- settings
@router.get("/settings")
async def get_settings(request: Request, uid: str = Depends(user_id)) -> dict:
    return await db(request, "settings", uid)


@router.put("/settings")
async def put_settings(request: Request, body: dict[str, Any], uid: str = Depends(user_id)) -> dict:
    p = lim()
    if any(len(k) > 64 for k in body):
        raise HTTPException(400, "setting names are limited to 64 characters")
    if len(body) > p.settings_keys:
        raise HTTPException(413, f"at most {p.settings_keys} settings")
    _payload(body, p.settings_bytes, "settings")  # depth + size of this update
    await db(request, "put_settings_checked", uid, body, max_keys=p.settings_keys, max_bytes=p.settings_bytes, quota=quota())
    return {"ok": True}
