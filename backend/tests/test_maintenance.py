"""Candle-cache upkeep: retention, emergency cleanup, idle prewarm."""
import time

from app import maintenance
from app.candles.store import StoreRegistry

DAY_MS = 86_400_000


def _fill(st, symbol, interval, days, step_ms):
    now = int(time.time() * 1000)
    bars = [[t, 1, 1, 1, 1, 1] for t in range(now - days * DAY_MS, now, step_ms)]
    st.upsert(symbol, interval, bars)
    st.set_coverage(symbol, interval, bars[0][0], bars[-1][0], False)
    return bars


def test_retention_prunes_old_minutes_and_drops_unused(tmp_path):
    reg = StoreRegistry(tmp_path)
    st = reg.get("bybit")
    _fill(st, "BTCUSDT", "1m", 40, 60_000 * 30)        # 40 days of (sparse) 1m
    _fill(st, "BTCUSDT", "1h", 400, 3_600_000)         # 1h is kept
    _fill(st, "OLDUSDT", "1h", 10, 3_600_000)
    st.touch("BTCUSDT", "1m"); st.touch("BTCUSDT", "1h")
    st._conn.execute("UPDATE coverage SET last_access=? WHERE symbol='OLDUSDT'", (int(time.time()) - 90 * 86400,))
    res = maintenance.retention(reg, ["bybit"])
    left_1m = st.read("BTCUSDT", "1m", None, None)
    assert left_1m and left_1m[0][0] >= int(time.time() * 1000) - 30 * DAY_MS - 60_000
    assert st.coverage("BTCUSDT", "1m").first_ts >= left_1m[0][0] - 60_000 * 30
    assert st.count("BTCUSDT", "1h") > 9000
    assert st.coverage("OLDUSDT", "1h") is None and res["series_dropped"] == 1


def test_emergency_stops_once_enough_is_free(tmp_path, monkeypatch):
    reg = StoreRegistry(tmp_path)
    st = reg.get("bybit")
    _fill(st, "BTCUSDT", "1m", 20, 60_000 * 10)
    _fill(st, "BTCUSDT", "5m", 60, 300_000 * 10)
    calls = {"n": 0}

    def fake_free():
        calls["n"] += 1
        return 1 if calls["n"] <= 1 else 10 * 1024**3        # low only before the first stage
    monkeypatch.setattr(maintenance, "free_bytes", fake_free)
    res = maintenance.emergency(reg, ["bybit"])
    assert res["stages"] == ["1m older than 7 days"]
    assert st.count("BTCUSDT", "5m") > 0                       # later stages not needed


def test_emergency_does_nothing_with_free_space(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "free_bytes", lambda: 50 * 1024**3)
    assert maintenance.emergency(StoreRegistry(tmp_path), ["bybit"]) is None


def test_prewarm_waits_while_people_use_the_site():
    class Hub:
        clients = set()

    class State:
        hub = Hub()
        last_user_bars = time.monotonic()
    p = maintenance.Prewarm(State())
    assert not p.idle()
    State.last_user_bars = time.monotonic() - 600
    assert p.idle()
