"""Alerts, Telegram linking, feedback, shared charts and derivatives data."""
import asyncio
import inspect
import json
import sqlite3
import time

import pytest

from app.alerts import AlertEngine, crossed, level_at
from app.derivs import DerivStore, Derivs, LiqRecorder
from app.feedback import issue_body, issue_title
from app.telegram import TelegramBot
from app.userdata.db import LimitError, Quota, UserDB

Q = Quota(user_bytes=10 * 1024**2, db_bytes=None)


def _db(tmp_path) -> UserDB:
    return UserDB(tmp_path / "app.sqlite")


# --------------------------------------------------------------------- schema
def test_v2_alerts_become_price_alerts(tmp_path):
    p = tmp_path / "app.sqlite"
    c = sqlite3.connect(p)
    from app.userdata.db import SCHEMA_V1
    from app.userdata.extra import SCHEMA_V2
    c.executescript(SCHEMA_V1 + SCHEMA_V2 + "PRAGMA user_version=2;")
    c.execute("INSERT INTO alerts(user_id, symbol, cond, level, created_at) VALUES('u','bybit:BTCUSDT','up',100,1)")
    c.commit()
    c.close()
    db = UserDB(p)
    assert [(a["metric"], a["level"]) for a in db.alerts("u")] == [("price", 100.0)]
    assert db.active_alerts()[0]["metric"] == "price"


def test_v1_database_gets_v2_tables(tmp_path):
    p = tmp_path / "app.sqlite"
    c = sqlite3.connect(p)
    from app.userdata.db import SCHEMA_V1
    c.executescript(SCHEMA_V1 + "PRAGMA user_version=1;")
    c.execute("INSERT INTO settings(user_id, key, value) VALUES('u','k','1')")
    c.commit()
    c.close()
    db = UserDB(p)
    assert db.settings("u") == {"k": 1}
    assert db.alerts("u") == []
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 3


# --------------------------------------------------------------------- alerts
def test_alert_limits_and_history(tmp_path):
    db = _db(tmp_path)
    for i in range(3):
        db.create_alert_checked("u", "bybit:BTCUSDT", "cross", 100.0 + i, None, None, "", max_active=3, max_total=4)
    with pytest.raises(LimitError):
        db.create_alert_checked("u", "bybit:BTCUSDT", "cross", 1.0, None, None, "", max_active=3, max_total=4)
    ids = [a["id"] for a in db.alerts("u")]
    assert db.fire_alerts([(ids[0], 0, 101.0), (ids[1], 0, 102.0)]) == {ids[0], ids[1]}
    assert db.fire_alerts([(ids[0], 0, 101.0)]) == set()  # never twice
    db.update_alert_checked("u", ids[2], active=None, level=150.0, cond=None, note=None, max_active=3)
    assert db.fire_alerts([(ids[2], 0, 101.0)]) == set()  # the target changed after the engine read it
    # 1 armed + 2 fired; total cap 4 → creating two more forgets the oldest fired one
    db.create_alert_checked("u", "bybit:ETHUSDT", "up", 5.0, None, None, "", max_active=3, max_total=4)
    db.create_alert_checked("u", "bybit:ETHUSDT", "up", 6.0, None, None, "", max_active=3, max_total=4)
    rows = db.alerts("u")
    assert len(rows) == 4 and sum(a["active"] for a in rows) == 3
    # re-arming respects the active cap
    fired = next(a for a in rows if not a["active"])
    with pytest.raises(LimitError):
        db.update_alert_checked("u", fired["id"], active=True, level=None, cond=None, note=None, max_active=3)
    assert db.alerts("other") == []


def test_paused_alerts_are_never_dropped_to_make_room(tmp_path):
    db = _db(tmp_path)
    a = [db.create_alert_checked("u", "bybit:BTCUSDT", "cross", 100.0 + i, None, None, "", max_active=5, max_total=3)
         for i in range(3)]
    db.update_alert_checked("u", a[0]["id"], active=False, level=None, cond=None, note=None, max_active=5)  # paused
    db.fire_alerts([(a[1]["id"], 0, 101.0)])                                                               # fired
    db.create_alert_checked("u", "bybit:ETHUSDT", "up", 5.0, None, None, "", max_active=5, max_total=3)
    ids = {x["id"] for x in db.alerts("u")}
    assert a[0]["id"] in ids and a[1]["id"] not in ids        # the fired one made room, the paused one stayed
    db.update_alert_checked("u", a[2]["id"], active=False, level=None, cond=None, note=None, max_active=5)
    with pytest.raises(LimitError) as e:                       # only paused + armed left: no silent deletion
        db.create_alert_checked("u", "bybit:ETHUSDT", "up", 6.0, None, None, "", max_active=5, max_total=3)
    assert "paused" in str(e.value.detail if hasattr(e.value, "detail") else e.value)


