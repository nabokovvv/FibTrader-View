"""Add older bars to the biquote CFD archives from ``dukascopy_history.py`` output (one ``<SYMBOL>.npz`` per
instrument). Runs on the server next to the app, while it is running.

Only bars OLDER than the first bar already stored for a series are added (the archives keep biquote's own bars
untouched; nothing at or after the join is written), into the same files the app reads: 1h / 4h / 1D into
``bq-archive.sqlite``, 1m … 30m into ``bq-minutes-archive.sqlite``. Afterwards each series' coverage starts at its
oldest bar and is marked exhausted. A series whose older bars would end more than 4 days before the first stored bar
is skipped (no months-long hole inside a chart). Re-running is harmless (it adds only what is still older).

    ./venv/bin/python backend/scripts/import_history.py /path/to/npz-dir [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.candles.store import MarketStore  # noqa: E402
from app.markets.biquote import ARCHIVE_INTERVALS, CATALOG, MINUTE_ARCHIVE_INTERVALS, merge_sundays  # noqa: E402

BATCH = 50_000
MAX_GAP_MS = 4 * 86_400_000     # a weekend (+ a holiday) between the last added and the first stored bar is fine
IVMS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000,
        "1D": 86_400_000}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    stores = {"archive": MarketStore(config.CANDLES_DIR / "bq-archive.sqlite"),
              "minutes-archive": MarketStore(config.CANDLES_DIR / "bq-minutes-archive.sqlite")}
    for st in stores.values():   # the app writes to the same files: wait for its short transactions
        with st._lock:
            st._conn.execute("PRAGMA busy_timeout=30000")
    kind = {**{iv: "archive" for iv in ARCHIVE_INTERVALS}, **{iv: "minutes-archive" for iv in MINUTE_ARCHIVE_INTERVALS}}
    total = 0
    for f in sorted(Path(args.src).glob("*.npz")):
        sym = f.stem
        if sym not in CATALOG:
            print(f"{sym}: not a biquote symbol, skipped")
            continue
        data = np.load(f)
        line = []
        for iv in data.files:
            if iv not in kind:
                continue
            store = stores[kind[iv]]
            arr = data[iv]
            if iv == "1D":   # Sunday evening sessions belong to Monday's bar (as the app stores them)
                arr = np.array(merge_sundays(arr.tolist()), dtype=float).reshape(-1, 6)
            first, last = store.bounds(sym, iv)
            if first is None:
                line.append(f"{iv}: no biquote bars yet, skipped")
                continue
            arr = arr[arr[:, 0] < first]
            ok = np.isfinite(arr[:, 1:5]).all(axis=1) & (arr[:, 3] > 0) & (arr[:, 3] <= arr[:, 2])
            arr = arr[ok]
            if not len(arr):
                line.append(f"{iv}: 0")
                continue
            gap = first - arr[-1, 0]
            if gap > max(MAX_GAP_MS, 3 * IVMS[iv]):   # Dukascopy stopped long before biquote starts: no hole
                line.append(f"{iv}: skipped (gap of {gap / 86_400_000:.0f} days before the first stored bar)")
                continue
            if not args.dry_run:
                for i in range(0, len(arr), BATCH):   # small batches: the server has 1 GB and the app keeps writing
                    chunk = arr[i:i + BATCH]
                    store.upsert(sym, iv, [[int(r[0]), *map(float, r[1:6])] for r in chunk])
                new_first, new_last = store.bounds(sym, iv)
                store.set_coverage(sym, iv, new_first, new_last, True)
            total += len(arr)
            line.append(f"{iv}: +{len(arr)}")
        print(f"{sym}: " + ", ".join(line), flush=True)
    print(f"{'would add' if args.dry_run else 'added'} {total} bars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
