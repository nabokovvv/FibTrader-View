"""Price alerts: crossing a price level or a drawn line, checked on the server every few seconds.

Prices come from per-market *feeds*: one request returns the last price of every symbol of a market, so alerts
work on symbols nobody has open and cost the same for 1 or 20 000 alerts. A new data source (e.g. a user's own
Alpaca key) plugs in as another feed. A market is polled only while it has armed alerts.

An alert fires once (then it is switched off; the user re-arms it) and goes to Telegram when the account is linked;
the site also shows it on the next poll of GET /api/alerts.
"""
from __future__ import annotations

import asyncio
import logging
import math
import secrets
import time
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints

from . import config, limits
from .auth import user_id
from .limits import Busy
from .userdata.routes import SymbolId, call, db

log = logging.getLogger("lv.alerts")

POLL_S = 3.0
CODE_TTL_MS = 15 * 60_000
MARKET_NAME = {
    "bybit": {"en": "Bybit perpetual", "ru": "Bybit фьючерс"},
    "bybit-spot": {"en": "Bybit spot", "ru": "Bybit спот"},
    "hl": {"en": "Hyperliquid", "ru": "Hyperliquid"},
}
COND_TEXT = {
    "cross": {"en": "crossed", "ru": "пересекла"},
    "up": {"en": "crossed up", "ru": "пересекла вверх"},
    "down": {"en": "crossed down", "ru": "пересекла вниз"},
}


# ----------------------------------------------------------------------- feeds
class Feed(Protocol):
    async def prices(self, wanted: set[str]) -> dict[str, float]: ...