def test_line_alert_follows_its_drawing(tmp_path):
    db = _db(tmp_path)
    pts = [{"timestamp": 1000, "value": 10}, {"timestamp": 2000, "value": 20}]
    db.put_drawing_checked("u", "d1", "bybit:BTCUSDT", "segment", json.dumps({"points": pts}),
                           max_total=10, max_per_symbol=10, quota=Q)
    db.create_alert_checked("u", "bybit:BTCUSDT", "cross", None, [1000, 10, 2000, 20], "d1", "", max_active=5, max_total=10)
    moved = [{"timestamp": 1000, "value": 30}, {"timestamp": 3000, "value": 50}]
    db.put_drawing_checked("u", "d1", "bybit:BTCUSDT", "segment", json.dumps({"points": moved}),
                           max_total=10, max_per_symbol=10, quota=Q)
    assert db.alerts("u")[0]["line"] == [1000, 30, 3000, 50]
    db.delete_drawing("u", "d1")
    assert db.alerts("u") == []


def test_level_and_crossing():
    a = {"line": [0, 100.0, 1000, 200.0]}
    assert level_at(a, 2000) == 300.0  # extended to the right
    assert level_at({"level": 5.0, "line": None}, 0) == 5.0
    c = lambda cond, prev, cur, lvl: crossed(cond, prev - lvl, cur - lvl)  # noqa: E731
    assert c("cross", 99, 101, 100) and c("cross", 101, 99, 100)
    assert c("up", 99, 100, 100) and not c("up", 101, 99, 100)
    assert c("down", 101, 99, 100) and not c("down", 99, 101, 100)
    assert not c("cross", 101, 102, 100)
    # price stays at 100 while a sloped line passes 101 → 99: that is a crossing
    assert crossed("cross", 100 - 101, 100 - 99)


class FakeFeed:
    def __init__(self, seq):
        self.seq = list(seq)

    async def prices(self, wanted=None):
        return self.seq.pop(0)


class FakeSymbols:
    async def get(self, sid):
        return None


class FakeBot:
    available = True

    def __init__(self):
        self.sent = []

    def send_alert(self, chat_id, text):
        self.sent.append((chat_id, text))
        return True


async def test_engine_fires_once_and_notifies(tmp_path):
    db = _db(tmp_path)
    db.create_alert_checked("u", "bybit:BTCUSDT", "cross", 100.0, None, None, "breakout", max_active=5, max_total=10)
    db.create_alert_checked("v", "bybit:BTCUSDT", "down", 100.0, None, None, "", max_active=5, max_total=10)
    db.tg_new_code("u", "code12345", "ru", 60_000)
    assert db.tg_claim("code12345", 777) == ("u", "ru")
    bot = FakeBot()
    eng = AlertEngine(db, {"bybit": FakeFeed([{"BTCUSDT": 99}, {"BTCUSDT": 101}, {"BTCUSDT": 99}, {"BTCUSDT": 101}])}, FakeSymbols(), bot)
    assert await eng.tick() == []                       # first price: baseline only
    hits = await eng.tick()                             # 99 → 101: u's "cross" fires, v's "down" does not
    assert [a["user_id"] for a, _ in hits] == ["u"]
    assert bot.sent and bot.sent[0][0] == 777 and "breakout" in bot.sent[0][1] and "пересекла" in bot.sent[0][1]
    hits = await eng.tick()                             # 101 → 99: only v's alert is still armed
    assert [a["user_id"] for a, _ in hits] == ["v"]
    assert len(bot.sent) == 1                           # v has no Telegram
    # re-armed: the first tick after the change is a fresh baseline, not a stale crossing
    a = db.alerts("u")[0]
    db.update_alert_checked("u", a["id"], active=True, level=None, cond=None, note=None, max_active=5)
    assert await eng.tick() == []


