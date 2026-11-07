"""Hyperliquid: catalogue, candles window, archive, store routing, search ranking, live stream, OI, alerts feed."""
import json
import time

import httpx
import pytest

from app import limits
from app.alerts import HyperliquidFeed
from app.candles.service import CandleService
from app.candles.store import StoreRegistry
from app.derivs import DerivStore, Derivs, HLOIRecorder, _answer_hl
from app.hl_archive import HLArchiver
from app.intervals import DAY, HOUR, INTERVALS
from app.maintenance import emergency, retention
from app.markets.base import SymbolInfo
from app.markets.hyperliquid import WINDOW, HyperliquidProvider, candle_weight, price_decimals
from app.realtime.hl_ws import HLStream
from app.symbols import SymbolRegistry

NOW = int(time.time() * 1000) // HOUR * HOUR


def _asset(name, sz=2, delisted=False):
    return {"name": name, "szDecimals": sz, **({"isDelisted": True} if delisted else {})}


def _ctx(px, vol, oi="10", prev=None):
    return {"markPx": str(px), "midPx": str(px), "dayNtlVlm": str(vol), "openInterest": oi, "prevDayPx": str(prev or px)}


class FakeHL:
    """Answers the info endpoint like Hyperliquid; candles exist only for the newest WINDOW bars."""

    def __init__(self):
        self.calls: list[dict] = []
        self.dead_coins = {"km:US500", "OLD", "xyz:DXY"}   # delisted: no new candles
        self.broken: set[str] = set()                      # exchanges answering with an error
        self.dexes = {
            "": ([_asset("BTC", 5), _asset("OLD", 1, True)], [_ctx(77000, 3e9), _ctx(1, 0)]),
            "xyz": ([_asset("xyz:TSLA", 3), _asset("xyz:NVDA", 3), _asset("xyz:GOLD", 4), _asset("xyz:DXY", 2, True)],
                    [_ctx(406.03, 5e7, prev=400), _ctx(207, 1.2e8), _ctx(3955.4, 9e7), _ctx(100, 0)]),
            "io": ([_asset("io:NVDA", 3)], [_ctx(206.6, 3e6)]),
            "km": ([_asset("km:US500", 2, True)], [_ctx(6000, 0)]),
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        t = body["type"]
        if t == "perpDexs":
            return httpx.Response(200, json=[None] + [{"name": d, "fullName": d.upper()} for d in self.dexes if d])
        if t in ("metaAndAssetCtxs", "allMids") and body.get("dex", "") in self.broken:
            return httpx.Response(422, text="broken exchange")
        if t == "metaAndAssetCtxs":
            assets, ctxs = self.dexes[body.get("dex", "")]
            return httpx.Response(200, json=[{"universe": assets}, ctxs])
        if t == "allMids":
            assets, ctxs = self.dexes[body.get("dex", "")]
            return httpx.Response(200, json={a["name"]: c["midPx"] for a, c in zip(assets, ctxs)} | {"@1": "1.0"})
        if t == "candleSnapshot":
            req = body["req"]
            if req["coin"] in self.dead_coins:
                return httpx.Response(200, json=[])
            ivms = {"1h": HOUR, "4h": 4 * HOUR, "1d": DAY, "1m": 60_000}[req["interval"]]
            newest = int(time.time() * 1000) // ivms * ivms
            oldest = newest - (WINDOW - 1) * ivms
            lo = max(req["startTime"], oldest)
            first = -(-lo // ivms) * ivms
            rows = [{"t": t, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10"}
                    for t in range(first, min(req["endTime"], newest) + 1, ivms)]
            return httpx.Response(200, json=rows)
        if t == "fundingHistory":
            return httpx.Response(200, json=[{"coin": body["coin"], "fundingRate": "0.0000125", "time": NOW - 2 * HOUR + 56}])
        return httpx.Response(422, text="bad")


@pytest.fixture
def hl(monkeypatch):
    fake = FakeHL()
    for k in ("HL_RATE", "HL_BURST", "HL_BG_RATE", "HL_BG_BURST"):   # no pacing in tests
        monkeypatch.setattr(limits, k, 1e9)
    limits._hl.clear()
    p = HyperliquidProvider(httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    p.fake = fake
    return p


# -------------------------------------------------------------------- catalogue
async def test_catalogue_names_primary_and_delisted(hl):
    listed = {s.symbol: s for s in await hl.symbols()}
    assert set(listed) == {"BTC", "xyz:TSLA", "xyz:NVDA", "xyz:GOLD", "io:NVDA"}   # delisted ones are not listed
    tsla = listed["xyz:TSLA"]
    assert (tsla.id, tsla.display, tsla.name, tsla.type, tsla.exchange) == \
        ("hl:xyz:TSLA", "TSLA", "Tesla Perpetual", "stock", "trade.xyz")
    assert tsla.price_precision == 2                     # 406.03: 5 significant figures, ≤ 6 - szDecimals
    assert "XAU" in listed["xyz:GOLD"].aliases
    assert listed["xyz:NVDA"].extra["primary"] and not listed["io:NVDA"].extra["primary"]   # by volume
    assert listed["io:NVDA"].aliases == []
    assert listed["BTC"].display == "BTC-USDC" and listed["BTC"].type == "crypto"
    old = await hl.resolve("km:US500")                   # delisted: still opens (old drawings, archive)
    assert old is not None and old.name.endswith("(delisted)")
    assert hl.dexes() == ["", "io", "xyz"]
    assert hl.archive_coins()[0] == "xyz:NVDA" and hl.archive_coins()[-1] == "BTC"   # stocks first, by volume


async def test_dead_exchange_is_skipped_on_refresh(hl):
    await hl.symbols()
    hl.fake.calls.clear()
    await hl._refresh(background=False)
    dexes = [c.get("dex", "") for c in hl.fake.calls if c["type"] == "metaAndAssetCtxs"]
    assert "km" not in dexes and "xyz" in dexes
    assert (await hl.resolve("km:US500")) is not None    # still known


def test_price_decimals():
    assert price_decimals(77430.5, 5) == 0
    assert price_decimals(1.1193, 1) == 4
    assert price_decimals(0.040077, 0) == 6
    assert price_decimals(3955.4, 4) == 1
    assert price_decimals(None, 3) == 3
    assert candle_weight(5000) == 20 + 84


# ----------------------------------------------------------------------- candles
async def test_candles_are_clamped_to_the_window(hl):
    bars = await hl.fetch_bars("xyz:TSLA", "1h", 1, NOW)
    assert WINDOW - 2 <= len(bars) <= WINDOW
    req = [c for c in hl.fake.calls if c["type"] == "candleSnapshot"][-1]["req"]
    assert req["startTime"] <= hl.history_floor("1h") and req["interval"] == "1h"
    assert hl.history_floor("1h") > bars[0][0]          # the chart's floor keeps a small margin inside the window
    assert await hl.fetch_bars("xyz:TSLA", "1h", 1, NOW - (WINDOW + 10) * HOUR) == []   # nothing older exists


async def test_quotes_and_mids(hl):
    await hl.symbols()
    q = {x.id: x for x in await hl.quotes(["xyz:TSLA", "BTC"])}
    assert q["hl:xyz:TSLA"].price == pytest.approx(406.03)
    assert q["hl:xyz:TSLA"].change_pct == pytest.approx((406.03 / 400 - 1) * 100)
    mids = await HyperliquidFeed(hl).prices({"xyz:TSLA"})
    assert mids["xyz:TSLA"] == pytest.approx(406.03) and "BTC" not in mids     # only the exchanges asked for
    assert "@1" not in await hl.mids({"BTC"})                                   # spot pairs are left out


# ------------------------------------------------------------------------ archive
def _service(tmp_path, hl):
    stores = StoreRegistry(tmp_path)
    return CandleService({"hl": hl}, stores, backfill_bars=3000), stores


async def test_archive_seeds_then_tops_up_and_survives_cleanup(tmp_path, hl, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "CANDLES_DIR", tmp_path)
    svc, stores = _service(tmp_path, hl)
    arch = HLArchiver(hl, svc)
    n = await arch.update("xyz:TSLA", "1h")
    assert WINDOW - 3 <= n <= WINDOW
    store = stores.get("hl-archive")
    cov = store.coverage("xyz:TSLA", "1h")
    assert cov.exhausted and cov.first_ts < hl.history_floor("1h")
    assert stores.get("hl").coverage("xyz:TSLA", "1h") is None        # archived intervals never go to the cache
    assert await arch.update("xyz:TSLA", "1h") is None                # fresh: skipped
    # a day later only the new bars are fetched
    later = NOW + DAY
    hl.fake.calls.clear()
    store.set_coverage("xyz:TSLA", "1h", cov.first_ts, cov.last_ts - DAY, True)  # pretend the last day is missing
    await arch.update("xyz:TSLA", "1h", now=later)
    req = [c for c in hl.fake.calls if c["type"] == "candleSnapshot"][0]["req"]
    assert req["startTime"] == cov.last_ts - DAY + HOUR
    # the cache cleanup works on the cache stores only: the archive keeps everything
    before = store.count("xyz:TSLA", "1h")
    retention(stores, {"hl": hl}, now=time.time() + 400 * 86400)
    emergency(stores, {"hl": hl})
    assert store.count("xyz:TSLA", "1h") == before


async def test_chart_reads_archived_bars_older_than_the_window(tmp_path, hl):
    svc, stores = _service(tmp_path, hl)
    store = stores.get("hl-archive")
    floor = hl.history_floor("1h")
    old = [[floor - k * HOUR, 1, 2, 0.5, 1.5, 1] for k in range(20, 1020)][::-1]   # 1000 bars Hyperliquid no longer has
    store.upsert("xyz:TSLA", "1h", old)
    await HLArchiver(hl, svc).update("xyz:TSLA", "1h")
    cov = store.coverage("xyz:TSLA", "1h")
    assert cov.first_ts == old[0][0] and cov.exhausted
    info = await hl.resolve("xyz:TSLA")
    page = await svc.get_bars(info, "1h", floor + HOUR, 300)      # scrolled back to the window edge
    assert page.bars[0][0] < floor and page.bars[0][0] >= old[0][0]
    assert page.more
    page2 = await svc.get_bars(info, "1h", old[0][0] + 50 * HOUR, 300)
    assert page2.bars[0][0] == old[0][0] and not page2.more


async def test_week_and_month_come_from_days(tmp_path, hl):
    svc, _ = _service(tmp_path, hl)
    info = await hl.resolve("xyz:GOLD")
    page = await svc.get_bars(info, "1W", None, 10)
    assert page.native == "1D"
    assert svc.native_for(hl, "2h") == "1h" and svc.native_for(hl, "1M") == "1D"


# ------------------------------------------------------------------------- search
async def test_search_keeps_bybit_first_for_crypto(hl):
    bybit = type("P", (), {})()
    bybit.market = "bybit"
    bybit.native_intervals = frozenset()

    async def syms():
        return [SymbolInfo(id="bybit:BTCUSDT", market="bybit", symbol="BTCUSDT", name="BTC / USDT Perpetual",
                           type="crypto", exchange="BYBIT", aliases=["BTC"])]
    bybit.symbols = syms
    hl.fake.dexes["para"] = ([_asset("para:BTCD", 1)], [_ctx(60.3, 1e4)])
    reg = SymbolRegistry({"bybit": bybit, "hl": hl})
    assert (await reg.search("BTC", 5))[0].id == "bybit:BTCUSDT"
    assert (await reg.search("TSLA", 5))[0].id == "hl:xyz:TSLA"
    assert (await reg.search("XAU", 5))[0].id == "hl:xyz:GOLD"
    nv = [s.id for s in await reg.search("NVDA", 5)]
    assert nv[:2] == ["hl:xyz:NVDA", "hl:io:NVDA"]
    assert (await reg.get("GOLD")).id == "hl:xyz:GOLD"
    # private instance: the exchange listing of a stock comes before its Hyperliquid perpetual
    us = type("P", (), {"market": "us", "native_intervals": frozenset()})()

    async def us_syms():
        return [SymbolInfo(id="us:TSLA", market="us", symbol="TSLA", name="Tesla", type="stock", exchange="NASDAQ")]
    us.symbols = us_syms
    reg2 = SymbolRegistry({"us": us, "hl": hl})
    assert [s.id for s in await reg2.search("TSLA", 5)][:2] == ["us:TSLA", "hl:xyz:TSLA"]


# -------------------------------------------------------------------------- stream
async def test_stream_parses_candles_for_subscribed_topics():
    got = []

    async def on_bar(sid, interval, bar, closed):
        got.append((sid, interval, bar))
    s = HLStream(on_bar)
    s._topics[s.kline_topic("xyz:TSLA", "1h")] = 1
    msg = {"channel": "candle", "data": {"t": NOW, "T": NOW + HOUR - 1, "s": "xyz:TSLA", "i": "1h",
                                         "o": "1", "c": "2", "h": "3", "l": "0.5", "v": "7", "n": 3}}
    await s.handle(json.dumps(msg))
    await s.handle(json.dumps({**msg, "data": {**msg["data"], "s": "xyz:NVDA"}}))   # not subscribed
    await s.handle("not json")
    assert got == [("hl:xyz:TSLA", "1h", [NOW, 1.0, 3.0, 0.5, 2.0, 7.0])]
    assert s._msg("1D|BTC", "subscribe") == {"method": "subscribe", "subscription": {"type": "candle", "coin": "BTC",
                                                                                      "interval": "1d"}}
    assert HLStream(on_bar, max_topics=5000).max_topics == 900   # Hyperliquid: 1000 per IP


# --------------------------------------------------------------------- derivatives
async def test_open_interest_is_recorded_and_bucketed(tmp_path, hl):
    d = Derivs(DerivStore(tmp_path / "derivs.sqlite"), None, hl)
    rec = HLOIRecorder(d)
    for k in range(8):                                    # two hours of 15-minute points
        hl.fake.dexes["xyz"][1][0]["openInterest"] = str(100 + k)
        assert await rec.record(NOW - 2 * HOUR + k * 15 * 60_000) == 5
    hourly = await _answer_hl(d, "oi", "xyz:TSLA", "1h", NOW - 3 * HOUR, NOW)
    assert hourly["points"] == [[NOW - 2 * HOUR, 103.0], [NOW - HOUR, 107.0]]   # the last value of each hour
    fine = await _answer_hl(d, "oi", "xyz:TSLA", "15m", NOW - 3 * HOUR, NOW)
    assert len(fine["points"]) == 8 and fine["exhausted"]
    liq = await _answer_hl(d, "liq", "xyz:TSLA", "1h", NOW - DAY, NOW)
    assert liq["unavailable"] == "source"
    fund = await _answer_hl(d, "funding", "xyz:TSLA", "1h", NOW - DAY, NOW)
    assert fund["points"] == [[NOW - 2 * HOUR, 0.0000125]]   # stamped on the hour
    # after 30 days each hour keeps its LAST point — also when the :00 poll was missed
    h1, h2 = NOW - 33 * DAY, NOW - 33 * DAY + HOUR
    d.store.add_hl_oi([("xyz:TSLA", h1 + k * 15 * 60_000, 10.0 + k) for k in range(4)] +
                      [("xyz:TSLA", h2 + k * 15 * 60_000, 20.0 + k) for k in range(1, 4)])
    assert d.store.prune(now=NOW / 1000)["hl_oi_rows"] == 5
    assert d.store.hl_oi("xyz:TSLA", h1, h2 + HOUR, HOUR) == [[h1, 13.0], [h2, 23.0]]


def test_intervals_used_by_hyperliquid_exist():
    from app.markets.hyperliquid import IV
    assert set(IV) <= set(INTERVALS)


# ------------------------------------------------------- Astra round 6 regressions
async def test_chart_requests_never_narrow_archive_coverage(tmp_path, hl):
    svc, stores = _service(tmp_path, hl)
    store = stores.get("hl-archive")
    ws = hl.window_start("1h")
    old = [[ws - k * HOUR, 1, 2, 0.5, 1.5, 1] for k in range(1, 801)][::-1]   # 800 bars only the archive has
    store.upsert("xyz:TSLA", "1h", old)
    await HLArchiver(hl, svc).update("xyz:TSLA", "1h")
    info = await hl.resolve("xyz:TSLA")
    to, seen = None, 0
    for _ in range(30):                                   # scroll back page by page, like the chart does
        page = await svc.get_bars(info, "1h", to, 1000)
        seen += len(page.bars)
        if not page.more:
            break
        to = page.bars[0][0]
    assert page.bars[0][0] == old[0][0] and not page.more
    assert seen >= 800 + WINDOW - 5
    cov = store.coverage("xyz:TSLA", "1h")
    assert cov.first_ts == old[0][0] and cov.exhausted


async def test_chart_first_bootstrap_keeps_the_whole_window(tmp_path, hl):
    svc, stores = _service(tmp_path, hl)
    info = await hl.resolve("xyz:NVDA")
    to = None
    for _ in range(30):
        page = await svc.get_bars(info, "1h", to, 1000)
        if not page.more:
            break
        to = page.bars[0][0]
    store = stores.get("hl-archive")
    cov = store.coverage("xyz:NVDA", "1h")
    assert cov.exhausted and cov.first_ts <= hl.window_start("1h") + HOUR   # no safety-margin bars left behind
    assert await HLArchiver(hl, svc).update("xyz:NVDA", "1h") is None       # the archive job sees it complete


async def test_long_delisted_market_still_opens(tmp_path, hl):
    svc, stores = _service(tmp_path, hl)
    store = stores.get("hl-archive")
    end = NOW - 130 * DAY
    bars = [[end - k * HOUR, 1, 2, 0.5, 1.5, 1] for k in range(600)][::-1]
    store.upsert("km:US500", "1h", bars)
    store.set_coverage("km:US500", "1h", bars[0][0], bars[-1][0], True)
    info = await hl.resolve("km:US500")
    page = await svc.get_bars(info, "1h", None, 300)
    assert len(page.bars) == 300 and page.bars[-1][0] == end and page.more


async def test_unknown_ids_never_reach_the_upstream(tmp_path, hl):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.derivs import _derivs
    d = Derivs(DerivStore(tmp_path / "derivs.sqlite"), None, hl)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(derivs=d, symbols=SymbolRegistry({"hl": hl}))),
                          scope={"state": {"client_ip": "198.51.100.7"}})
    calls = len(hl.fake.calls)
    with pytest.raises(HTTPException) as e:
        await _derivs(req, "hl:xyz:NOPE")
    assert e.value.status_code == 404
    assert all(c["type"] != "fundingHistory" for c in hl.fake.calls[calls:])
    assert (await _derivs(req, "hl:xyz:TSLA"))[2].symbol == "xyz:TSLA"


async def test_one_broken_exchange_does_not_hide_the_others(tmp_path, hl):
    await hl.symbols()
    hl.fake.broken.add("io")
    mids = await hl.mids({"xyz:TSLA", "io:NVDA"})
    assert "xyz:TSLA" in mids and "io:NVDA" not in mids
    d = Derivs(DerivStore(tmp_path / "derivs.sqlite"), None, hl)
    assert await HLOIRecorder(d).record(NOW) == 4          # main + xyz markets, io skipped


def test_budgets_hold_a_full_request_and_stay_under_the_ip_limit():
    full = candle_weight(WINDOW)
    assert limits.HL_BURST >= full and limits.HL_BG_BURST >= full   # the cost of a request is never clamped
    assert limits.HL_BURST + limits.HL_RATE * 60 + limits.HL_BG_BURST + limits.HL_BG_RATE * 60 < 1200


async def test_reconnect_does_not_resubscribe_a_topic_dropped_meanwhile(monkeypatch):
    import asyncio
    from app.realtime import hl_ws
    sent: list[dict] = []
    gone = asyncio.Event()

    class Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send(self, m):
            sent.append(json.loads(m))

        def __aiter__(self):
            return self

        async def __anext__(self):
            await gone.wait()
            raise StopAsyncIteration

    monkeypatch.setattr(hl_ws.websockets, "connect", lambda *a, **k: Conn())

    async def on_bar(*a):
        pass
    s = HLStream(on_bar)
    s._budget = limits.AsyncBucket(5, 1)                  # one command now, the next after 0.2 s
    s._topics = {"1h|BTC": 1, "1h|ETH": 1}
    run = asyncio.create_task(s._run())
    await asyncio.sleep(0.05)                             # connected; BTC sent, ETH waits for the budget
    await s.unsubscribe("1h|ETH")                         # the last viewer leaves meanwhile
    await asyncio.sleep(0.4)
    subs = [m["subscription"]["coin"] for m in sent if m["method"] == "subscribe"]
    assert subs == ["BTC"]
    gone.set()
    await s.stop()
    run.cancel()


async def test_minutes_are_archived_for_hip3_markets_only(tmp_path, hl, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "CANDLES_DIR", tmp_path)
    svc, stores = _service(tmp_path, hl)
    assert svc.store_for(hl, "1m", "xyz:TSLA").path.name == "hl-minutes-archive.sqlite"
    assert svc.store_for(hl, "1m", "BTC").path.name == "hl.sqlite"
    assert svc.store_for(hl, "3m", "xyz:TSLA").path.name == "hl.sqlite"          # 3m is not archived
    assert svc.store_for(hl, "1h", "BTC").path.name == "hl-archive.sqlite"
    assert hl.archived("BTC") == ["1D", "4h", "1h"]
    assert hl.archived("xyz:TSLA") == ["1D", "4h", "1h", "30m", "15m", "5m", "1m"]
    arch = HLArchiver(hl, svc)
    assert await arch.update("xyz:TSLA", "1m") > 0
    assert stores.get("hl-minutes-archive").coverage("xyz:TSLA", "1m").exhausted
    before = stores.get("hl-minutes-archive").count("xyz:TSLA", "1m")
    retention(stores, {"hl": hl}, now=time.time() + 400 * 86400)
    emergency(stores, {"hl": hl})
    assert stores.get("hl-minutes-archive").count("xyz:TSLA", "1m") == before
    info = await hl.resolve("xyz:TSLA")
    page = await svc.get_bars(info, "1m", None, 100)
    assert len(page.bars) == 100
