from app.candles.store import StoreRegistry


def test_upsert_read_and_coverage(tmp_path):
    reg = StoreRegistry(tmp_path)
    st = reg.get("bybit")
    bars = [[1000 + i * 60_000, 1.0, 2.0, 0.5, 1.5, 10.0] for i in range(10)]
    assert st.upsert("BTCUSDT", "1m", bars) == 10
    # upsert is idempotent and updates values
    bars[3][4] = 9.9
    st.upsert("BTCUSDT", "1m", bars[3:4])
    rows = st.read("BTCUSDT", "1m", None, None)
    assert len(rows) == 10 and rows[3][4] == 9.9

    tail = st.read("BTCUSDT", "1m", None, None, limit=3)
    assert [r[0] for r in tail] == [b[0] for b in bars[-3:]]
    ranged = st.read("BTCUSDT", "1m", bars[2][0], bars[4][0])
    assert len(ranged) == 3

    assert st.coverage("BTCUSDT", "1m") is None
    st.set_coverage("BTCUSDT", "1m", 1000, bars[-1][0], False)
    cov = st.coverage("BTCUSDT", "1m")
    assert cov and cov.first_ts == 1000 and cov.last_ts == bars[-1][0] and not cov.exhausted
    st.set_coverage("BTCUSDT", "1m", 500, bars[-1][0], True)
    assert st.coverage("BTCUSDT", "1m").exhausted

    assert "BTCUSDT@1m" in st.stats()
    reg.close_all()
