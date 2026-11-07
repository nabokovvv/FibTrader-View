import asyncio
import json

from app.sentiment import Sentiment, parse_fng, parse_yt_csv

DAY = 86_400_000


def test_parse_fng_skips_bad_rows_and_snaps_to_day():
    got = parse_fng({"data": [{"value": "28", "timestamp": "1793923200"}, {"value": "x", "timestamp": "1"},
                              {"value": "150", "timestamp": "1793836800"}, {"timestamp": "5"}]})
    assert got == {1793923200000: 28.0}


def test_parse_yt_csv():
    text = ("date,index,index_old,active_weight,coverage,videos,pressure\n2023-01-26,59.29,54.07,43.81,1.0,4,0.096\n"
            "bad,1\n2023-01-27,50,1,1,1,1,\n2023-01-28,50,1,1,1,1,-120.5\n2023-01-29,50,1,1,1,1,nan\n")
    assert parse_yt_csv(text) == {1674691200000: 0.096, 1674864000000: -120.5}


def test_yt_reloads_when_the_file_changes(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("date,pressure\n2026-11-04,40\n")
    s = Sentiment(tmp_path / "fng.json", f)
    assert asyncio.run(s.yt_points()) == [[1793750400000, 40.0]]
    f.write_text("date,pressure\n2026-11-04,40\n2026-11-05,54.7\n")
    s._yt_checked = 0
    assert [p[1] for p in asyncio.run(s.yt_points())] == [40.0, 54.7]
    f.unlink()
    s._yt_checked = 0
    assert asyncio.run(s.yt_points()) == []


def test_fng_cache_round_trip_and_due(tmp_path):
    cache = tmp_path / "fng.json"
    cache.write_text(json.dumps([[2 * DAY, 30], [DAY, 20]]))
    s = Sentiment(cache, tmp_path / "none.csv")
    assert s.fng == [[DAY, 20.0], [2 * DAY, 30.0]]
    assert not s._fng_due(2 * DAY + 3600_000)
    assert s._fng_due(3 * DAY + 1)
    cache.write_text("garbage")
    assert Sentiment(cache, tmp_path / "none.csv").fng == []
