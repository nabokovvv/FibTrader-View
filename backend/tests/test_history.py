"""Older-history guard (history.py): wallets, pace, daily stop, networks, persistence, and the two endpoints."""
import time
from types import SimpleNamespace

import httpx
import pytest

from app import auth, history, limits, main
from app.derivs import DerivStore, Derivs
from app.history import (FRESH_BARS, NET_FACTOR, REFILL, SLOW_S, STOP, STOP_MS, WALLET, HistoryQuota, Refused,
                         fresh_cutoff, network_key, share_window)
from app.intervals import DAY, HOUR, MINUTE
from app.markets.base import SymbolInfo

T0 = 1_800_000_000_000


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def quota(clock=None, path=None, **kw):
    return HistoryQuota(path, clock=clock or Clock(), **kw)


# ----------------------------------------------------------------------------------------------- wallets
async def test_only_the_bars_sent_are_charged_and_failures_are_free():
    q = quota()
    g = await q.admit("alice", "203.0.113.5", 1000)
    assert g.lock is None and g.ticket is not None
    q.settle(g.ticket, 300)                       # a page of 1000 reserved, 300 older bars sent
    w = q._mem["u:alice"]
    assert w.tokens == WALLET - 300 and sum(w.hours.values()) == 300
    g = await q.admit("alice", "203.0.113.5", 1000)
    q.refund(g.ticket)                            # the request failed: nothing charged
    assert q._mem["u:alice"].tokens == WALLET - 300
    q.settle(g.ticket, 999)                       # a ticket is settled once
    assert q._mem["u:alice"].tokens == WALLET - 300


async def test_two_parallel_requests_cannot_spend_the_same_tokens():
    clock = Clock()
    q = quota(clock)
    first = await q.admit("bob", "203.0.113.6", 1000)
    q._mem["u:bob"].tokens = 1500                  # 1000 still reserved by the first request, 1500 left
    second = await q.admit("bob", "203.0.113.6", 1000)
    assert second.ticket.items[0][2] == 1000       # the second one reserved its own 1000
    third = await q.admit("bob", "203.0.113.6", 1000)
    assert third.ticket.items[0][2] == 0           # only 500 left: this one is paced, not paid from the same tokens
    with pytest.raises(Refused) as e:
        await q.admit("bob", "203.0.113.6", 1000)
    assert e.value.code == "slow" and e.value.status == 429 and e.value.retry_after >= 1
    q.settle(first.ticket, 1000)


async def test_an_empty_wallet_paces_and_refills():
    clock = Clock()
    q = quota(clock)
    g = await q.admit("carol", "203.0.113.7", 500)
    q._mem["u:carol"].tokens = 0
    q.settle(g.ticket, 0)
    q._mem["u:carol"].tokens = -500                # nothing left at all
    assert (await q.admit("carol", "203.0.113.7", 500)).ticket is not None   # the first page goes through …
    with pytest.raises(Refused) as e:              # … the next one waits SLOW_S
        await q.admit("carol", "203.0.113.7", 500)
    assert e.value.retry_after == SLOW_S
    clock.t += int(SLOW_S * 1000)
    assert (await q.admit("carol", "203.0.113.7", 500)).ticket is not None
    clock.t += int((1500 / REFILL) * 1000)         # 1500 bars of refill: full speed again for a page of 500
    g = await q.admit("carol", "203.0.113.7", 500)
    assert g.ticket.items[0][2] == 500


async def test_big_pages_wait_longer_but_not_without_bound():
    clock = Clock()
    q = quota(clock)
    await q.admit("dan", "203.0.113.8", 1)
    q._mem["u:dan"].tokens = -1e9
    await q.admit("dan", "203.0.113.8", 5000)
    assert q._mem["u:dan"].slow_next - clock.t == 2 * SLOW_S * 1000


async def test_a_day_of_bulk_downloading_stops_older_history_for_a_day():
    clock = Clock()
    q = quota(clock)
    g = await q.admit("eve", "198.51.100.9", 1000)
    q.settle(g.ticket, STOP + 1)
    g = await q.admit("eve", "198.51.100.9", 1000)
    assert g.lock is not None and g.lock.kind == "stopped" and g.lock.until == clock.t + STOP_MS
    r = g.lock.refuse()
    assert r.status == 429 and r.code == "stopped" and r.until == g.lock.until
    clock.t += STOP_MS + 1
    g = await q.admit("eve", "198.51.100.9", 1000)
    assert g.lock is None and g.ticket is not None


