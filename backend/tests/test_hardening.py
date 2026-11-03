"""Public-instance hardening (Astra review 2026-11-03): SPA path traversal, body cap, backfill budget, WS validation."""
import asyncio
import json

import pytest

from app import limits, main
from app.candles.service import CandleService, now_ms
from app.candles.store import StoreRegistry
from app.guard import Guard
from app.intervals import DAY, MINUTE
from app.markets.base import SymbolInfo
from app.realtime.hub import Client, Hub
from tests.test_service import FakeProvider


# ------------------------------------------------------------------ SPA files
@pytest.mark.parametrize("path", [
    "../../README.md", "..%2f..%2fREADME.md", "../backend/app/config.py", "/etc/passwd", "assets/../../../.env",
    "..\\..\\README.md", "a\x00b", "", ".",
])
def test_spa_never_serves_outside_dist(path, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("index")
    (dist / "assets" / "app.js").write_text("js")
    (tmp_path / "README.md").write_text("secret")
    (dist / "link").symlink_to(tmp_path / "README.md")
    monkeypatch.setattr(main, "DIST_ROOT", dist.resolve())
    assert main._dist_file(path) is None
    assert main._dist_file("link") is None                  # symlink pointing outside
    assert main._dist_file("assets/app.js") == (dist / "assets" / "app.js").resolve()


# ------------------------------------------------------------------ body cap
async def _call(app, method, body, headers=()):
    sent = []
    chunks = [body[i:i + 1000] for i in range(0, len(body), 1000)] or [b""]

    async def receive():
        c = chunks.pop(0)
        return {"type": "http.request", "body": c, "more_body": bool(chunks)}

    async def send(msg):
        sent.append(msg)
    scope = {"type": "http", "method": method, "path": "/api/settings", "headers": list(headers), "client": ("127.0.0.1", 1)}
    await app(scope, receive, send)
    return sent[0]["status"], sent


async def test_guard_rejects_big_bodies_before_the_app(monkeypatch):
    monkeypatch.setattr(limits, "profile", lambda: limits.PUBLIC)
    reached = []

    async def app(scope, receive, send):
        reached.append((await receive())["body"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    g = Guard(app)
    status, _ = await _call(g, "PUT", b"x" * 10, [(b"content-length", b"99999999")])
    assert status == 413 and not reached
    status, _ = await _call(g, "PUT", b"x" * (limits.PUBLIC.body_bytes + 1))   # no content-length, streamed
    assert status == 413 and not reached
    status, sent = await _call(g, "PUT", b'{"a":1}')
    assert status == 200 and reached == [b'{"a":1}']


async def test_guard_rate_limits_by_cloudflare_ip(monkeypatch):
    monkeypatch.setattr(limits, "profile", lambda: limits.PUBLIC)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    g = Guard(app)
    codes = [(await _call(g, "GET", b"", [(b"cf-connecting-ip", b"203.0.113.9")]))[0] for _ in range(80)]
    assert codes.count(429) > 0
    assert (await _call(g, "GET", b"", [(b"cf-connecting-ip", b"203.0.113.10")]))[0] == 200  # other visitor unaffected


def test_cf_header_only_trusted_from_the_tunnel():
    assert limits.client_ip([(b"cf-connecting-ip", b"1.2.3.4")], "127.0.0.1") == "1.2.3.4"
    assert limits.client_ip([(b"cf-connecting-ip", b"1.2.3.4")], "8.8.8.8") == "8.8.8.8"


# ------------------------------------------------------------- backfill budget
SYM = SymbolInfo(id="fake:X", market="fake", symbol="X", name="X", type="crypto", exchange="F")


async def test_one_bar_request_cannot_trigger_a_huge_backfill(tmp_path):
    now = now_ms()
    prov = FakeProvider(first_ts=now - 400 * DAY)
    svc = CandleService({"fake": prov}, StoreRegistry(tmp_path), backfill_bars=3000)
    await svc.get_bars(SYM, "1m", now - 300 * DAY, 1)         # an old window first
    await svc.get_bars(SYM, "1m", None, 1)                    # then "just the current bar"
    widest = max((e - s) / MINUTE for _, s, e in prov.calls)
    assert widest <= 3000 + 2, f"fetched {widest:.0f} bars in one go"


async def test_tail_cache_avoids_refetching_the_live_bar(tmp_path):
    now = now_ms()
    prov = FakeProvider(first_ts=now - 5 * DAY)
    svc = CandleService({"fake": prov}, StoreRegistry(tmp_path), backfill_bars=3000, tail_ttl_ms=3000)
    await svc.get_bars(SYM, "1m", None, 100)
    n = len(prov.calls)
    for _ in range(20):
        await svc.get_bars(SYM, "1m", None, 100)
    assert len(prov.calls) - n <= 1


# ------------------------------------------------------------------ websocket
class FakeSymbols:
    def __init__(self):
        self.known = {"bybit:BTCUSDT": SymbolInfo(id="bybit:BTCUSDT", market="bybit", symbol="BTCUSDT", name="BTC",
                                                  type="crypto", exchange="Bybit")}

    async def get(self, ident):
        return self.known.get(ident)


class FakeStream:
    def __init__(self):
        self.topics = []

    @staticmethod
    def kline_topic(symbol, interval):
        return f"kline.{interval}.{symbol}"

    @staticmethod
    def ticker_topic(symbol):
        return f"tickers.{symbol}"

    async def subscribe(self, topic, reserve=0):
        self.topics.append(topic)
        return True

    async def unsubscribe(self, topic):
        self.topics.remove(topic)


class FakeProv:
    native_intervals = frozenset({"1m", "1h"})


def _hub(monkeypatch, profile=limits.PUBLIC):
    monkeypatch.setattr(limits, "profile", lambda: profile)
    hub = Hub(candles=None, symbols=FakeSymbols(), providers={"bybit": FakeProv()})
    hub._bybit = {"bybit": FakeStream()}
    return hub


def _client():
    return Client(ws=None, ip="1.1.1.1", queue=asyncio.Queue(maxsize=8))


async def test_ws_ignores_invalid_subscriptions(monkeypatch):
    hub, c = _hub(monkeypatch), _client()
    for i in range(50):
        await hub._on_message(c, {"op": "sub", "ch": "bars", "id": "bybit:BTCUSDT", "interval": f"invalid-{i}"})
    await hub._on_message(c, {"op": "sub", "ch": "quotes", "ids": [f"missing-{i}" for i in range(500)]})
    await hub._on_message(c, {"op": "sub", "ch": "bars", "id": {"x": 1}, "interval": "1m"})
    assert not c.bars and not c.quotes and not hub._bar_refs and not hub._quote_refs and not hub._bar_tasks


async def test_ws_caps_streams_per_connection(monkeypatch):
    p = limits.Profile(**{**limits.PUBLIC.__dict__, "ws_bar_subs": 1})
    hub, c = _hub(monkeypatch, p), _client()
    await hub._on_message(c, {"op": "sub", "ch": "bars", "id": "bybit:BTCUSDT", "interval": "1m"})
    await hub._on_message(c, {"op": "sub", "ch": "bars", "id": "bybit:BTCUSDT", "interval": "1h"})
    assert c.bars == {("bybit:BTCUSDT", "1m")}
    assert json.loads(c.queue.get_nowait())["ch"] == "error"


async def test_ws_slow_client_is_dropped_not_awaited(monkeypatch):
    hub, c = _hub(monkeypatch), _client()
    hub.clients.add(c)
    c.bars.add(("bybit:BTCUSDT", "1m"))

    class WS:
        async def close(self, code=1000):
            pass
    c.ws = WS()
    for i in range(20):  # queue holds 8: the rest overflows instead of blocking the broadcaster
        await hub._broadcast_bar("bybit:BTCUSDT", "1m", [i, 1, 1, 1, 1, 1], closed=False)
    assert c.closed


# ------------------------------------------------- Astra round 2 (2026-11-03)
from app.userdata.db import LimitError, Quota, UserDB  # noqa: E402

Q = Quota(user_bytes=50_000, db_bytes=None)


def _put(db, i, symbol="bybit:A", data='{"p":1}', **kw):
    kw = {"max_total": 10, "max_per_symbol": 3, "quota": Q, **kw}
    db.put_drawing_checked("u", f"d{i}", symbol, "segment", data, **kw)


def test_moving_a_drawing_respects_the_target_symbol_cap(tmp_path):
    db = UserDB(tmp_path / "a.sqlite")
    for i in range(3):
        _put(db, i, "bybit:A")
    for i in range(3, 6):
        _put(db, i, "bybit:B")
    with pytest.raises(LimitError) as e:
        _put(db, 3, "bybit:A")             # move d3 from B onto the full A
    assert e.value.status == 409
    _put(db, 0, "bybit:A", data='{"p":2}')  # editing in place is fine


def test_quota_counts_the_stored_bytes(tmp_path):
    db = UserDB(tmp_path / "a.sqlite")
    big = limits.dumps({"s": "é" * 9000})   # 18 KB of UTF-8 as stored
    _put(db, 0, data=big, quota=Quota(user_bytes=40_000, db_bytes=None))
    _put(db, 1, data=big, quota=Quota(user_bytes=40_000, db_bytes=None))
    with pytest.raises(LimitError):
        _put(db, 2, data=big, quota=Quota(user_bytes=40_000, db_bytes=None))
    assert db.user_bytes("u") == 2 * (len(big.encode()) + len("segment") + len("bybit:A"))


def test_drawings_are_read_in_bounded_pages(tmp_path):
    db = UserDB(tmp_path / "a.sqlite")
    for i in range(25):
        _put(db, i, data=limits.dumps({"s": "x" * 1000}), max_total=100, max_per_symbol=100)
    seen, off, pages = [], 0, 0
    while off is not None:
        page, off = db.drawings_page("u", "bybit:A", off, 10, 4000)
        assert len(page) <= 10 and pages < 20
        seen += [d["id"] for d in page]
        pages += 1
    assert sorted(seen) == sorted(f"d{i}" for i in range(25)) and pages > 3


def test_settings_cap_holds_across_updates(tmp_path):
    db = UserDB(tmp_path / "a.sqlite")
    db.put_settings_checked("u", {f"a{i}": 1 for i in range(60)}, max_keys=64, max_bytes=10_000, quota=Q)
    with pytest.raises(LimitError):
        db.put_settings_checked("u", {f"b{i}": 1 for i in range(10)}, max_keys=64, max_bytes=10_000, quota=Q)


async def test_historical_request_does_not_poison_the_live_tail(tmp_path):
    now = now_ms()
    prov = FakeProvider(first_ts=now - 5 * DAY)
    svc = CandleService({"fake": prov}, StoreRegistry(tmp_path), backfill_bars=3000, tail_ttl_ms=3000)
    old = now - 10 * MINUTE
    await svc.get_bars(SYM, "1m", old, 600)
    await svc.get_bars(SYM, "1m", old, 600)
    page = await svc.get_bars(SYM, "1m", None, 600)
    assert page.bars[-1][0] >= now - 2 * MINUTE, "current bars missing"


async def test_full_cache_still_serves_the_whole_window(tmp_path):
    now = now_ms()
    prov = FakeProvider(first_ts=now - 5 * DAY)
    svc = CandleService({"fake": prov}, StoreRegistry(tmp_path), backfill_bars=3000)
    await svc.get_bars(SYM, "1m", None, 100)                 # 100 bars stored
    svc.can_persist = lambda: False                           # cache now full
    page = await svc.get_bars(SYM, "1m", None, 600)
    assert len(page.bars) >= 590


async def test_ws_quotes_cannot_take_the_chart_topics(monkeypatch):
    p = limits.Profile(**{**limits.PUBLIC.__dict__, "upstream_topics": 5, "upstream_bar_reserve": 2})
    hub = _hub(monkeypatch, p)
    from app.realtime.bybit_ws import BybitStream

    async def noop(*a):
        return None
    stream = BybitStream("linear", noop, noop, max_topics=5)
    stream._ensure_running = lambda: None
    hub._bybit = {"bybit": stream}
    for i in range(6):
        hub.symbols.known[f"bybit:C{i}USDT"] = SymbolInfo(id=f"bybit:C{i}USDT", market="bybit", symbol=f"C{i}USDT",
                                                          name="x", type="crypto", exchange="Bybit")
    c = _client()
    await hub._on_message(c, {"op": "set", "ch": "quotes", "ids": [f"bybit:C{i}USDT" for i in range(6)]})
    assert len(c.quotes) == 3                                # 5 topics - 2 kept for charts
    other = _client()
    await hub._on_message(other, {"op": "sub", "ch": "bars", "id": "bybit:BTCUSDT", "interval": "1m"})
    assert other.bars == {("bybit:BTCUSDT", "1m")}           # a chart can still open


async def test_ws_change_budget_counts_each_subscription(monkeypatch):
    hub, c = _hub(monkeypatch), _client()
    c.changes = limits.RateLimiter(0.001, 3, max_keys=1)
    for i in range(10):
        hub.symbols.known[f"bybit:D{i}USDT"] = SymbolInfo(id=f"bybit:D{i}USDT", market="bybit", symbol=f"D{i}USDT",
                                                          name="x", type="crypto", exchange="Bybit")
    await hub._on_message(c, {"op": "set", "ch": "quotes", "ids": [f"bybit:D{i}USDT" for i in range(10)]})
    assert not c.quotes                                       # 10 changes > budget of 3: refused as a whole


async def test_guard_second_receive_reaches_the_server(monkeypatch):
    monkeypatch.setattr(limits, "profile", lambda: limits.PUBLIC)
    got = []

    async def app(scope, receive, send):
        got.append(await receive())
        got.append(await receive())   # must be the server's next message, not recursion
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    msgs = [{"type": "http.request", "body": b"{}", "more_body": False}, {"type": "http.disconnect"}]

    async def receive():
        return msgs.pop(0)

    async def send(m):
        pass
    await Guard(app)({"type": "http", "method": "PUT", "path": "/api/x", "headers": [], "client": ("127.0.0.1", 1)}, receive, send)
    assert got[1]["type"] == "http.disconnect"


# ------------------------------------------------- Astra round 3 (2026-11-03)
async def test_slow_bodies_time_out_and_non_api_writes_are_refused(monkeypatch):
    p = limits.Profile(**{**limits.PUBLIC.__dict__, "body_deadline_s": 0.2})
    monkeypatch.setattr(limits, "profile", lambda: p)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    g = Guard(app)
    sent = []

    async def send(m):
        sent.append(m)

    async def never_finishes():
        await asyncio.sleep(10)
    await g({"type": "http", "method": "PUT", "path": "/api/settings", "headers": [], "client": ("127.0.0.1", 1)}, never_finishes, send)
    assert sent[0]["status"] == 408 and g.uploads_total == 0
    sent.clear()
    await g({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}, never_finishes, send)
    assert sent[0]["status"] == 405


async def test_two_tabs_of_one_list_count_once(monkeypatch):
    p = limits.Profile(**{**limits.PUBLIC.__dict__, "ws_ip_quote_subs": 5})
    hub = _hub(monkeypatch, p)
    for i in range(5):
        hub.symbols.known[f"bybit:E{i}USDT"] = SymbolInfo(id=f"bybit:E{i}USDT", market="bybit", symbol=f"E{i}USDT",
                                                          name="x", type="crypto", exchange="Bybit")
    ids = [f"bybit:E{i}USDT" for i in range(5)]
    tab1, tab2 = _client(), _client()
    await hub._on_message(tab1, {"op": "set", "ch": "quotes", "ids": ids})
    await hub._on_message(tab2, {"op": "set", "ch": "quotes", "ids": ids})
    assert len(tab1.quotes) == 5 and len(tab2.quotes) == 5


async def test_rejected_topic_is_forgotten(monkeypatch):
    hub, c = _hub(monkeypatch), _client()
    hub.clients.add(c)
    await hub._on_message(c, {"op": "sub", "ch": "bars", "id": "bybit:BTCUSDT", "interval": "1h"})
    assert c.bars
    await hub._on_reject("bybit", "kline.60.BTCUSDT")
    assert not c.bars and ("bybit:BTCUSDT", "1h") not in hub._bar_refs
    assert json.loads(c.queue.get_nowait())["ch"] == "error"


def test_batch_update_only_touches_own_existing_drawings(tmp_path):
    db = UserDB(tmp_path / "a.sqlite")
    _put(db, 1)
    db.put_drawing_checked("other", "x", "bybit:A", "segment", '{"p":1}', max_total=10, max_per_symbol=10, quota=Q)
    n = db.update_drawings_checked("u", [("d1", "bybit:A", "segment", '{"lock":true}'), ("x", "bybit:A", "segment", '{"evil":1}'),
                                         ("nope", "bybit:A", "segment", "{}")], quota=Q)
    assert n == 1
    assert db.drawings("other", "bybit:A")[0].get("p") == 1


def test_seed_is_atomic_with_the_empty_check(tmp_path):
    db = UserDB(tmp_path / "a.sqlite")
    db.create_watchlist_checked("u", "Mine", [], max_lists=10, quota=Q)
    assert not db.seed_watchlists_checked("u", [("Crypto", ["bybit:BTCUSDT"])], max_lists=10, quota=Q)
    assert [w["name"] for w in db.watchlists("u")] == ["Mine"]


async def test_command_queue_drops_changes_cancelled_while_waiting():
    from app.realtime.bybit_ws import BybitStream

    async def noop(*a):
        return None
    st = BybitStream("linear", noop, noop, max_topics=100, cmd_rate=5, cmd_burst=1)
    st._ensure_running = lambda: None
    st.connected = True
    sent = []

    async def rec(msg):
        sent.append(msg)
    st._send = rec
    await st.subscribe("tickers.AUSDT")          # takes the only token
    await st.subscribe("tickers.BUSDT")          # waits for a token …
    await asyncio.sleep(0.05)
    await st.unsubscribe("tickers.BUSDT")        # … and is cancelled meanwhile
    await asyncio.sleep(0.5)
    args = [a for m in sent for a in m["args"]]
    assert "tickers.AUSDT" in args and "tickers.BUSDT" not in args
    st._sender.cancel()
