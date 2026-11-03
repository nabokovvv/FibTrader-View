from datetime import datetime, timezone

from app.candles.resample import bucket_start, resample
from app.intervals import HOUR, MINUTE


def ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


def test_bucket_start_intraday_and_calendar():
    t = ts("2026-10-28 13:47:00")
    assert bucket_start(t, "5m") == ts("2026-10-28 13:45:00")
    assert bucket_start(t, "4h") == ts("2026-10-28 12:00:00")
    assert bucket_start(t, "1D") == ts("2026-10-28 00:00:00")
    assert bucket_start(t, "1W") == ts("2026-10-26 00:00:00")   # Monday
    assert bucket_start(ts("2026-11-01 10:00:00"), "1W") == ts("2026-10-26 00:00:00")  # Sunday -> same week
    assert bucket_start(t, "1M") == ts("2026-10-01 00:00:00")


def test_resample_1m_to_5m_ohlcv():
    base = ts("2026-10-28 13:45:00")
    bars = [[base + i * MINUTE, 10 + i, 12 + i, 9 + i, 11 + i, 1.0] for i in range(7)]  # 13:45..13:51
    out = resample(bars, "5m")
    assert [b[0] for b in out] == [base, base + 5 * MINUTE]
    first = out[0]
    assert first[1] == 10 and first[2] == 16 and first[3] == 9 and first[4] == 15 and first[5] == 5.0
    second = out[1]
    assert second[1] == 15 and second[5] == 2.0


def test_resample_skips_session_gaps_without_inventing_bars():
    day1 = ts("2026-10-29 13:00:00")
    day2 = ts("2026-10-30 13:00:00")
    bars = [[day1, 1, 2, 0.5, 1.5, 1], [day1 + HOUR, 1.5, 3, 1, 2, 1], [day2, 5, 6, 4, 5.5, 1]]
    out = resample(bars, "4h")
    assert len(out) == 2
    assert out[0][0] == ts("2026-10-29 12:00:00") and out[0][2] == 3 and out[0][3] == 0.5
    assert out[1][0] == ts("2026-10-30 12:00:00")
