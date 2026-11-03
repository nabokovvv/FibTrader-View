"""SQLite raw-bar store: one database per market, only *closed* bars at provider-native intervals.

coverage(symbol, interval) = [first_ts, last_ts] is a contiguous range in which every bar the provider
has is stored. `exhausted` means the provider has nothing older than first_ts.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..markets.base import Bar

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars(
  symbol TEXT NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL,
  o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL, v REAL NOT NULL,
  PRIMARY KEY(symbol, interval, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS coverage(
  symbol TEXT NOT NULL, interval TEXT NOT NULL,
  first_ts INTEGER NOT NULL, last_ts INTEGER NOT NULL,
  exhausted INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL,
  PRIMARY KEY(symbol, interval)) WITHOUT ROWID;
"""


@dataclass
class Coverage:
    first_ts: int
    last_ts: int
    exhausted: bool


class MarketStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(coverage)")}
            if "last_access" not in cols:  # when a chart last read this series (maintenance keeps what people use)
                self._conn.execute("ALTER TABLE coverage ADD COLUMN last_access INTEGER NOT NULL DEFAULT 0")

    # --------------------------------------------------------------- coverage
    def coverage(self, symbol: str, interval: str) -> Coverage | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT first_ts, last_ts, exhausted FROM coverage WHERE symbol=? AND interval=?",
                (symbol, interval)).fetchone()
        return Coverage(row[0], row[1], bool(row[2])) if row else None

    def set_coverage(self, symbol: str, interval: str, first_ts: int, last_ts: int, exhausted: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO coverage(symbol, interval, first_ts, last_ts, exhausted, updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(symbol, interval) DO UPDATE SET first_ts=excluded.first_ts, last_ts=excluded.last_ts, "
                "exhausted=excluded.exhausted, updated_at=excluded.updated_at",
                (symbol, interval, first_ts, last_ts, int(exhausted), int(time.time())))

    # ------------------------------------------------------------------- bars
    def upsert(self, symbol: str, interval: str, bars: list[Bar]) -> int:
        if not bars:
            return 0
        with self._lock:
            self._conn.execute("BEGIN")
            self._conn.executemany(
                "INSERT INTO bars(symbol, interval, ts, o, h, l, c, v) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol, interval, ts) DO UPDATE SET o=excluded.o, h=excluded.h, l=excluded.l, "
                "c=excluded.c, v=excluded.v",
                [(symbol, interval, int(b[0]), b[1], b[2], b[3], b[4], b[5]) for b in bars])
            self._conn.execute("COMMIT")
        return len(bars)

    def read(self, symbol: str, interval: str, start_ts: int | None, end_ts: int | None,
             limit: int | None = None) -> list[Bar]:
        """Bars with start_ts <= ts <= end_ts ascending; with `limit`, the *latest* `limit` bars of the range."""
        sql = "SELECT ts, o, h, l, c, v FROM bars WHERE symbol=? AND interval=?"
        args: list = [symbol, interval]
        if start_ts is not None:
            sql += " AND ts>=?"; args.append(int(start_ts))
        if end_ts is not None:
            sql += " AND ts<=?"; args.append(int(end_ts))
        if limit:
            sql += " ORDER BY ts DESC LIMIT ?"; args.append(int(limit))
        else:
            sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        if limit:
            rows.reverse()
        return [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows]

    # ------------------------------------------------------------ maintenance
    def touch(self, symbol: str, interval: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE coverage SET last_access=? WHERE symbol=? AND interval=?",
                               (int(time.time()), symbol, interval))

    def series(self) -> list[tuple[str, str, int, int, int]]:
        """(symbol, interval, first_ts, last_ts, last_access) of every stored series."""
        with self._lock:
            return [tuple(r) for r in self._conn.execute(
                "SELECT symbol, interval, first_ts, last_ts, CASE WHEN last_access > 0 THEN last_access ELSE updated_at END "
                "FROM coverage").fetchall()]

    def prune(self, symbol: str, interval: str, before_ts: int) -> int:
        """Delete bars older than `before_ts`; coverage then starts there (history can be fetched again later)."""
        with self._lock:
            self._conn.execute("BEGIN")
            n = self._conn.execute("DELETE FROM bars WHERE symbol=? AND interval=? AND ts<?", (symbol, interval, before_ts)).rowcount
            cov = self._conn.execute("SELECT last_ts FROM coverage WHERE symbol=? AND interval=?", (symbol, interval)).fetchone()
            if cov is not None:
                if cov[0] < before_ts:
                    self._conn.execute("DELETE FROM coverage WHERE symbol=? AND interval=?", (symbol, interval))
                else:
                    self._conn.execute("UPDATE coverage SET first_ts=MAX(first_ts, ?), exhausted=0 WHERE symbol=? AND interval=?",
                                       (before_ts, symbol, interval))
            self._conn.execute("COMMIT")
        return n

    def drop(self, symbol: str, interval: str) -> int:
        with self._lock:
            self._conn.execute("BEGIN")
            n = self._conn.execute("DELETE FROM bars WHERE symbol=? AND interval=?", (symbol, interval)).rowcount
            self._conn.execute("DELETE FROM coverage WHERE symbol=? AND interval=?", (symbol, interval))
            self._conn.execute("COMMIT")
        return n

    def compact(self) -> None:
        """Give freed pages back to the disk (incremental if enabled, else nothing) and refresh planner stats."""
        with self._lock:
            mode = self._conn.execute("PRAGMA auto_vacuum").fetchone()[0]
            if mode == 2:
                self._conn.execute("PRAGMA incremental_vacuum")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("PRAGMA optimize")

    def enable_incremental_vacuum(self, max_bytes: int) -> bool:
        """One-time switch to auto_vacuum=INCREMENTAL (needs a full VACUUM, so only for small files)."""
        with self._lock:
            if self._conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2:
                return True
            if self.path.stat().st_size > max_bytes:
                return False
            self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self._conn.execute("VACUUM")
            return True

    def bounds(self, symbol: str, interval: str) -> tuple[int, int] | tuple[None, None]:
        """Oldest and newest stored bar."""
        with self._lock:
            r = self._conn.execute("SELECT MIN(ts), MAX(ts) FROM bars WHERE symbol=? AND interval=?",
                                   (symbol, interval)).fetchone()
        return (r[0], r[1]) if r and r[0] is not None else (None, None)

    def count(self, symbol: str, interval: str) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM bars WHERE symbol=? AND interval=?",
                                      (symbol, interval)).fetchone()[0]

    def stats(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbol, interval, COUNT(*), MIN(ts), MAX(ts) FROM bars GROUP BY symbol, interval").fetchall()
        return {f"{r[0]}@{r[1]}": {"bars": r[2], "first": r[3], "last": r[4]} for r in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class StoreRegistry:
    def __init__(self, root: Path):
        self.root = root
        self._stores: dict[str, MarketStore] = {}
        self._lock = threading.Lock()

    def get(self, market: str) -> MarketStore:
        with self._lock:
            st = self._stores.get(market)
            if st is None:
                st = self._stores[market] = MarketStore(self.root / f"{market}.sqlite")
            return st

    def close_all(self) -> None:
        for st in self._stores.values():
            st.close()