async def test_many_alerts_are_split_not_truncated(tmp_path):
    db = _db(tmp_path)
    for i in range(20):
        db.create_alert_checked("u", "bybit:BTCUSDT", "cross", 100.0 + i / 100, None, None, "n" * 140, max_active=20, max_total=60)
    db.tg_new_code("u", "code12345", "en", 60_000)
    db.tg_claim("code12345", 1)
    bot = FakeBot()
    eng = AlertEngine(db, {"bybit": FakeFeed([{"BTCUSDT": 99}, {"BTCUSDT": 99.5}, {"BTCUSDT": 101}])}, FakeSymbols(), bot)
    await eng.tick(); await eng.tick()
    assert len(await eng.tick()) == 20
    assert len(bot.sent) >= 2 and all(len(t) <= 3600 for _, t in bot.sent)
    assert sum(t.count("🔔") for _, t in bot.sent) == 20


# ------------------------------------------------------------------- telegram
async def test_telegram_linking(tmp_path, monkeypatch):
    import app.telegram as tg
    monkeypatch.setattr(tg, "REPLY_EVERY_S", 0.0)
    db = _db(tmp_path)

    async def call(fn, *a):
        return getattr(db, fn)(*a)

    bot = TelegramBot("t", call)
    db.tg_new_code("u", "abcdefgh123", "en", 60_000)
    await bot.on_message({"chat": {"id": 5, "type": "group"}, "text": "/start abcdefgh123"})
    assert db.tg_link("u") is None                      # groups are ignored
    await bot.on_message({"chat": {"id": 5, "type": "private"}, "text": "/start abcdefgh123"})
    assert db.tg_link("u")["chat_id"] == 5
    await bot.on_message({"chat": {"id": 6, "type": "private"}, "text": "/start abcdefgh123"})
    assert db.tg_link("u")["chat_id"] == 5              # one use only
    db.tg_new_code("w", "expired0001", "en", -1)
    await bot.on_message({"chat": {"id": 7, "type": "private"}, "text": "/start expired0001"})
    assert db.tg_link("w") is None
    await bot.on_message({"chat": {"id": 5, "type": "private"}, "text": "/stop"})
    assert db.tg_link("u") is None
    assert len(bot._replies) == 4
    monkeypatch.setattr(tg, "REPLY_EVERY_S", 3.0)
    for _ in range(50):  # a flood gets one answer, not fifty
        await bot.on_message({"chat": {"id": 9, "type": "private"}, "text": "spam"})
    assert len(bot._replies) == 5
    await bot._client.aclose()


# ------------------------------------------------------------------- feedback
def test_feedback_issue_is_safe_and_anonymous(tmp_path):
    body = issue_body("idea", "hi @octocat ```\n![x](http://e/x.png)",
                      {"symbol": "bybit:BTC\n\n![img](http://e/y.png) @octocat", "evil": "x"}, "user-uuid")
    assert "user-uuid" not in body and "```text" in body and body.count("```") == 2
    inside = body.split("```text", 1)[1].split("```", 1)[0]
    assert "bybit:BTC" in inside and "y.png" in inside and "evil" not in body  # user context only inside the fence
    assert issue_title("bug", "  line one\nline two ") == "[Bug] line one line two"
    db = _db(tmp_path)
    for _ in range(2):
        db.add_feedback_checked("u", "idea", "text", "{}", per_day=2)
    with pytest.raises(LimitError):
        db.add_feedback_checked("u", "idea", "text", "{}", per_day=2)
    assert len(db.feedback_pending()) == 2


# --------------------------------------------------------------------- shares
def test_shares(tmp_path):
    db = _db(tmp_path)
    for i in range(3):
        db.create_share_checked("u", f"share000{i}", json.dumps({"symbol": "bybit:BTCUSDT", "i": i}), 60_000,
                                max_shares=2, quota=Q)
    assert db.share("share0000") is None                # the oldest made room
    assert db.share("share0002")["state"]["i"] == 2
    db.create_share_checked("u", "oldshare1", "{}", -1, max_shares=5, quota=Q)
    assert db.share("oldshare1") is None                # expired
    assert db.delete_share("other", "share0002") is False and db.delete_share("u", "share0002") is True


# --------------------------------------------------------------------- derivs
class FakeBybit:
    def __init__(self):
        self.calls = 0

    async def _get(self, path, params):
        self.calls += 1
        start, end = params["startTime"], params["endTime"]
        step = 3_600_000
        ts = list(range(start - start % step + step if start % step else start, end + 1, step))[-200:]
        return {"list": [{"timestamp": str(t), "openInterest": "1.5"} for t in reversed(ts)]}