class BybitFeed:
    """All last prices of one Bybit category in one request (inside the shared Bybit REST budget)."""

    def __init__(self, provider):
        self.provider = provider

    async def prices(self, wanted: set[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for t in await self.provider.tickers():
            try:
                out[t["symbol"]] = float(t["lastPrice"])
            except (KeyError, TypeError, ValueError):
                continue
        return out


class HyperliquidFeed:
    """Mid prices of every market of a Hyperliquid exchange in one cheap request (weight 2), only for the exchanges
    that have armed alerts."""

    def __init__(self, provider):
        self.provider = provider

    async def prices(self, wanted: set[str]) -> dict[str, float]:
        return await self.provider.mids(wanted)


def build_feeds(providers: dict) -> dict[str, Feed]:
    feeds: dict[str, Feed] = {m: BybitFeed(p) for m, p in providers.items() if m in ("bybit", "bybit-spot")}
    if "hl" in providers:
        feeds["hl"] = HyperliquidFeed(providers["hl"])
    return feeds


# ---------------------------------------------------------------------- checks
def level_at(alert: dict, now_ms: float) -> float | None:
    """The price the alert watches right now: a fixed level, or the drawn line extended to the right."""
    line = alert.get("line")
    if line:
        t1, p1, t2, p2 = line
        return p1 + (p2 - p1) * (now_ms - t1) / (t2 - t1)
    return alert.get("level")


def crossed(cond: str, prev_diff: float, cur_diff: float) -> bool:
    """`*_diff` = price − watched level, each at its own moment (a sloped line moves between two polls)."""
    up = prev_diff < 0 <= cur_diff
    down = prev_diff > 0 >= cur_diff
    return (up or down) if cond == "cross" else up if cond == "up" else down


def fmt(v: float, precision: int) -> str:
    return f"{v:,.{max(0, min(precision, 10))}f}"


class AlertEngine:
    def __init__(self, userdb, feeds: dict[str, Feed], symbols, bot=None):
        self.udb, self.feeds, self.symbols, self.bot = userdb, feeds, symbols, bot
        self._rev = -1
        self._groups: dict[str, dict[str, list[dict]]] = {}   # market -> symbol -> armed alerts
        self._last: dict[tuple[str, str], tuple[float, float]] = {}  # (price, ms) last seen per (market, symbol)
        self._seen: dict[int, int] = {}                       # alert id -> rev it has a baseline for
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.feeds:
            self._task = asyncio.create_task(self._run(), name="alerts")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:  # noqa: BLE001
                pass

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(POLL_S)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("alerts tick failed")

    async def _reload(self) -> None:
        self._rev = self.udb.alerts_rev  # first: a change made while reading triggers the next reload
        groups: dict[str, dict[str, list[dict]]] = {}
        for a in await call(self.udb, "active_alerts"):
            market, _, sym = a["symbol"].partition(":")
            groups.setdefault(market, {}).setdefault(sym, []).append(a)
        self._groups = groups
        watched = {(m, s) for m, by in groups.items() for s in by}
        self._last = {k: v for k, v in self._last.items() if k in watched}
        armed = {a["id"] for by in groups.values() for lst in by.values() for a in lst}
        self._seen = {k: v for k, v in self._seen.items() if k in armed}

    async def tick(self, now_ms: float | None = None) -> list[tuple[dict, float]]:
        if self._rev != self.udb.alerts_rev:
            await self._reload()
        now_ms = now_ms or time.time() * 1000
        hits: list[tuple[dict, float]] = []
        for market, by_sym in self._groups.items():
            feed = self.feeds.get(market)
            if feed is None or not by_sym:
                continue
            try:
                px = await feed.prices(set(by_sym))
            except Busy:
                continue  # the shared Bybit budget is in use by charts: next tick
            except Exception as exc:  # noqa: BLE001
                log.info("alerts: %s prices failed: %s", market, exc)
                continue
            for sym, alerts in by_sym.items():
                p = px.get(sym)
                if p is None:
                    continue
                prev = self._last.get((market, sym))
                self._last[(market, sym)] = (p, now_ms)
                for a in alerts:
                    if self._seen.get(a["id"]) != a.get("rev", 0):  # new or changed target: this tick is its baseline
                        self._seen[a["id"]] = a.get("rev", 0)
                        continue
                    if prev is None:
                        continue
                    l0, l1 = level_at(a, prev[1]), level_at(a, now_ms)
                    if l0 is None or l1 is None or not (math.isfinite(l0) and math.isfinite(l1)):
                        continue
                    if crossed(a["cond"], prev[0] - l0, p - l1):
                        hits.append((a, p))
        if hits:
            done = await call(self.udb, "fire_alerts", [(a["id"], a.get("rev", 0), p) for a, p in hits])
            hits = [(a, p) for a, p in hits if a["id"] in done]
            await self._notify(hits, now_ms)
        return hits

    async def _notify(self, hits: list[tuple[dict, float]], now_ms: float) -> None:
        if not self.bot or not hits:
            return
        chats = await call(self.udb, "tg_chats", sorted({a["user_id"] for a, _ in hits}))
        by_user: dict[str, list[str]] = {}
        for a, price in hits:
            link = chats.get(a["user_id"])
            if link is None:
                continue
            by_user.setdefault(a["user_id"], []).append(await self.message(a, price, now_ms, link[1]))
        for uid, parts in by_user.items():
            msg = ""
            for part in parts:  # Telegram takes 4096 characters: split between notifications, never inside one
                if msg and len(msg) + 2 + len(part) > 3500:
                    self.bot.send_alert(chats[uid][0], msg)
                    msg = ""
                msg = f"{msg}\n\n{part}" if msg else part
            if msg:
                self.bot.send_alert(chats[uid][0], msg)

    async def message(self, a: dict, price: float, now_ms: float, lang: str) -> str:
        info = await self.symbols.get(a["symbol"])
        market = a["symbol"].split(":", 1)[0]
        name = (info.display or info.symbol) if info else a["symbol"].split(":", 1)[-1]
        prec = info.price_precision if info else 4
        lvl = level_at(a, now_ms) or 0.0
        what = COND_TEXT.get(a["cond"], COND_TEXT["cross"])[lang]
        target = (f"your line ({fmt(lvl, prec)})" if lang == "en" else f"вашу линию ({fmt(lvl, prec)})") if a.get("line") \
            else fmt(lvl, prec)
        now_word = "now" if lang == "en" else "сейчас"
        lines = [f"🔔 {name} · {MARKET_NAME.get(market, {}).get(lang, market)}",
                 f"{'Price' if lang == 'en' else 'Цена'} {what} {target} ({now_word} {fmt(price, prec)})"]
        if a.get("note"):
            lines.append(a["note"])
        if config.PUBLIC_URL:
            lines.append(f"{config.PUBLIC_URL}/?symbol={a['symbol']}")
        return "\n".join(lines)


# ------------------------------------------------------------------------- API
router = APIRouter()
Note = Annotated[str, StringConstraints(max_length=140, strip_whitespace=True)]


class AlertIn(BaseModel):
    symbol: SymbolId
    cond: Literal["cross", "up", "down"] = "cross"
    level: float | None = Field(None, gt=0, lt=1e12)
    line: list[float] | None = Field(None, min_length=4, max_length=4)
    drawing_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    note: Note = ""


class AlertPatch(BaseModel):
    active: bool | None = None
    level: float | None = Field(None, gt=0, lt=1e12)
    cond: Literal["cross", "up", "down"] | None = None
    note: Note | None = None


def _engine(request: Request) -> AlertEngine:
    return request.app.state.alerts


@router.get("/alerts")
async def list_alerts(request: Request, uid: str = Depends(user_id)) -> dict:
    eng = _engine(request)
    return {"alerts": await db(request, "alerts", uid), "markets": sorted(eng.feeds),
            "telegram": bool(eng.bot and eng.bot.available)}


@router.post("/alerts")
async def create_alert(request: Request, body: AlertIn, uid: str = Depends(user_id)) -> dict:
    if (body.level is None) == (body.line is None):
        raise HTTPException(400, "give either a price level or a line")
    if body.line is not None:
        t1, p1, t2, p2 = body.line
        if not all(math.isfinite(v) for v in body.line) or not t1 < t2 or p1 <= 0 or p2 <= 0:
            raise HTTPException(400, "bad line")
    if body.symbol.split(":", 1)[0] not in _engine(request).feeds:
        raise HTTPException(400, "alerts are not available for this market yet")
    if await request.app.state.symbols.get(body.symbol) is None:
        raise HTTPException(404, f"unknown symbol '{body.symbol}'")
    p = limits.profile()
    alert = await db(request, "create_alert_checked", uid, body.symbol, body.cond, body.level, body.line,
                     body.drawing_id, body.note, max_active=p.alerts_active, max_total=p.alerts_total)
    return {"alert": alert}


@router.patch("/alerts/{aid}")
async def update_alert(request: Request, aid: int, body: AlertPatch, uid: str = Depends(user_id)) -> dict:
    alert = await db(request, "update_alert_checked", uid, aid, active=body.active, level=body.level, cond=body.cond,
                     note=body.note, max_active=limits.profile().alerts_active)
    if alert is None:
        raise HTTPException(404, "alert not found")
    return {"alert": alert}


@router.delete("/alerts/{aid}")
async def delete_alert(request: Request, aid: int, uid: str = Depends(user_id)) -> dict:
    return {"deleted": await db(request, "delete_alert", uid, aid)}


# -------------------------------------------------------------- telegram link
class LinkIn(BaseModel):
    lang: Literal["en", "ru"] = "en"


def _bot(request: Request):
    bot = getattr(request.app.state, "telegram", None)
    return bot if bot is not None and bot.available else None


@router.get("/telegram")
async def telegram_status(request: Request, uid: str = Depends(user_id)) -> dict:
    bot = _bot(request)
    link = await db(request, "tg_link", uid) if bot else None
    return {"available": bot is not None, "linked": link is not None, "bot": bot.username if bot else None}


@router.post("/telegram/link")
async def telegram_link(request: Request, body: LinkIn, uid: str = Depends(user_id)) -> dict:
    bot = _bot(request)
    if bot is None:
        raise HTTPException(503, "Telegram is not available right now")
    code = secrets.token_urlsafe(18)  # 24 chars of [A-Za-z0-9_-]: valid as a Telegram start parameter
    await db(request, "tg_new_code", uid, code, body.lang, CODE_TTL_MS)
    return {"url": bot.link_url(code), "expires_in": CODE_TTL_MS // 1000}


@router.delete("/telegram")
async def telegram_unlink(request: Request, uid: str = Depends(user_id)) -> dict:
    return {"unlinked": await db(request, "tg_unlink", uid)}