async def test_the_daily_amount_is_a_rolling_24_hours():
    clock = Clock()
    q = quota(clock)
    for _ in range(3):                              # 3 × 0.4 of the stop, but 25 h apart: never stopped
        g = await q.admit("fay", "198.51.100.10", 10)
        q.settle(g.ticket, int(STOP * 0.4))
        clock.t += 25 * HOUR
        q._mem["u:fay"].tokens = WALLET
    assert (await q.admit("fay", "198.51.100.10", 10)).lock is None
    g = await q.admit("fay", "198.51.100.10", 10)
    q.settle(g.ticket, int(STOP * 0.6))
    clock.t += HOUR
    g = await q.admit("fay", "198.51.100.10", 10)
    q.settle(g.ticket, int(STOP * 0.6))             # 1.2 × the stop within two hours
    assert (await q.admit("fay", "198.51.100.10", 10)).lock is not None


async def test_accounts_behind_one_network_share_a_larger_wallet():
    q = quota()
    g = await q.admit("u1", "2001:db8:1:2::10", 10)
    q.settle(g.ticket, STOP)                        # one account at its stop (not over it)
    g = await q.admit("u2", "2001:db8:1:2:ffff::1", 10)   # another account, same /64
    assert g.lock is None
    net = q._mem["n:2001:db8:1:2::/64"]
    assert net.tokens == WALLET * NET_FACTOR - STOP - 10
    q.settle(g.ticket, STOP * (NET_FACTOR - 1) + 1)  # the network as a whole goes over its stop
    g = await q.admit("u3", "2001:db8:1:2::99", 10)
    assert g.lock is not None and g.lock.kind == "stopped"   # a fresh account from there is stopped too
    assert (await q.admit("u3", "2001:db8:9:9::1", 10)).lock is None   # from elsewhere it is not


def test_networks_are_addresses_and_ipv6_64s():
    assert network_key("203.0.113.5") == "203.0.113.5"
    assert network_key("::ffff:203.0.113.5") == "203.0.113.5"
    assert network_key("2001:db8:1:2:3:4:5:6") == "2001:db8:1:2::/64"
    assert network_key("2001:DB8:1:2::1") == network_key("2001:db8:1:2:aaaa::1")
    assert network_key("unknown").startswith("?")


async def test_emergency_exception_has_no_wallet():
    q = quota(unlimited=frozenset({"owner"}))
    g = await q.admit("owner", "203.0.113.5", 1000)
    assert g.lock is None and g.ticket is None and "u:owner" not in q._mem


async def test_a_stop_survives_a_restart(tmp_path):
    clock = Clock()
    q = quota(clock, tmp_path / "history.sqlite")
    g = await q.admit("gus", "198.51.100.11", 1000)
    q.settle(g.ticket, STOP + 1)
    await q.stop()
    q2 = quota(clock, tmp_path / "history.sqlite")
    clock.t += HOUR
    g = await q2.admit("gus", "198.51.100.11", 1000)
    assert g.lock is not None and g.lock.until == T0 + STOP_MS
    await q2.stop()


async def test_memory_is_bounded_and_pushed_out_wallets_come_back(tmp_path):
    clock = Clock()
    q = quota(clock, tmp_path / "history.sqlite", max_keys=4)
    g = await q.admit("hal", "198.51.100.12", 10)
    q.settle(g.ticket, 777)
    for i in range(6):                              # other accounts push hal out of memory
        q.refund((await q.admit(f"x{i}", f"198.51.100.{20 + i}", 10)).ticket)
    assert "u:hal" not in q._mem and len(q._mem) <= 4
    await q.flush()
    assert not q._evicted
    g = await q.admit("hal", "198.51.100.12", 10)   # read back from the file
    assert sum(q._mem["u:hal"].hours.values()) == 777
    await q.stop()


