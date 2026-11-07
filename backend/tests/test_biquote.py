"""biquote: catalogue, candles window, archive, chart reads, search ranking, tick stream, quotes and alerts feed."""
import json
import time
from datetime import datetime, timezone

import httpx
import pytest

from app.alerts import BiquoteFeed, build_feeds
from app.bq_archive import BQArchiver
from app.candles.service import CandleService
from app.candles.store import StoreRegistry
from app.intervals import DAY, HOUR, INTERVALS, MINUTE
from app.maintenance import emergency, retention
from app.markets import biquote as bqmod
from app.markets.biquote import CATALOG, BiquoteProvider
from app.realtime.bq_ws import RS, BiquoteStream
from app.symbols import SymbolRegistry

NOW = int(time.time() * 1000)
WINDOW = {"1m": 301, "5m": 289, "15m": 193, "30m": 193, "1h": 169, "4h": 181, "1d": 500}
IVMS = {"1m": MINUTE, "5m": 5 * MINUTE, "15m": 15 * MINUTE, "30m": 30 * MINUTE, "1h": HOUR, "4h": 4 * HOUR, "1d": DAY}


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeBQ:
    """Answers like biquote: /ohlc gives the newest min(limit, window) bars (newest first), /latest a dict."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        path = request.url.path
        if path.endswith("/ohlc"):
            iv = request.url.params["interval"]
            limit = int(request.url.params["limit"])
            ivms = IVMS[iv]
            cur = NOW // ivms * ivms
            n = min(limit, WINDOW[iv])
            bars = [{"openTime": iso(cur - k * ivms), "open": 10.0 + k, "high": 12.0 + k, "low": 9.0 + k, "close": 11.0 + k,
                     "volume": 0, "tickVolume": 100 + k, "isOpen": k == 0} for k in range(n)]
            return httpx.Response(200, json={"symbol": path.split("/")[2], "interval": iv, "bars": bars})
        if path == "/api/latest":
            syms = request.url.params.get_list("symbols")
            return httpx.Response(200, json={s: {"symbol": s, "mid": 1.5, "dayDiffPercent": -0.4} for s in syms})
        return httpx.Response(404)


@pytest.fixture
def bq(monkeypatch):
    fake = FakeBQ()
    p = BiquoteProvider(httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    p._fg.rate = p._bg.rate = 1e9
    p.fake = fake
    return p


def _service(tmp_path, bq):
    stores = StoreRegistry(tmp_path)
    return CandleService({"bq": bq}, stores, backfill_bars=3000), stores


# ------------------------------------------------------------------ catalogue
def test_catalogue_is_the_curated_seventy():
    assert len(CATALOG) == 70
    kinds = [v[1] for v in CATALOG.values()]
    assert kinds.count("stock") == 19 and kinds.count("index") == 11 and kinds.count("commodity") == 12
    assert kinds.count("forex") == 28
    assert not any(s.endswith("USDT") for s in CATALOG)


async def test_symbols_resolve_only_the_catalogue(bq):
    assert (await bq.resolve("us500")).id == "bq:US500"
    assert await bq.resolve("BTCUSDT") is None
    eur = await bq.resolve("EURUSD")
    assert eur.type == "forex" and eur.price_precision == 5 and eur.session == "24x5"
    assert (await bq.resolve("USDJPY")).price_precision == 3
    assert (await bq.resolve("AAPL")).session != "24x7"


# -------------------------------------------------------------------- candles
async def test_fetch_asks_only_for_what_is_needed_and_cuts_locally(bq):
    ivms = HOUR
    start = NOW - 10 * ivms
    bars = await bq.fetch_candles("EURUSD", "1h", start, NOW)
    req = bq.fake.calls[-1]
    assert req.url.path == "/api/EURUSD/ohlc" and req.url.params["interval"] == "1h"
    assert int(req.url.params["limit"]) == 12
    assert all(start <= b[0] <= NOW for b in bars) and bars == sorted(bars)
    assert bars[-1][5] == 100          # volume = tick count
    # far back: at most 1000, i.e. the whole window
    await bq.fetch_candles("EURUSD", "1D", 1, NOW)
    assert int(bq.fake.calls[-1].url.params["limit"]) == 1000
    # unknown symbol / interval: no request
    n = len(bq.fake.calls)
    assert await bq.fetch_candles("BTCUSDT", "1h", start, NOW) == []
    assert await bq.fetch_candles("EURUSD", "2h", start, NOW) == []
    assert len(bq.fake.calls) == n


async def test_upstream_errors_raise_and_404_is_empty(bq):
    bq.fake.status = 404
    assert await bq.fetch_candles("EURUSD", "1h", NOW - DAY, NOW) == []
    bq.fake.status = 503
    with pytest.raises(httpx.HTTPError):
        await bq.fetch_candles("EURUSD", "1h", NOW - DAY, NOW)


async def test_archive_seeds_whole_window_then_tops_up_and_survives_cleanup(tmp_path, bq, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "CANDLES_DIR", tmp_path)
    svc, stores = _service(tmp_path, bq)
    arch = BQArchiver(bq, svc)
    n = await arch.update("XAUUSD", "1h")
    assert n == WINDOW["1h"] - 1                       # the forming bar is not stored
    store = stores.get("bq-archive")
    cov = store.coverage("XAUUSD", "1h")
    assert cov.exhausted
    assert stores.get("bq").coverage("XAUUSD", "1h") is None
    bq.fake.calls.clear()
    await arch.update("XAUUSD", "1h")
    assert int(bq.fake.calls[-1].url.params["limit"]) <= 4    # only since the last stored bar
    before = store.count("XAUUSD", "1h")
    retention(stores, {"bq": bq}, now=time.time() + 400 * 86400)
    emergency(stores, {"bq": bq})
    assert store.count("XAUUSD", "1h") == before


async def test_cycle_covers_every_symbol_and_archive_interval(tmp_path, bq, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "CANDLES_DIR", tmp_path)
    svc, _ = _service(tmp_path, bq)
    stats = await BQArchiver(bq, svc).cycle()
    assert stats["series"] == 70 * 7 and stats["failed"] == 0
    # 1h / 4h / 1D in the backed-up archive, 1m … 30m in the minutes archive, nothing in the cache
    assert svc.store_for(bq, "1m", "EURUSD").path.name == "bq-minutes-archive.sqlite"
    assert svc.store_for(bq, "1h", "EURUSD").path.name == "bq-archive.sqlite"
    assert svc.stores.get("bq-minutes-archive").coverage("EURUSD", "1m").exhausted
    assert svc.stores.get("bq").coverage("EURUSD", "1m") is None


async def test_chart_reads_archive_and_stops_at_its_start(tmp_path, bq):
    svc, stores = _service(tmp_path, bq)
    info = await bq.resolve("US500")
    page = await svc.get_bars(info, "1h", None, 100)
    assert len(page.bars) == 100 and page.more
    first = stores.get("bq-archive").coverage("US500", "1h")
    assert first is not None
    page = await svc.get_bars(info, "1h", page.bars[0][0], 500)
    assert not page.more                                   # nothing older can be fetched
    # 1m is a cache interval with a floor; 2h / 1W come from 1h / 1D
    page = await svc.get_bars(info, "1m", None, 50)
    assert page.native == "1m" and len(page.bars) == 50
    assert svc.native_for(bq, "2h") == "1h" and svc.native_for(bq, "1W") == "1D" and svc.native_for(bq, "3m") == "1m"


# --------------------------------------------------------------------- search
async def test_search_lists_cfds_after_hyperliquid_and_finds_their_names(bq):
    reg = SymbolRegistry({"bq": bq})
    res = await reg.search("SPX")
    assert res[0].id == "bq:US500"
    assert (await reg.get("DAX")).id == "bq:DE30"
    assert (await reg.get("bq:eurusd")).id == "bq:EURUSD"
    assert await reg.get("bq:BTCUSDT") is None


# --------------------------------------------------------------------- stream
def _tick(sym, mid, ts, **kw):
    return {"symbol": sym, "mid": mid, "bid": mid, "ask": mid, "timestamp": iso(ts), "dayDiffPercent": 0.5, **kw}


async def test_ticks_build_bars_and_the_closed_bar_goes_out_final_before_the_next(bq, monkeypatch):
    import asyncio
    from app.realtime import bq_ws
    monkeypatch.setattr(bq_ws, "FINAL_DELAY_S", 0)
    monkeypatch.setattr(bq_ws, "FLUSH_S", 0.01)
    got = []

    async def on_bar(sid, iv, bar, closed):
        got.append((sid, iv, list(bar), closed))

    s = BiquoteStream(bq, on_bar)
    monkeypatch.setattr(s, "_run", lambda: asyncio.sleep(0))          # no network: ticks are fed by hand
    monkeypatch.setattr(s, "_reseed_loop", lambda: asyncio.sleep(0))
    assert await s.subscribe(s.kline_topic("EURUSD", "1m"))
    t0 = NOW // MINUTE * MINUTE
    s.on_tick(_tick("EURUSD", 1.10, t0 + 1000))
    s.on_tick(_tick("EURUSD", 1.12, t0 + 2000))
    s.on_tick(_tick("EURUSD", 1.09, t0 + 3000))
    s.on_tick(_tick("EURUSD", 2.00, t0 - 5000))           # late tick of the previous bar: ignored
    s.on_tick(_tick("EURUSD", 5.00, t0 + 4000, stale=True))   # stale quote: ignored
    s.on_tick(_tick("BTCUSDT", 1.0, t0 + 4000))            # not ours: ignored
    assert s._bars["1m|EURUSD"] == [t0, 1.10, 1.12, 1.09, 1.09, 0.0]
    s.on_tick(_tick("EURUSD", 1.11, t0 + MINUTE + 10))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if any(g[2][0] == t0 + MINUTE for g in got):
            break
    closed = [g for g in got if g[3]]
    assert closed and closed[0][2] == [t0, 10.0, 12.0, 9.0, 11.0, 100]        # biquote's final version
    first_new = next(i for i, g in enumerate(got) if g[2][0] == t0 + MINUTE)
    assert first_new > got.index(closed[0])                                   # the new bar only after it
    assert bq.ticks["EURUSD"][0] == 1.11
    await s.stop()


async def test_reseed_takes_open_and_ticks_from_rest_and_the_close_once_ticks_stop(bq):
    s = BiquoteStream(bq, None)
    topic = s.kline_topic("XAUUSD", "1h")
    await s.subscribe(topic)
    cur = NOW // HOUR * HOUR
    s._bars[topic] = [cur, 50.0, 60.0, 1.0, 55.5, 0.0]
    s._tick_at[topic] = time.monotonic()
    await s.reseed(topic)
    assert s._bars[topic] == [cur, 10.0, 60.0, 1.0, 55.5, 100]              # streamed close: ticks are coming
    s._tick_at[topic] = time.monotonic() - 60
    await s.reseed(topic)
    assert s._bars[topic][4] == 11.0                                         # no ticks lately: REST close
    await s.unsubscribe(topic)
    assert topic not in s._bars and topic not in s._tick_at
    await s.stop()


async def test_minute_history_is_reachable_to_its_first_bar(tmp_path, bq):
    svc, stores = _service(tmp_path, bq)
    info = await bq.resolve("EURUSD")
    seen, to = [], None
    for _ in range(10):
        page = await svc.get_bars(info, "1m", to, 60)
        seen = page.bars + seen
        if not page.more or not page.bars:
            break
        to = page.bars[0][0]
    assert len({b[0] for b in seen}) == WINDOW["1m"] - 1 or len({b[0] for b in seen}) == WINDOW["1m"]


async def test_topics_are_bounded_and_validated(bq):
    s = BiquoteStream(bq, None, max_topics=2)
    assert not await s.subscribe("1h|BTCUSDT")
    assert await s.subscribe("1h|EURUSD") and await s.subscribe("4h|EURUSD")
    assert not await s.subscribe("1D|EURUSD")
    assert await s.subscribe("1h|EURUSD")                  # same topic: only a reference
    await s.stop()


def test_signalr_frames(bq):
    s = BiquoteStream(bq, None)
    frame = json.dumps({"type": 1, "target": "ReceiveTick", "arguments": [_tick("AAPL", 300.0, NOW)]}) + RS + '{"type":6}' + RS
    assert s.handle(frame) is None
    assert bq.ticks["AAPL"][0] == 300.0
    assert s.handle("not json" + RS + '{"type":7,"error":"bye"}' + RS) == "close"


async def test_wanted_symbols_expire(bq, monkeypatch):
    s = BiquoteStream(bq, None)
    monkeypatch.setattr(s, "_ensure_running", lambda: None)
    s.want(["EURUSD", "NOPE"])
    assert s.needed() == {"EURUSD"}
    s._wanted["EURUSD"] = time.monotonic() - 1
    assert s.needed() == set()


# --------------------------------------------------------------- quotes, alerts
async def test_prices_use_fresh_ticks_and_batch_the_rest(bq):
    bq.ticks["EURUSD"] = (1.2345, NOW, time.monotonic(), 0.3)
    bq.ticks["GBPUSD"] = (9.9, NOW, time.monotonic() - 3600, 0.3)     # too old
    out = await bq.prices({"EURUSD", "GBPUSD", "AAPL", "BTCUSDT"})
    assert out["EURUSD"] == (1.2345, 0.3)
    assert out["GBPUSD"] == (1.5, -0.4) and out["AAPL"] == (1.5, -0.4) and "BTCUSDT" not in out
    req = bq.fake.calls[-1]
    assert req.url.path == "/api/latest" and sorted(req.url.params.get_list("symbols")) == ["AAPL", "GBPUSD"]
    q = await bq.quotes(["EURUSD"])
    assert q[0].id == "bq:EURUSD" and q[0].price == 1.2345


async def test_alert_feed(bq):
    feeds = build_feeds({"bq": bq})
    assert isinstance(feeds["bq"], BiquoteFeed)
    bq.ticks["US500"] = (6000.0, NOW, time.monotonic(), None)
    assert await feeds["bq"].prices({"US500"}) == {"US500": 6000.0}


def test_window_bounds_are_inside_biquote_windows():
    for iv, ms in bqmod.WINDOW_MS.items():
        key = "1d" if iv == "1D" else iv
        assert ms <= WINDOW[key] * INTERVALS[iv].ms    # gaps (nights, weekends) only stretch a bar count in time


# ------------------------------------------------------------- older history
def test_history_import_adds_only_bars_older_than_the_first_stored(tmp_path, monkeypatch):
    import numpy as np
    import sys
    from app import config
    from app.candles.store import MarketStore
    from scripts import import_history
    monkeypatch.setattr(config, "CANDLES_DIR", tmp_path)
    arch = MarketStore(tmp_path / "bq-archive.sqlite")
    first = NOW // HOUR * HOUR - 10 * HOUR
    arch.upsert("EURUSD", "1h", [[first + k * HOUR, 1.1, 1.2, 1.0, 1.15, 5] for k in range(5)])
    arch.set_coverage("EURUSD", "1h", first, first + 4 * HOUR, True)
    older = [[first - k * HOUR, 2.0, 2.1, 1.9, 2.05, 9] for k in range(3, 0, -1)]
    overlap = [[first + k * HOUR, 9.0, 9.0, 9.0, 9.0, 9] for k in range(2)]          # must not overwrite biquote
    bad = [[first - 10 * HOUR, float("nan"), 1, 1, 1, 1]]
    src = tmp_path / "npz"
    src.mkdir()
    np.savez_compressed(src / "EURUSD.npz", **{"1h": np.array(bad + older + overlap, dtype=float),
                                                "1m": np.array([[first - MINUTE, 1, 1, 1, 1, 1]], dtype=float)})
    np.savez_compressed(src / "BTCUSDT.npz", **{"1h": np.array(older, dtype=float)})
    monkeypatch.setattr(sys, "argv", ["import_history", str(src)])
    assert import_history.main() == 0
    rows = arch.read("EURUSD", "1h", None, None)
    assert [r[0] for r in rows[:3]] == [r[0] for r in older] and rows[3][1] == 1.1
    cov = arch.coverage("EURUSD", "1h")
    assert cov.first_ts == older[0][0] and cov.exhausted
    assert MarketStore(tmp_path / "bq-minutes-archive.sqlite").count("EURUSD", "1m") == 0   # no biquote 1m yet
    assert import_history.main() == 0                                                       # re-run: nothing new
    assert arch.count("EURUSD", "1h") == 8
    # older bars that stop months before the first stored one are not imported (no hole in the chart)
    arch.upsert("GBPUSD", "1h", [[first, 1, 1, 1, 1, 1]])
    np.savez_compressed(src / "GBPUSD.npz", **{"1h": np.array([[first - 30 * DAY, 2, 2, 2, 2, 1]], dtype=float)})
    assert import_history.main() == 0
    assert arch.count("GBPUSD", "1h") == 1


# ------------------------------------------------------------ Sunday sessions
SUN = 1793491200000      # 2026-11-01 00:00 UTC, a Sunday


def test_day_bucket_moves_sunday_to_monday():
    from app.markets.biquote import day_bucket
    assert datetime.fromtimestamp(SUN / 1000, tz=timezone.utc).weekday() == 6
    assert day_bucket(SUN + 22 * HOUR) == SUN + DAY                 # Sunday 22:00 -> Monday's bar
    assert day_bucket(SUN + DAY + 5 * HOUR) == SUN + DAY            # Monday stays Monday
    assert day_bucket(SUN - DAY + 5 * HOUR) == SUN - DAY            # Saturday stays (nothing trades anyway)


def test_merge_sundays_folds_into_monday():
    from app.markets.biquote import merge_sundays
    fri, mon, nxt = SUN - 2 * DAY, SUN + DAY, SUN + 8 * DAY
    rows = [[fri, 1, 2, 0.5, 1.5, 10], [SUN, 1.6, 1.8, 1.55, 1.7, 1], [mon, 1.7, 3, 1.2, 2.5, 20],
            [SUN + 7 * DAY, 2.6, 2.7, 2.5, 2.6, 2]]                   # a Sunday whose Monday is not there yet
    assert merge_sundays(rows) == [[fri, 1, 2, 0.5, 1.5, 10], [mon, 1.6, 3, 1.2, 2.5, 21], [nxt, 2.6, 2.7, 2.5, 2.6, 2]]


async def test_daily_fetch_and_stream_have_no_sunday_bars(bq):
    from app.markets.biquote import day_bucket
    bars = await bq.fetch_candles("XAUUSD", "1D", NOW - 30 * DAY, NOW)
    assert bars and all(datetime.fromtimestamp(b[0] / 1000, tz=timezone.utc).weekday() != 6 for b in bars)
    assert all(day_bucket(b[0]) == b[0] for b in bars)
    s = BiquoteStream(bq, None)
    topic = s.kline_topic("XAUUSD", "1D")
    await s.subscribe(topic)
    s.on_tick(_tick("XAUUSD", 4000.0, SUN + 22 * HOUR))
    s.on_tick(_tick("XAUUSD", 4010.0, SUN + DAY + HOUR))             # Monday: same bar, no roll-over
    assert s._bars[topic][0] == SUN + DAY and s._bars[topic][1] == 4000.0 and s._bars[topic][4] == 4010.0
    assert topic not in s._closing
    await s.stop()


def test_stored_sunday_bars_are_folded_once(tmp_path):
    from app.candles.store import MarketStore
    from scripts.merge_sunday_bars import merge_store
    st = MarketStore(tmp_path / "bq-archive.sqlite")
    st.upsert("EURUSD", "1D", [[SUN - 2 * DAY, 1, 2, 0.5, 1.5, 10], [SUN, 1.6, 1.8, 1.55, 1.7, 1],
                               [SUN + DAY, 1.7, 3, 1.2, 2.5, 20]])
    st.set_coverage("EURUSD", "1D", SUN - 2 * DAY, SUN + DAY, True)
    assert merge_store(st, dry_run=True) == {"EURUSD": 1} and st.count("EURUSD", "1D") == 3
    assert merge_store(st) == {"EURUSD": 1}
    assert st.read("EURUSD", "1D", None, None) == [[SUN - 2 * DAY, 1, 2, 0.5, 1.5, 10], [SUN + DAY, 1.6, 3, 1.2, 2.5, 21]]
    assert st.coverage("EURUSD", "1D").exhausted
    assert merge_store(st) == {}                                      # second run: nothing to do
