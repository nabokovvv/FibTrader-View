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
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 2


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

    async def prices(self):
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
    d.recorded = {"BTCUSDT"}
    rec = LiqRecorder(d)
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