def test_fresh_window_and_shared_moments():
    now = T0
    assert fresh_cutoff("1h", now) == (now - FRESH_BARS * HOUR) // HOUR * HOUR
    # finer points than the chart's bars: the window counts the points (a monthly chart is not 100 years)
    assert fresh_cutoff("1M", now, point_ms=DAY) == (now - FRESH_BARS * DAY) // DAY * DAY
    assert fresh_cutoff("1m", now, point_ms=HOUR) == fresh_cutoff("1m", now)   # coarser points: the chart's window
    made = now - 100 * DAY
    assert share_window("1h", made - 5 * HOUR, made) == ((made - 5 * HOUR - FRESH_BARS * HOUR) // HOUR * HOUR,
                                                         made - 5 * HOUR + FRESH_BARS * HOUR)
    # the right edge in the future counts as the moment the link was made
    assert share_window("1h", made + DAY, made)[1] == made + FRESH_BARS * HOUR
    # a moment that was already old when the link was made opens nothing
    assert share_window("1h", made - FRESH_BARS * HOUR - HOUR, made) is None


async def test_paced_pages_are_charged_too():
    clock = Clock()
    q = quota(clock)
    g = await q.admit("ivy", "198.51.100.30", 500)
    q.settle(g.ticket, 0)
    q._mem["u:ivy"].tokens = 0
    g = await q.admit("ivy", "198.51.100.30", 500)          # paced: reserves nothing …
    assert g.ticket.items[0][2] == 0
    q.settle(g.ticket, 500)                                  # … but what it sent is charged
    assert q._mem["u:ivy"].tokens == -500
    clock.t += int(SLOW_S * 1000)                            # the pace and the refill no longer add up:
    g = await q.admit("ivy", "198.51.100.30", 500)          # still paced after one pause
    assert g.ticket.items[0][2] == 0


async def test_a_wallet_in_use_is_never_swapped_for_a_stored_copy(tmp_path):
    clock = Clock()
    q = quota(clock, tmp_path / "history.sqlite", max_keys=4)
    g = await q.admit("jay", "198.51.100.40", 1000)          # a request in flight …
    for i in range(6):                                       # … while other accounts crowd memory
        q.refund((await q.admit(f"y{i}", f"198.51.100.{50 + i}", 10)).ticket)
        await q.flush()
    assert len(q._mem) <= 4 and "u:jay" in q._mem             # the others were pushed out, not jay
    other = await q.admit("jay", "198.51.100.40", 10)        # a second request of the same account
    assert other.ticket.items[0][1] is g.ticket.items[0][1]  # the very same wallet object
    q.settle(g.ticket, STOP + 1)                             # the first one ends with a stop: nothing lost
    q.settle(other.ticket, 0)
    await q.flush()
    q2 = quota(clock, tmp_path / "history.sqlite")
    assert (await q2.admit("jay", "198.51.100.40", 10)).lock is not None
    await q.stop()
    await q2.stop()


async def test_the_newest_page_is_charged_when_it_reaches_back():
    q = quota()
    assert await q.charge("kim", "198.51.100.70", 700) is None
    assert q._mem["u:kim"].tokens == WALLET - 700 and not q._active
    g = await q.admit("kim", "198.51.100.70", 10)
    q.settle(g.ticket, STOP)
    lock = await q.charge("kim", "198.51.100.70", 10)
    assert lock is not None and lock.kind == "stopped"


# ----------------------------------------------------------------------------------------------- endpoints
SYM = SymbolInfo(id="fake:X", market="fake", symbol="X", name="X", type="crypto", exchange="F")


class Symbols:
    async def get(self, id):
        return SYM if id in ("fake:X", "X") else None


class Candles:
    """Bars `step` apart back to 2000 (hourly; daily: a sparse market): `limit` bars before `to` (the newest
    without it)."""

    def __init__(self, step=HOUR):
        self.step = step

    async def get_bars(self, info, interval, to, limit):
        now = int(time.time() * 1000)
        end = (min(to - 1, now) if to else now) // HOUR * HOUR
        bars = [[end - k * self.step, 1.0, 2.0, 0.5, 1.5, 1.0] for k in range(limit)][::-1]
        return SimpleNamespace(native="1h", more=True, bars=bars)


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(limits, "profile", lambda: limits.PUBLIC)
    st = main.app.state
    q = HistoryQuota(None, clock=Clock())    # fixed: no refill between two requests
    for k, v in {"symbols": Symbols(), "candles": Candles(), "history": q, "limits": main._Limits(),
                 "userdb": None, "derivs": None}.items():
        monkeypatch.setattr(st, k, v, raising=False)

    async def who(request):
        return request.headers.get("x-test-user") or None
    monkeypatch.setattr(auth, "optional_user_id", who)
    history._shares.clear()
    transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 1))
    return httpx.AsyncClient(transport=transport, base_url="http://t"), q


def _now():
    return int(time.time() * 1000)


