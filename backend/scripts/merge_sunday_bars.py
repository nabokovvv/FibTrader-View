"""One-off: fold the Sunday daily bars already stored for the biquote CFD market into the Monday after them (the
app does this for every new bar since 2026-11-06, see markets/biquote.py: day_bucket / merge_sundays).

Runs on the server next to the app (each symbol is rewritten in one transaction). The bar count of bq-archive
drops by the number of Sundays, so the nightly backup refuses to replace its copy until it is run once with
FV_BACKUP_FORCE=1 (see ops/server/fv-backup-r2).

    ./venv/bin/python backend/scripts/merge_sunday_bars.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.candles.store import MarketStore  # noqa: E402
from app.markets.biquote import CATALOG, merge_sundays  # noqa: E402


def merge_store(store: MarketStore, dry_run: bool = False) -> dict[str, int]:
    """symbol -> Sunday bars folded."""
    out = {}
    for sym in CATALOG:
        rows = store.read(sym, "1D", None, None, 10_000_000)
        merged = merge_sundays(rows)
        if len(merged) == len(rows) and all(a[0] == b[0] for a, b in zip(merged, rows)):
            continue
        out[sym] = len(rows) - len(merged)
        if dry_run:
            continue
        cov = store.coverage(sym, "1D")
        with store._lock:
            c = store._conn
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute("DELETE FROM bars WHERE symbol=? AND interval='1D'", (sym,))
                c.executemany("INSERT INTO bars(symbol, interval, ts, o, h, l, c, v) VALUES(?,?,?,?,?,?,?,?)",
                              [(sym, "1D", int(b[0]), b[1], b[2], b[3], b[4], b[5]) for b in merged])
                if merged:
                    c.execute("INSERT INTO coverage(symbol, interval, first_ts, last_ts, exhausted, updated_at) "
                              "VALUES(?,?,?,?,?,?) ON CONFLICT(symbol, interval) DO UPDATE SET first_ts=excluded.first_ts, "
                              "last_ts=excluded.last_ts, exhausted=excluded.exhausted, updated_at=excluded.updated_at",
                              (sym, "1D", int(merged[0][0]), int(merged[-1][0]), int(cov.exhausted if cov else True),
                               int(time.time())))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    store = MarketStore(config.CANDLES_DIR / "bq-archive.sqlite")
    with store._lock:
        store._conn.execute("PRAGMA busy_timeout=30000")
    res = merge_store(store, args.dry_run)
    for sym, n in res.items():
        print(f"{sym}: {n} Sunday bars folded into Monday")
    print(f"{'would fold' if args.dry_run else 'folded'} {sum(res.values())} bars in {len(res)} symbols")
    return 0


if __name__ == "__main__":
    sys.exit(main())
