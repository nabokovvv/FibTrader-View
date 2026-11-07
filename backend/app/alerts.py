"""Alerts: the price crossing a level or a drawn line, the funding rate or open interest of a perpetual crossing a
level, or a market-wide daily series (Fear & Greed Index, influencer pressure) crossing a level. Checked on the
server every few seconds.

Values come from per-market *feeds*: one request returns the last price (and, for perpetuals, the funding rate and
open interest) of every symbol of a market, so alerts work on symbols nobody has open and cost the same for 1 or
20 000 alerts. A new data source (e.g. a user's own Alpaca key) plugs in as another feed. A market is polled only
while it has armed alerts. The daily series are read from memory (``sentiment.py``): no extra requests.

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
    "bq": {"en": "CFD (biquote)", "ru": "CFD (biquote)"},
}
METRICS = ("price", "funding", "oi", "fng", "yt")
PERP_METRICS = {"funding", "oi"}        # per symbol, perpetuals only
MARKET_METRICS = {"fng", "yt"}          # one series for the whole market (the alert keeps its chart's symbol)
PERP_MARKETS = {"bybit", "hl"}
METRIC_NAME = {
    "price": {"en": "Price", "ru": "Цена"},
    "funding": {"en": "Funding rate", "ru": "Фандинг"},
    "oi": {"en": "Open interest", "ru": "Открытый интерес"},
    "fng": {"en": "Fear & Greed Index", "ru": "Индекс страха и жадности"},
    "yt": {"en": "Influencer pressure", "ru": "Давление инфлюенсеров"},
}
COND_TEXT = {
    "cross": {"en": "crossed", "ru": "пересекла"},
    "up": {"en": "crossed up", "ru": "пересекла вверх"},
    "down": {"en": "crossed down", "ru": "пересекла вниз"},
}


# ----------------------------------------------------------------------- feeds
class Feed(Protocol):
    async def prices(self, wanted: set[str]) -> dict[str, float]: ...
    # optional: async def values(wanted, metrics) -> {symbol: {metric: value}} (price, funding in %, oi in coins)


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class BybitFeed:
    """All last prices of one Bybit category in one request (inside the shared Bybit REST budget); the same answer
    carries the funding rate and open interest of every perpetual."""

    def __init__(self, provider):
        self.provider = provider

    async def prices(self, wanted: set[str]) -> dict[str, float]:
        return {s: v["price"] for s, v in (await self.values(wanted, {"price"})).items() if "price" in v}

    async def values(self, wanted: set[str], metrics: set[str]) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for t in await self.provider.tickers():
            sym = t.get("symbol")
            if not sym:
                continue
            row: dict[str, float] = {}
            for metric, key, scale in (("price", "lastPrice", 1), ("funding", "fundingRate", 100), ("oi", "openInterest", 1)):
                v = _num(t.get(key)) if metric in metrics else None
                if v is not None:
                    row[metric] = v * scale
            out[sym] = row
        return out


class HyperliquidFeed:
    """Mid prices of every market of a Hyperliquid exchange in one cheap request (weight 2), only for the exchanges
    that have armed alerts. Funding (hourly, in %) and open interest come from the exchange context (weight 20): it
    is refreshed by its own task at most once a minute (background budget, which may wait), and the engine only reads
    the last snapshot, so a slow refresh never holds up the other alerts. A snapshot older than 3 minutes is not used."""

    CTX_EVERY_S = 60
    CTX_MAX_AGE_S = 180

    def __init__(self, provider):
        self.provider = provider
        self._refresh: dict[str, asyncio.Task] = {}

    async def prices(self, wanted: set[str]) -> dict[str, float]:
        return await self.provider.mids(wanted)

    def _ctx_refresh(self, dex: str) -> None:
        task = self._refresh.get(dex)
        if task is not None and not task.done():
            return

        async def run() -> None:
            try:
                await self.provider.contexts(dex, self.CTX_EVERY_S, background=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the next tick tries again
                log.info("alerts: hyperliquid context %s: %s", dex or "main", exc)

        self._refresh[dex] = asyncio.create_task(run(), name=f"alerts-hl-ctx-{dex or 'main'}")

    async def values(self, wanted: set[str], metrics: set[str]) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        if "price" in metrics:
            for s, p in (await self.provider.mids(wanted)).items():
                out.setdefault(s, {})["price"] = p
        if metrics & PERP_METRICS:
            from .markets.hyperliquid import split
            now = time.time()
            for dex in sorted({split(c)[0] for c in wanted}):
                snap = self.provider.cached_contexts(dex)
                if snap is None or now - snap[0] >= self.CTX_EVERY_S:
                    self._ctx_refresh(dex)
                if snap is None or now - snap[0] > self.CTX_MAX_AGE_S:
                    continue
                for coin, c in snap[1].items():
                    f, oi = _num(c.get("funding")), _num(c.get("openInterest"))
                    if f is not None:
                        out.setdefault(coin, {})["funding"] = f * 100
                    if oi is not None:
                        out.setdefault(coin, {})["oi"] = oi
        return out


class BiquoteFeed:
    """Streamed mid prices (the symbols asked for stay on the biquote stream); a symbol without a fresh tick is read
    in one batched REST call."""

    def __init__(self, provider):
        self.provider = provider

    async def prices(self, wanted: set[str]) -> dict[str, float]:
        return {s: p for s, (p, _ch) in (await self.provider.prices(wanted)).items()}


def build_feeds(providers: dict) -> dict[str, Feed]:
    feeds: dict[str, Feed] = {m: BybitFeed(p) for m, p in providers.items() if m in ("bybit", "bybit-spot")}
    if "hl" in providers:
        feeds["hl"] = HyperliquidFeed(providers["hl"])
    if "bq" in providers:
        feeds["bq"] = BiquoteFeed(providers["bq"])
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
    def __init__(self, userdb, feeds: dict[str, Feed], symbols, bot=None, sentiment=None):
        self.udb, self.feeds, self.symbols, self.bot, self.sentiment = userdb, feeds, symbols, bot, sentiment
        self._rev = -1
        # market -> symbol -> armed alerts; market-wide series live under market "*" with the metric as the symbol
        self._groups: dict[str, dict[str, list[dict]]] = {}
        self._last: dict[tuple[str, str, str], tuple[float, float]] = {}  # (value, ms) per (market, symbol, metric)
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
            metric = a.get("metric") or "price"
            market, _, sym = ("*", "", metric) if metric in MARKET_METRICS else a["symbol"].partition(":")
            groups.setdefault(market, {}).setdefault(sym, []).append(a)
        self._groups = groups
        watched = {(m, s, a.get("metric") or "price") for m, by in groups.items() for s, lst in by.items() for a in lst}
        self._last = {k: v for k, v in self._last.items() if k in watched}
        armed = {a["id"] for by in groups.values() for lst in by.values() for a in lst}
        self._seen = {k: v for k, v in self._seen.items() if k in armed}

    async def tick(self, now_ms: float | None = None) -> list[tuple[dict, float]]:
        if self._rev != self.udb.alerts_rev:
            await self._reload()
        now_ms = now_ms or time.time() * 1000
        hits: list[tuple[dict, float]] = []
        for market, by_sym in self._groups.items():
            if not by_sym:
                continue
            try:
                vals = await self._values(market, by_sym)
            except Busy:
                continue  # the shared Bybit budget is in use by charts: next tick
            except Exception as exc:  # noqa: BLE001
                log.info("alerts: %s values failed: %s", market, exc)
                continue
            if vals is None:
                continue
            for sym, alerts in by_sym.items():
                row = vals.get(sym) or {}
                for metric in {a.get("metric") or "price" for a in alerts}:
                    v = row.get(metric)
                    if v is None:
                        continue
                    key = (market, sym, metric)
                    prev = self._last.get(key)
                    self._last[key] = (v, now_ms)
                    for a in alerts:
                        if (a.get("metric") or "price") != metric:
                            continue
                        if self._seen.get(a["id"]) != a.get("rev", 0):  # new or changed target: this tick is its baseline
                            self._seen[a["id"]] = a.get("rev", 0)
                            continue
                        if prev is None:
                            continue
                        l0, l1 = level_at(a, prev[1]), level_at(a, now_ms)
                        if l0 is None or l1 is None or not (math.isfinite(l0) and math.isfinite(l1)):
                            continue
                        if crossed(a["cond"], prev[0] - l0, v - l1):
                            hits.append((a, v))
        if hits:
            done = await call(self.udb, "fire_alerts", [(a["id"], a.get("rev", 0), p) for a, p in hits])
            hits = [(a, p) for a, p in hits if a["id"] in done]
            await self._notify(hits, now_ms)
        return hits

    async def _values(self, market: str, by_sym: dict[str, list[dict]]) -> dict[str, dict[str, float]] | None:
        if market == "*":  # market-wide daily series: the newest value, read from memory
            if self.sentiment is None:
                return None
            out: dict[str, dict[str, float]] = {}
            for metric in by_sym:
                pts = self.sentiment.fng if metric == "fng" else await self.sentiment.yt_points()
                if pts:
                    out[metric] = {metric: float(pts[-1][1])}
            return out
        feed = self.feeds.get(market)
        if feed is None:
            return None
        metrics = {a.get("metric") or "price" for lst in by_sym.values() for a in lst}
        if hasattr(feed, "values"):
            return await feed.values(set(by_sym), metrics)
        return {s: {"price": p} for s, p in (await feed.prices(set(by_sym))).items()}

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
        metric = a.get("metric") or "price"
        info = await self.symbols.get(a["symbol"])
        market = a["symbol"].split(":", 1)[0]
        name = (info.display or info.symbol) if info else a["symbol"].split(":", 1)[-1]
        prec = {"price": info.price_precision if info else 4, "funding": 4, "oi": 0, "fng": 0, "yt": 1}.get(metric, 4)
        unit = "%" if metric == "funding" else ""
        lvl = level_at(a, now_ms) or 0.0
        what = COND_TEXT.get(a["cond"], COND_TEXT["cross"])[lang]
        # "пересекла" agrees with Цена; Фандинг / интерес / Индекс are masculine, Давление is neuter
        if lang == "ru" and metric != "price":
            what = what.replace("пересекла", "пересекло" if metric == "yt" else "пересёк")
        target = (f"your line ({fmt(lvl, prec)})" if lang == "en" else f"вашу линию ({fmt(lvl, prec)})") if a.get("line") \
            else fmt(lvl, prec) + unit
        now_word = "now" if lang == "en" else "сейчас"
        head = f"🔔 {name} · {MARKET_NAME.get(market, {}).get(lang, market)}" if metric not in MARKET_METRICS \
            else f"🔔 {METRIC_NAME[metric][lang]}"
        lines = [head, f"{METRIC_NAME.get(metric, METRIC_NAME['price'])[lang]} {what} {target} ({now_word} {fmt(price, prec)}{unit})"]
        if a.get("note"):
            lines.append(a["note"])
        if config.PUBLIC_URL:
            lines.append(f"{config.PUBLIC_URL}/?symbol={a['symbol']}")
        return "\n".join(lines)


# ------------------------------------------------------------------------- API
router = APIRouter()
Note = Annotated[str, StringConstraints(max_length=140, strip_whitespace=True)]


Metric = Literal["price", "funding", "oi", "fng", "yt"]


def check_level(metric: str, level: float) -> None:
    """Levels each metric can take: prices and open interest above zero, funding within ±100 %, F&G 0–100."""
    ok = {"price": 0 < level, "oi": 0 < level, "funding": -100 < level < 100, "fng": 0 <= level <= 100,
          "yt": -1e6 < level < 1e6}.get(metric, False)
    if not (ok and math.isfinite(level)):
        raise HTTPException(400, f"bad level for {metric}")


class AlertIn(BaseModel):
    symbol: SymbolId
    cond: Literal["cross", "up", "down"] = "cross"
    metric: Metric = "price"
    level: float | None = Field(None, gt=-1e12, lt=1e12)
    line: list[float] | None = Field(None, min_length=4, max_length=4)
    drawing_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    note: Note = ""


class AlertPatch(BaseModel):
    active: bool | None = None
    level: float | None = Field(None, gt=-1e12, lt=1e12)
    cond: Literal["cross", "up", "down"] | None = None
    note: Note | None = None


def _engine(request: Request) -> AlertEngine:
    return request.app.state.alerts


@router.get("/alerts")
async def list_alerts(request: Request, uid: str = Depends(user_id)) -> dict:
    eng = _engine(request)
    return {"alerts": await db(request, "alerts", uid), "markets": sorted(eng.feeds),
            "telegram": bool(eng.bot and eng.bot.available),
            "metrics": [m for m in METRICS if m not in MARKET_METRICS or eng.sentiment is not None]}


@router.post("/alerts")
async def create_alert(request: Request, body: AlertIn, uid: str = Depends(user_id)) -> dict:
    if (body.level is None) == (body.line is None):
        raise HTTPException(400, "give either a price level or a line")
    if body.metric != "price" and (body.line is not None or body.drawing_id is not None):
        raise HTTPException(400, "lines and drawings carry price alerts only")
    if body.level is not None:
        check_level(body.metric, body.level)
    market = body.symbol.split(":", 1)[0]
    if body.metric in PERP_METRICS and market not in PERP_MARKETS:
        raise HTTPException(400, "funding and open interest alerts work on perpetuals only")
    if body.metric in MARKET_METRICS and _engine(request).sentiment is None:
        raise HTTPException(400, "this series is not available here")
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
                     body.drawing_id, body.note, max_active=p.alerts_active, max_total=p.alerts_total, metric=body.metric)
    return {"alert": alert}


@router.patch("/alerts/{aid}")
async def update_alert(request: Request, aid: int, body: AlertPatch, uid: str = Depends(user_id)) -> dict:
    if body.level is not None:
        mine = next((a for a in await db(request, "alerts", uid) if a["id"] == aid), None)
        if mine is None:
            raise HTTPException(404, "alert not found")
        check_level(mine.get("metric") or "price", body.level)
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