async def test_guests_get_the_fresh_window_only(api):
    client, _ = api
    cut = fresh_cutoff("1h")
    r = await client.get("/api/bars", params={"id": "fake:X", "interval": "1h", "limit": 2000})
    assert r.status_code == 200 and len(r.json()["bars"]) == 1000   # the newest page, at most 1000 bars
    to = cut + 300 * HOUR
    r = (await client.get("/api/bars", params={"id": "fake:X", "interval": "1h", "to": to, "limit": 500})).json()
    assert r["bars"][0][0] == cut and r["more"] is False and r["locked"] == "login"   # cut at the window's edge
    assert r["floor"] == cut
    r = await client.get("/api/bars", params={"id": "fake:X", "interval": "1h", "to": cut})
    assert r.status_code == 403 and r.json()["code"] == "login_required"


async def test_accounts_pay_only_for_bars_older_than_the_window(api):
    client, q = api
    cut = fresh_cutoff("1h")
    h = {"x-test-user": "trader", "cf-connecting-ip": "203.0.113.50"}
    r = (await client.get("/api/bars", params={"id": "fake:X", "interval": "1h", "to": cut + 200 * HOUR, "limit": 500}, headers=h)).json()
    assert len(r["bars"]) == 500 and "locked" not in r
    assert q._mem["u:trader"].tokens == WALLET - 300            # 300 of the 500 are older than the window
    await client.get("/api/bars", params={"id": "fake:X", "interval": "1h", "to": cut - 10 * DAY, "limit": 500}, headers=h)
    assert q._mem["u:trader"].tokens == WALLET - 800


async def test_a_stopped_account_keeps_the_fresh_window(api):
    client, q = api
    h = {"x-test-user": "bulk", "cf-connecting-ip": "203.0.113.51"}
    g = await q.admit("bulk", "203.0.113.51", 10)
    q.settle(g.ticket, STOP + 1)
    cut = fresh_cutoff("1h")
    r = await client.get("/api/bars", params={"id": "fake:X", "interval": "1h", "to": cut - DAY}, headers=h)
    assert r.status_code == 429 and r.json()["code"] == "stopped" and r.json()["until"] > _now()
    assert int(r.headers["retry-after"]) > 3600
    r = (await client.get("/api/bars", params={"id": "fake:X", "interval": "1h", "to": cut + 100 * HOUR}, headers=h)).json()
    assert r["locked"] == "stopped" and r["bars"][0][0] == cut and r["until"] > _now()


async def test_paced_answers_carry_retry_after(api):
    client, q = api
    h = {"x-test-user": "fast", "cf-connecting-ip": "203.0.113.52"}
    g = await q.admit("fast", "203.0.113.52", 1)
    q.settle(g.ticket, 0)
    q._mem["u:fast"].tokens = -1e6
    params = {"id": "fake:X", "interval": "1h", "to": fresh_cutoff("1h") - DAY, "limit": 500}
    assert (await client.get("/api/bars", params=params, headers=h)).status_code == 200
    r = await client.get("/api/bars", params=params, headers=h)
    assert r.status_code == 429 and r.json()["code"] == "slow" and r.headers["retry-after"] == str(int(SLOW_S))


async def test_a_shared_chart_opens_its_moment_to_guests(api, monkeypatch):
    client, _ = api
    made = _now() - 200 * DAY
    moment = made - 10 * HOUR
    shares = {"abcdefgh12": {"state": {"symbol": "fake:X", "interval": "1h", "to": moment}, "created_at": made},
              "oldmoment1": {"state": {"symbol": "fake:X", "interval": "1h", "to": made - 60 * DAY}, "created_at": made}}

    async def call(userdb, fn, sid):
        return shares.get(sid)
    import app.userdata.routes as routes
    monkeypatch.setattr(routes, "call", call)
    near = {"id": "fake:X", "interval": "1h", "to": moment + 300 * HOUR, "limit": 500}
    assert (await client.get("/api/bars", params=near)).status_code == 403          # without the link
    r = await client.get("/api/bars", params={**near, "share": "abcdefgh12"})
    assert r.status_code == 200 and len(r.json()["bars"]) == 500 and "locked" not in r.json()
    r = await client.get("/api/bars", params={**near, "interval": "15m", "share": "abcdefgh12"})
    assert r.status_code == 403                                                       # another interval: no
    edge = (await client.get("/api/bars", params={**near, "to": moment - 1000 * HOUR, "share": "abcdefgh12"})).json()
    assert edge["locked"] == "login" and edge["bars"][0][0] == (moment - FRESH_BARS * HOUR) // HOUR * HOUR == edge["floor"]
    far = {**near, "to": moment - 2 * FRESH_BARS * HOUR, "share": "abcdefgh12"}
    assert (await client.get("/api/bars", params=far)).status_code == 403            # far before the moment: no
    between = {**near, "to": moment + 1300 * HOUR, "share": "abcdefgh12"}            # after its window, before now's
    assert (await client.get("/api/bars", params=between)).status_code == 403
    # a link that names a moment already old when it was made opens nothing (no way around the wallet)
    old = {**near, "to": made - 60 * DAY + HOUR, "share": "oldmoment1"}
    assert (await client.get("/api/bars", params=old)).status_code == 403


