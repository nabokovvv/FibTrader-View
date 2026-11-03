"""Candle service against a fake provider: gap filling, live bar handling, exhaustion, resampling."""
import asyncio

import pytest

from app.candles.service import CandleService, now_ms
from app.candles.store import StoreRegistry
from app.intervals import HOUR, MINUTE
from app.markets.base import SymbolInfo


class FakeProvider:
    """Serves 1m bars for a fixed history [first_ts, now]; counts requests."""
    market = "fake"
    native_intervals = frozenset({"1m", "1h", "1D"})

    def __init__(self, first_ts: int):
        self.first_ts = first_ts
        self.calls: list[tuple[str, int, int]] = []

    async def symbols(self):
        return []

    async def resolve(self, symbol):
        return None

    def history_floor(self, interval):
        return None

    async def quotes(self, symbols):
        return []

    async def fetch_bars(self, symbol, interval, start_ms, end_ms):
        self.calls.append((interval, start_ms, end_ms))
        step = {"1m": MINUTE, "1h": HOUR}[interval]
        now = now_ms()
        start = max(start_ms, self.first_ts)
        start -= start % step
        out = []
        t = start
        while t <= min(end_ms, now):
            out.append([t, 1.0, 2.0, 0.5, 1.5, 1.0])
            t += step
        return out


SYM = SymbolInfo(id="fake:X", market="fake", symbol="X", name="X", type="crypto", exchange="F")


@pytest.fixture
def svc(tmp_path):
    now = now_ms()
    prov = FakeProvider(first_ts=now - 10 * 24 * HOUR)  # 10 days of history
    reg = StoreRegistry(tmp_path)
    return CandleService({"fake": prov}, reg), prov, reg


def test_init_page_returns_limit_and_stores_closed_bars(svc):
    service, prov, reg = svc
    page = asyncio.run(service.get_bars(SYM, "1m", None, 100))
    assert len(page.bars) == 100
    assert page.more is True
    # last bar is the live (unclosed) one and is NOT stored
    store = reg.get("fake")
    cov = store.coverage("X", "1m")
    assert cov is not None
    assert page.bars[-1][0] >= cov.last_ts
    assert store.count("X", "1m") >= 99
    assert len(prov.calls) == 1


def test_older_page_fills_gap_and_marks_exhausted(svc):
    service, prov, reg = svc
    first = asyncio.run(service.get_bars(SYM, "1h", None, 50))
    oldest = first.bars[0][0]
    older = asyncio.run(service.get_bars(SYM, "1h", oldest, 50))
    assert older.bars and older.bars[-1][0] < oldest
    assert all(a[0] < b[0] for a, b in zip(older.bars, older.bars[1:]))
    # walk back until history ends
    page = older
    for _ in range(10):
        if not page.more:
            break
        page = asyncio.run(service.get_bars(SYM, "1h", page.bars[0][0], 50))
    assert page.more is False
    cov = reg.get("fake").coverage("X", "1h")
    assert cov.exhausted


def test_resampled_interval_uses_native_store(svc):
    service, prov, reg = svc
    page = asyncio.run(service.get_bars(SYM, "5m", None, 20))
    assert page.native == "1m" and len(page.bars) == 20
    assert all(b[0] % (5 * MINUTE) == 0 for b in page.bars)
    assert page.bars[0][5] == 5.0  # five 1m bars aggregated
    assert reg.get("fake").count("X", "5m") == 0  # only native bars are stored


def test_second_call_hits_cache(svc):
    service, prov, reg = svc
    asyncio.run(service.get_bars(SYM, "1h", None, 30))
    n = len(prov.calls)
    asyncio.run(service.get_bars(SYM, "1h", None, 30))
    # only the tail (from last closed bar) is re-fetched
    assert len(prov.calls) == n + 1
    assert prov.calls[-1][1] >= prov.calls[0][1]