async def test_oi_pages_are_cached_and_budgeted(tmp_path):
    prov = FakeBybit()
    d = Derivs(DerivStore(tmp_path / "derivs.sqlite"), prov)
    now = int(time.time() * 1000)
    span = 200 * 3_600_000
    start = now - 5 * span
    res = await d.series("oi", "BTCUSDT", "1h", start, now, max_pages=2)
    assert prov.calls == 2 and res["covered_from"] > start and not res["exhausted"]
    calls = prov.calls
    res2 = await d.series("oi", "BTCUSDT", "1h", start, now, max_pages=2)
    assert prov.calls == calls + 2                      # cached pages are free, the budget goes to older ones…
    assert res2["covered_from"] < res["covered_from"]   # …so the next request reaches further back
    calls = prov.calls
    await d.series("oi", "BTCUSDT", "1h", res2["covered_from"], now, max_pages=2)
    assert prov.calls == calls                          # everything in that range is cached now
    assert all(start <= p[0] < now for p in res2["points"])


async def test_liquidations_are_summed_per_minute(tmp_path):
    d = Derivs(DerivStore(tmp_path / "derivs.sqlite"), None)
    rec = LiqRecorder(d, ["BTCUSDT"])
    assert d.recorded == {"BTCUSDT"}
    t0 = 1_700_000_040_000
    msg = lambda side, v: json.dumps({"topic": "allLiquidation.BTCUSDT", "data": [{"T": t0 + 1000, "s": "BTCUSDT", "S": side, "v": v, "p": "100"}]})  # noqa: E731
    rec.on_message(msg("Buy", "2"))
    rec.on_message(msg("Sell", "1"))
    rec.on_message(json.dumps({"topic": "allLiquidation.XYZUSDT", "data": [{"T": t0, "s": "XYZUSDT", "S": "Buy", "v": "1", "p": "1"}]}))
    await rec.flush()
    rec.on_message(msg("Buy", "1"))
    await rec.flush()
    rows = d.store.liq("BTCUSDT", t0 - 3_600_000, t0 + 3_600_000, 3_600_000)
    assert rows == [[t0 // 3_600_000 * 3_600_000, 300.0, 100.0]]
    assert d.store.liq("XYZUSDT", 0, t0 * 2, 60_000) == []
    assert inspect.iscoroutinefunction(rec.flush)
    # liquidations are kept for good: maintenance never deletes them
    d.store.add_liq([("BTCUSDT", 1_000_000_000_000, 5.0, 0.0)])
    d.store.prune(now=t0 / 1000 + 400 * 86400)
    assert d.store.liq_first("BTCUSDT") == 1_000_000_000_000


def test_fixed_liquidation_list(tmp_path):
    from app.derivs import LIQ_SYMBOLS_FILE, load_liq_symbols
    syms = load_liq_symbols()
    assert syms and len(syms) == len(set(syms)) and {"BTCUSDT", "NVDAUSDT", "XAUUSDT"} <= set(syms)
    assert not {"USDCUSDT", "PAXGUSDT", "XAUTUSDT", "USDEUSDT"} & set(syms)   # no stablecoins / gold
    assert LIQ_SYMBOLS_FILE.exists()
    bad = tmp_path / "bad.json"
    bad.write_text('{"symbols": ["BTC/USDT"]}')
    assert load_liq_symbols(bad) == []          # a broken list records nothing rather than something else
    assert load_liq_symbols(tmp_path / "missing.json") == []


def test_snapshot_is_cleaned():
    from fastapi import HTTPException
    from app.shares import clean_snapshot
    ok = clean_snapshot({"symbol": "bybit:BTCUSDT", "interval": "1h", "theme": "x", "junk": 1,
                         "indicators": [{"name": "HMA", "calcParams": [9]}],
                         "drawings": [{"name": "segment", "points": [{"timestamp": 1, "value": 2}], "extendData": {"a": 1}}]})
    assert "junk" not in ok and "theme" not in ok and ok["indicators"][0]["calcParams"] == [9]
    for bad in ({"symbol": "x", "interval": "7h"}, {"symbol": "x", "interval": "1h", "indicators": {"a": 1}},
                {"symbol": "x", "interval": "1h", "indicators": [{"name": "HMA", "calcParams": [-1e20]}]},
                {"symbol": "x", "interval": "1h", "drawings": [{"name": "segment", "points": [{"timestamp": "a", "value": 1}]}]}):
        with pytest.raises(HTTPException):
            clean_snapshot(bad)


async def test_cached_pages_are_bounded_too(tmp_path):
    prov = FakeBybit()
    d = Derivs(DerivStore(tmp_path / "derivs.sqlite"), prov)
    now = int(time.time() * 1000)
    span = 200 * 3_600_000
    start = now - 40 * span
    for _ in range(20):
        await d.series("oi", "BTCUSDT", "1h", start, now, max_pages=10)
    res = await d.series("oi", "BTCUSDT", "1h", start, now, max_pages=10, max_read=12)
    assert len(res["points"]) <= 12 * 200 and res["covered_from"] > start


def test_liquidation_history_never_double_counts(tmp_path):
    from app.derivs import DerivStore
    M, H, D = 60_000, 3_600_000, 86_400_000
    st = DerivStore(tmp_path / "d.sqlite")
    T = 100 * D                                              # cutover: history before, our recorder after
    st.add_liq([("BTCUSDT", T - 5 * M, 999.0, 999.0), ("BTCUSDT", T + M, 7.0, 1.0)])   # pre-cutover live row is ignored
    # minutes for the last 2 hours, hours for the last 3 days, days for 20 days; all values 1/2
    st.import_liq_history("BTCUSDT", M, [(T - 2 * H + k * M, 1.0, 2.0) for k in range(120)], T - 2 * H, T)
    st.import_liq_history("BTCUSDT", H, [(T - 72 * H + k * H, 10.0, 20.0) for k in range(72)], T - 72 * H, T)
    st.import_liq_history("BTCUSDT", D, [(T - 20 * D + k * D, 100.0, 200.0) for k in range(20)] + [(T, 5.0, 5.0)], T - 20 * D, T)
    # 1m chart: minute history up to the cutover, then live; nothing older than the minute data
    m = st.liq("BTCUSDT", T - 3 * H, T + H, M)
    assert m[0] == [T - 2 * H, 1.0, 2.0] and m[-1] == [T + M, 7.0, 1.0] and len(m) == 121
    # 1h chart: the last 2 hours come from minutes (120 x 1 = 60 per hour), older ones from hourly rows
    h = dict((r[0], r[1]) for r in st.liq("BTCUSDT", T - 4 * H, T, H))
    assert h == {T - 4 * H: 10.0, T - 3 * H: 10.0, T - 2 * H: 60.0, T - H: 60.0}
    # 1D chart: minutes do not cover a whole day, so the day before the cutover comes from hours alone
    d = dict((r[0], r[1]) for r in st.liq("BTCUSDT", T - 5 * D, T + D, D))
    assert d[T - D] == 24 * 10.0 and d[T - 4 * D] == 100.0 and d[T] == 7.0
    # a cutover inside a bucket: its history part and its recorded part make ONE bar
    T2 = 200 * D + 30 * M
    st.add_liq([("ETHUSDT", T2 + M, 3.0, 0.0)])
    st.import_liq_history("ETHUSDT", M, [(T2 - 10 * M, 2.0, 1.0)], 200 * D, T2)
    assert st.liq("ETHUSDT", 200 * D, 201 * D, H) == [[200 * D, 5.0, 1.0]]
    assert d[T - 3 * D] == 10.0 * 24 and d[T - 4 * D] == 100.0          # hours reach 3 days back, days before that
    # the oldest bucket per chart size, and re-importing replaces instead of adding up
    assert st.liq_first("BTCUSDT", M) == T - 2 * H and st.liq_first("BTCUSDT", D) == T - 20 * D
    st.import_liq_history("BTCUSDT", D, [(T - 20 * D + k * D, 100.0, 200.0) for k in range(20)], T - 20 * D, T)
    assert dict((r[0], r[1]) for r in st.liq("BTCUSDT", T - 5 * D, T, D))[T - 4 * D] == 100.0
    assert st.drop_liq_before(T) == 1


# ------------------------------------------------------------ alerts on metrics
class FakeValuesFeed:
    def __init__(self, seq):
        self.seq = list(seq)
        self.asked: list[set] = []

    async def prices(self, wanted=None):
        raise AssertionError("values() should be used")

    async def values(self, wanted, metrics):
        self.asked.append(set(metrics))
        return self.seq.pop(0)


class FakeSentiment:
    def __init__(self):
        self.fng = [[0, 30.0]]
        self.yt = [[0, -1.0]]

    async def yt_points(self):
        return self.yt


async def test_funding_oi_and_market_wide_alerts(tmp_path):
    db = _db(tmp_path)
    kw = dict(max_active=10, max_total=20)
    db.create_alert_checked("u", "bybit:BTCUSDT", "up", 0.01, None, None, "", metric="funding", **kw)
    db.create_alert_checked("u", "bybit:BTCUSDT", "down", 1000.0, None, None, "", metric="oi", **kw)
    db.create_alert_checked("u", "bybit:ETHUSDT", "down", 25.0, None, None, "", metric="fng", **kw)
    db.create_alert_checked("u", "bybit:ETHUSDT", "cross", 0.0, None, None, "", metric="yt", **kw)
    db.tg_new_code("u", "code12345", "ru", 60_000)
    db.tg_claim("code12345", 9)
    sent = FakeSentiment()
    feed = FakeValuesFeed([{"BTCUSDT": {"price": 1, "funding": 0.005, "oi": 1200}},
                           {"BTCUSDT": {"price": 1, "funding": 0.02, "oi": 1100}},
                           {"BTCUSDT": {"price": 1, "funding": 0.02, "oi": 900}}])
    bot = FakeBot()
    eng = AlertEngine(db, {"bybit": feed}, FakeSymbols(), bot, sent)
    assert await eng.tick() == []                                   # baselines
    assert feed.asked[0] == {"funding", "oi"}                       # no price asked when no price alert
    hits = await eng.tick()
    assert [a["metric"] for a, _ in hits] == ["funding"]
    assert "Фандинг пересёк вверх 0.0100%" in bot.sent[-1][1]
    sent.fng = [[0, 30.0], [1, 20.0]]                               # a new day: F&G 30 → 20 crosses 25 down
    sent.yt = [[0, -1.0], [1, 0.5]]                                 # pressure −1 → 0.5 crosses zero
    hits = await eng.tick()
    assert sorted(a["metric"] for a, _ in hits) == ["fng", "oi", "yt"]
    texts = "\n".join(t for _, t in bot.sent)
    assert "Индекс страха и жадности" in texts and "Давление инфлюенсеров пересекло" in texts


def test_alert_metric_levels_are_checked():
    import pytest
    from fastapi import HTTPException

    from app.alerts import check_level
    check_level("funding", -0.05)
    check_level("yt", -20.0)
    check_level("fng", 0)
    for metric, lvl in (("price", 0), ("oi", -1), ("fng", 101), ("funding", 150), ("nope", 1)):
        with pytest.raises(HTTPException):
            check_level(metric, lvl)


async def test_bybit_feed_values_from_one_tickers_call():
    from app.alerts import BybitFeed

    class P:
        calls = 0

        async def tickers(self):
            P.calls += 1
            return [{"symbol": "BTCUSDT", "lastPrice": "100", "fundingRate": "0.0001", "openInterest": "5000"},
                    {"symbol": "ETHUSDT", "lastPrice": "10", "fundingRate": "", "openInterest": "x"},
                    {"lastPrice": "1"}]

    v = await BybitFeed(P()).values({"BTCUSDT"}, {"price", "funding", "oi"})
    assert v["BTCUSDT"] == {"price": 100.0, "funding": 0.01, "oi": 5000.0} and v["ETHUSDT"] == {"price": 10.0}
    assert await BybitFeed(P()).prices({"BTCUSDT"}) == {"BTCUSDT": 100.0, "ETHUSDT": 10.0}
    assert P.calls == 2


async def test_hyperliquid_context_refresh_never_blocks_the_tick():
    import asyncio as aio
    import time as tm

    from app.alerts import HyperliquidFeed

    class P:
        def __init__(self):
            self.snap = None
            self.gate = aio.Event()
            self.loads = 0

        async def mids(self, wanted):
            return {"BTC": 100.0}

        def cached_contexts(self, dex):
            return self.snap

        async def contexts(self, dex, max_age, background=False):
            self.loads += 1
            await self.gate.wait()                     # a slow / waiting refresh
            self.snap = (tm.time(), {"BTC": {"funding": "0.0000125", "openInterest": "10"}})

    p = P()
    feed = HyperliquidFeed(p)
    v = await aio.wait_for(feed.values({"BTC"}, {"price", "funding", "oi"}), 1)
    assert v == {"BTC": {"price": 100.0}}              # no snapshot yet: price only, no waiting
    await aio.sleep(0)
    await feed.values({"BTC"}, {"funding"})
    await aio.sleep(0)
    assert p.loads == 1                                # one refresh in flight, not one per tick
    p.gate.set()
    await aio.sleep(0)
    await aio.sleep(0)
    v = await feed.values({"BTC"}, {"funding", "oi"})
    assert v["BTC"]["funding"] == pytest.approx(0.00125) and v["BTC"]["oi"] == 10.0
