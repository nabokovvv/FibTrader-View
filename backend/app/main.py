"""FibTrader View backend: REST + WebSocket + static SPA on a single port."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, limits, plugins
from .guard import Guard
from .limits import Busy
from .candles.service import CandleService
from .candles.store import StoreRegistry
from .intervals import INTERVALS, ORDER
from .markets.base import Provider
from .markets.bybit import BybitProvider
from .markets.moex import MoexProvider
from .markets.yahoo import YahooProvider
from .symbols import MARKET_LABEL, MARKET_ORDER, SymbolRegistry

log = logging.getLogger("lv")


def setup_logging() -> None:
    config.ensure_dirs()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
        fh = logging.handlers.RotatingFileHandler(config.LOGS_DIR / "backend.log", maxBytes=10 * 1024**2, backupCount=5)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)  # the 5 s prewarm tick would flood the log


def build_providers() -> dict[str, Provider]:
    providers: dict[str, Provider] = {}
    on = config.market_enabled
    if on("bybit"):
        providers["bybit"] = BybitProvider("linear")
    if on("bybit-spot"):
        providers["bybit-spot"] = BybitProvider("spot")
    if on("moex"):
        providers["moex"] = MoexProvider()
    if on("yahoo"):
        providers["yahoo"] = YahooProvider()
    if on("us"):
        if config.has_alpaca():
            from .markets.alpaca import AlpacaProvider
            providers["us"] = AlpacaProvider()
        else:
            log.warning("Alpaca keys missing: US stocks market disabled")
    unknown = set(config.MARKETS) - {"bybit", "bybit-spot", "moex", "yahoo", "us"}
    if unknown:
        log.warning("LV_MARKETS: unknown markets ignored: %s", ", ".join(sorted(unknown)))
    return providers


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    if config.AUTH == "supabase" and not (config.SUPABASE_URL and config.SUPABASE_PUBLISHABLE_KEY):
        raise RuntimeError("LV_AUTH=supabase needs SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY")
    if config.AUTH not in ("none", "supabase"):
        raise RuntimeError(f"unknown LV_AUTH={config.AUTH!r} (use none or supabase)")
    providers = build_providers()
    stores = StoreRegistry(config.CANDLES_DIR)
    app.state.providers = providers
    app.state.stores = stores
    # its own dict: plugins may add internal (non user-facing) series providers to it
    if config.MAINTENANCE:  # small files: switch them to incremental vacuum once, so cleanups return disk space
        for m in providers:
            try:
                stores.get(m).enable_incremental_vacuum(max_bytes=2 * 1024**3)
            except Exception as exc:  # noqa: BLE001
                log.warning("incremental vacuum for %s not enabled: %s", m, exc)
    lp = limits.profile()
    app.state.candles = CandleService(dict(providers), stores, backfill_bars=lp.backfill_bars, cache_bytes=lp.candle_cache_bytes,
                                      min_free_disk=lp.min_free_disk, candles_dir=config.CANDLES_DIR,
                                      tail_ttl_ms=3000 if config.AUTH == "supabase" else 0)
    app.state.symbols = SymbolRegistry(providers)
    from .realtime.hub import Hub
    app.state.hub = Hub(app.state.candles, app.state.symbols, providers)
    from .userdata.db import UserDB
    app.state.userdb = UserDB(config.APP_DB)
    from .userdata.routes import call as udb_call
    bot = None
    if config.TELEGRAM_BOT_TOKEN:
        from .telegram import TelegramBot
        bot = TelegramBot(config.TELEGRAM_BOT_TOKEN, lambda fn, *a: udb_call(app.state.userdb, fn, *a))
        await bot.start()
    app.state.telegram = bot
    from .alerts import AlertEngine, build_feeds
    app.state.alerts = AlertEngine(app.state.userdb, build_feeds(providers), app.state.symbols, bot)
    from .feedback import FeedbackWorker
    app.state.feedback = FeedbackWorker(app.state.userdb, bot)
    app.state.derivs = app.state.liq = None
    if "bybit" in providers:
        from .derivs import DerivStore, Derivs, LiqRecorder
        app.state.derivs = Derivs(DerivStore(config.CANDLES_DIR / "derivs.sqlite"), providers["bybit"])
        if config.LIQUIDATIONS:
            app.state.liq = LiqRecorder(app.state.derivs)
    from .jobs import Jobs
    app.state.jobs = Jobs(app.state)
    plugins.startup(app.state)
    await app.state.hub.start()
    await app.state.alerts.start()
    await app.state.feedback.start()
    if app.state.liq:
        await app.state.liq.start()
    app.state.jobs.start()
    log.info("FibTrader View backend up on %s:%s (markets: %s; plugins: %s)", config.HOST, config.PORT,
             ", ".join(providers), ", ".join(plugins.names()) or "none")
    try:
        yield
    finally:
        plugins.shutdown(app.state)
        app.state.jobs.stop()
        await app.state.alerts.stop()
        await app.state.feedback.stop()
        if app.state.liq:
            await app.state.liq.stop()
        if app.state.derivs:
            app.state.derivs.store.close()
        if app.state.telegram:
            await app.state.telegram.stop()
        await app.state.hub.stop()
        stores.close_all()
        app.state.userdb.close()


class _Limits:
    def __init__(self) -> None:
        self.profile = p = limits.profile()
        self.bars_rate = limits.RateLimiter(p.bars_rate, p.bars_burst)
        self.bars_ip = limits.KeyedGate(p.bars_per_ip)
        self.bars_global = asyncio.Semaphore(p.bars_global)


app = FastAPI(title="FibTrader View", lifespan=lifespan, docs_url=None if config.AUTH == "supabase" else "/docs",
              redoc_url=None, openapi_url=None if config.AUTH == "supabase" else "/openapi.json")
app.state.limits = _Limits()
app.add_middleware(Guard)


# ------------------------------------------------------------------------ API
@app.get("/api/health")
async def health(request: Request) -> dict:
    return {"ok": True, "markets": list(request.app.state.providers), "intervals": ORDER}


@app.get("/api/config")
async def public_config(request: Request) -> dict:
    """Unauthenticated: tells the frontend how to sign in and which markets exist."""
    st = request.app.state
    bot = getattr(st, "telegram", None)
    features = {
        "alerts": sorted(st.alerts.feeds) if getattr(st, "alerts", None) else [],
        "telegram": bool(bot and bot.available),
        "feedback": config.AUTH == "supabase",
        "shares": True,
        "derivs": getattr(st, "derivs", None) is not None,
    }
    return {**auth.public_config(), "markets": list(st.providers), "features": features,
            "limits": limits.public_limits() if limits.profile() is limits.PUBLIC else None}


@app.get("/api/markets")
async def markets(request: Request) -> dict:
    out = []
    for m in MARKET_ORDER:
        p = request.app.state.providers.get(m)
        if p is None:
            continue
        out.append({"id": m, "label": MARKET_LABEL.get(m, m), "native_intervals": sorted(p.native_intervals, key=ORDER.index)})
    return {"markets": out, "intervals": [{"key": k, "span": v.span, "type": v.type} for k, v in INTERVALS.items()]}


@app.get("/api/symbols/search")
async def symbols_search(request: Request, q: str = Query("", max_length=64), limit: int = Query(40, ge=1, le=100),
                         markets: str | None = None) -> dict:
    mk = [m for m in markets.split(",") if m] if markets else None
    res = await request.app.state.symbols.search(q, limit=limit, markets=mk)
    return {"results": [s.to_dict() for s in res]}


@app.get("/api/symbols")
async def symbols_batch(request: Request, ids: str = Query(..., max_length=100 * 65)) -> dict:
    """Metadata for up to 100 symbols in one call (watchlists); unknown ids are left out."""
    wanted = [i for i in dict.fromkeys(ids.split(",")) if 0 < len(i) <= 64][:100]
    out = []
    for i in wanted:
        info = await request.app.state.symbols.get(i)
        if info is not None:
            out.append({"requested": i, **info.to_dict()})
    return {"symbols": out}


@app.get("/api/symbol")
async def symbol(request: Request, id: str) -> dict:
    info = await request.app.state.symbols.get(id)
    if info is None:
        raise HTTPException(404, f"unknown symbol '{id}'")
    return info.to_dict()


MIN_TS = 1_230_768_000_000  # 2009-01-01: nothing we serve is older


@app.get("/api/bars")
async def bars(request: Request, id: str = Query(..., max_length=64), interval: str = Query("1h", max_length=4),
               to: int | None = None, limit: int = Query(500, ge=1, le=2000)) -> dict:
    if interval not in INTERVALS:
        raise HTTPException(400, f"unknown interval '{interval}'")
    if to is not None and not (MIN_TS <= to <= int(time.time() * 1000) + 2 * 86_400_000):
        raise HTTPException(400, "'to' is out of range")
    info = await request.app.state.symbols.get(id)
    if info is None:
        raise HTTPException(404, f"unknown symbol '{id}'")
    ip = request.scope.get("state", {}).get("client_ip", "unknown")
    request.app.state.last_user_bars = time.monotonic()  # the prewarm job waits while people use the site
    lim = request.app.state.limits
    if not lim.bars_rate.allow(ip):
        raise HTTPException(429, "too many chart requests, slow down")
    if not lim.bars_ip.enter(ip):
        raise HTTPException(429, "too many chart requests at once")
    try:
        try:
            await asyncio.wait_for(lim.bars_global.acquire(), timeout=lim.profile.bars_wait_s)
        except TimeoutError as exc:
            raise HTTPException(503, "server is busy, try again in a moment") from exc
        try:
            page = await request.app.state.candles.get_bars(info, interval, to, limit)
        finally:
            lim.bars_global.release()
    except HTTPException:
        raise
    except Busy as exc:
        raise HTTPException(503, "data source is busy, try again in a moment") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # provider failure -> 502 with message
        log.exception("bars %s %s failed", id, interval)
        raise HTTPException(502, "data source error") from exc
    finally:
        lim.bars_ip.leave(ip)
    return {"id": info.id, "interval": interval, "native": page.native, "more": page.more, "bars": page.bars}


@app.get("/api/stats")
async def stats(request: Request) -> dict:
    """Full scan of the candle stores — private instance only."""
    if config.AUTH == "supabase":
        raise HTTPException(404)
    stores: StoreRegistry = request.app.state.stores
    return await asyncio.to_thread(lambda: {m: stores.get(m).stats() for m in request.app.state.providers})


from .userdata.routes import router as userdata_router  # noqa: E402
from .realtime.routes import router as realtime_router  # noqa: E402
from .alerts import router as alerts_router  # noqa: E402
from .derivs import router as derivs_router  # noqa: E402
from .feedback import router as feedback_router  # noqa: E402
from .shares import router as shares_router  # noqa: E402

app.include_router(userdata_router, prefix="/api")
for _router in (alerts_router, derivs_router, feedback_router, shares_router):
    app.include_router(_router, prefix="/api")
app.include_router(realtime_router)
for _router in plugins.routers():  # before the SPA catch-all below
    app.include_router(_router, prefix="/api")


# ------------------------------------------------------------------ frontend
DIST = config.FRONTEND_DIST
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


DIST_ROOT = DIST.resolve()


def _dist_file(path: str) -> Path | None:
    """A file inside frontend/dist, or None. Rejects traversal (.., encoded slashes), absolute paths and symlinks out."""
    if not path or "\\" in path or "\x00" in path or path.startswith("/"):
        return None
    candidate = (DIST_ROOT / path).resolve()
    if not candidate.is_relative_to(DIST_ROOT) or candidate == DIST_ROOT:
        return None
    return candidate if candidate.is_file() else None


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith("api/") or path == "ws":
        raise HTTPException(404)
    candidate = _dist_file(path)
    if candidate is not None:
        return FileResponse(candidate)
    index = DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"detail": "frontend not built: run `npm run build` in frontend/"}, status_code=503)