async def test_the_newest_page_of_a_sparse_market_is_cut_for_guests_and_charged_to_accounts(api):
    client, q = api
    main.app.state.candles = Candles(step=DAY)               # 1000 "hourly" bars a day apart (the fixture puts it back)
    cut = fresh_cutoff("1h")
    r = (await client.get("/api/bars", params={"id": "fake:X", "interval": "1h", "limit": 1000})).json()
    assert r["locked"] == "login" and min(b[0] for b in r["bars"]) >= cut and len(r["bars"]) <= 51
    h = {"x-test-user": "sparse", "cf-connecting-ip": "203.0.113.70"}
    r = (await client.get("/api/bars", params={"id": "fake:X", "interval": "1h", "limit": 1000}, headers=h)).json()
    older = sum(1 for b in r["bars"] if b[0] < cut)
    assert "locked" not in r and len(r["bars"]) == 1000 and q._mem["u:sparse"].tokens == WALLET - older


async def test_liquidation_history_is_guarded_like_candles(api, monkeypatch, tmp_path):
    client, q = api
    d = Derivs(DerivStore(tmp_path / "derivs.sqlite"), SimpleNamespace())
    now = _now() // MINUTE * MINUTE
    d.store.add_liq([("BTCUSDT", now - k * HOUR, 10.0, 5.0) for k in range(3000)])
    perp = SymbolInfo(id="bybit:BTCUSDT", market="bybit", symbol="BTCUSDT", name="BTC", type="crypto", exchange="Bybit")

    class Syms:
        async def get(self, id):
            return perp if id == "bybit:BTCUSDT" else None
    main.app.state.derivs = d          # (the fixture puts both back)
    main.app.state.symbols = Syms()
    cut = fresh_cutoff("1h")
    p = {"id": "bybit:BTCUSDT", "interval": "1h", "from": now - 2500 * HOUR, "to": now + MINUTE}
    r = (await client.get("/api/derivs/liq", params=p)).json()
    assert r["locked"] == "login" and r["exhausted"] is False and min(x[0] for x in r["points"]) >= cut
    r = await client.get("/api/derivs/liq", params={**p, "to": cut})
    assert r.status_code == 403 and r.json()["code"] == "login_required"
    h = {"x-test-user": "trader2", "cf-connecting-ip": "203.0.113.60"}
    r = (await client.get("/api/derivs/liq", params=p, headers=h)).json()
    older = sum(1 for x in r["points"] if x[0] < cut)
    assert "locked" not in r and older > 1000 and q._mem["u:trader2"].tokens == WALLET - older
    # a monthly chart gets daily buckets: its free window is 1200 days of them, not 1200 months
    d.store.add_liq([("BTCUSDT", now - k * DAY, 1.0, 1.0) for k in range(1300, 3000, 7)])
    r = (await client.get("/api/derivs/liq", params={**p, "interval": "1M", "from": now - 3000 * DAY})).json()
    assert r["locked"] == "login" and min(x[0] for x in r["points"]) >= now - FRESH_BARS * DAY - DAY
    d.store.close()


async def test_a_cancelled_derivs_request_gives_its_slot_back(monkeypatch):
    import asyncio

    from app import derivs as dv
    monkeypatch.setattr(dv, "_running", asyncio.Semaphore(0))   # every slot taken: the request waits …
    task = asyncio.create_task(dv._admit("198.51.100.99"))
    await asyncio.sleep(0.05)
    task.cancel()                                               # … and the client goes away
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dv._per_ip.count("198.51.100.99") == 0
