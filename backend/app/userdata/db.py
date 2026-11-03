"""User data in SQLite: drawings (per symbol), watchlists, layouts, settings — all scoped by user id.

On the private instance there is a single user, ``local``; on the public one the id is the Supabase user id.
Schema version lives in ``PRAGMA user_version``:
  0 → 1  every table gets ``user_id`` (existing rows become ``local``); keys include the user id.
  1 → 2  alerts, Telegram links, feedback, shared charts (see extra.py).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .extra import SCHEMA_V2, ExtraMixin

VERSION = 2

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS drawings(
  user_id TEXT NOT NULL, id TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL, data TEXT NOT NULL,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY(user_id, id));
CREATE INDEX IF NOT EXISTS drawings_user_symbol ON drawings(user_id, symbol);
CREATE TABLE IF NOT EXISTS watchlists(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, name TEXT NOT NULL, items TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS watchlists_user ON watchlists(user_id);
CREATE TABLE IF NOT EXISTS layouts(
  user_id TEXT NOT NULL, name TEXT NOT NULL, state TEXT NOT NULL, updated_at INTEGER NOT NULL,
  PRIMARY KEY(user_id, name));
CREATE TABLE IF NOT EXISTS settings(
  user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(user_id, key));
"""

# v0 had the same tables without user_id
MIGRATE_0_1 = """
ALTER TABLE drawings RENAME TO drawings_v0;
ALTER TABLE watchlists RENAME TO watchlists_v0;
ALTER TABLE layouts RENAME TO layouts_v0;
ALTER TABLE settings RENAME TO settings_v0;
DROP INDEX IF EXISTS drawings_symbol;
""" + SCHEMA_V1 + """
INSERT INTO drawings(user_id, id, symbol, name, data, created_at, updated_at)
  SELECT 'local', id, symbol, name, data, created_at, updated_at FROM drawings_v0;
INSERT INTO watchlists(id, user_id, name, items, position, created_at)
  SELECT id, 'local', name, items, position, created_at FROM watchlists_v0;
INSERT INTO layouts(user_id, name, state, updated_at) SELECT 'local', name, state, updated_at FROM layouts_v0;
INSERT INTO settings(user_id, key, value) SELECT 'local', key, value FROM settings_v0;
DROP TABLE drawings_v0; DROP TABLE watchlists_v0; DROP TABLE layouts_v0; DROP TABLE settings_v0;
"""


def _now() -> int:
    return int(time.time() * 1000)


def _dump(v) -> str:
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def _b(s: str) -> int:
    return len(s.encode())


class LimitError(Exception):
    """A per-account or storage limit; `status` is the HTTP code to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Quota:
    user_bytes: int
    db_bytes: int | None


class UserDB(ExtraMixin):
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= VERSION:
            return
        if version == 0:
            has_v0 = self._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='drawings'").fetchone()
            steps = (MIGRATE_0_1 if has_v0 else SCHEMA_V1) + SCHEMA_V2
        else:
            steps = SCHEMA_V2
        # one script = one transaction (executescript would commit a transaction opened outside of it)
        script = "BEGIN;" + steps + f"PRAGMA user_version={VERSION}; COMMIT;"
        try:
            self._conn.executescript(script)
        except Exception:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --------------------------------------------------------------- drawings
    def drawings(self, uid: str, symbol: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, data, updated_at FROM drawings WHERE user_id=? AND symbol=? ORDER BY created_at",
                (uid, symbol)).fetchall()
        return [{"id": r["id"], "name": r["name"], **json.loads(r["data"]), "updated_at": r["updated_at"]} for r in rows]

    def count_drawings(self, uid: str, symbol: str | None = None) -> int:
        with self._lock:
            if symbol is None:
                return self._conn.execute("SELECT COUNT(*) FROM drawings WHERE user_id=?", (uid,)).fetchone()[0]
            return self._conn.execute("SELECT COUNT(*) FROM drawings WHERE user_id=? AND symbol=?", (uid, symbol)).fetchone()[0]

    def user_bytes(self, uid: str) -> int:
        """Stored JSON of one account (drawings + watchlists + layouts + settings)."""
        with self._lock:
            return self._user_bytes(uid)

    _USER_BYTES = """SELECT
            (SELECT COALESCE(SUM(LENGTH(CAST(data AS BLOB)) + LENGTH(CAST(name AS BLOB)) + LENGTH(CAST(symbol AS BLOB))), 0)
               FROM drawings WHERE user_id=?) +
            (SELECT COALESCE(SUM(LENGTH(CAST(items AS BLOB)) + LENGTH(CAST(name AS BLOB))), 0) FROM watchlists WHERE user_id=?) +
            (SELECT COALESCE(SUM(LENGTH(CAST(state AS BLOB)) + LENGTH(CAST(name AS BLOB))), 0) FROM layouts WHERE user_id=?) +
            (SELECT COALESCE(SUM(LENGTH(CAST(value AS BLOB)) + LENGTH(CAST(key AS BLOB))), 0) FROM settings WHERE user_id=?)"""

    def _user_bytes(self, uid: str) -> int:
        return self._conn.execute(self._USER_BYTES, (uid, uid, uid, uid)).fetchone()[0]

    def _used_db_bytes(self) -> int:
        """Pages in use (a DELETE frees pages without shrinking the file, so the file size is not the measure)."""
        c = self._conn
        return (c.execute("PRAGMA page_count").fetchone()[0] - c.execute("PRAGMA freelist_count").fetchone()[0]) * \
            c.execute("PRAGMA page_size").fetchone()[0]

    @contextmanager
    def _tx(self):
        """One lock + one IMMEDIATE transaction: check-then-write sequences are atomic."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def _grow_ok(self, uid: str, delta: int, quota: Quota) -> None:
        if delta <= 0:
            return  # shrinking or equal writes are always allowed (they are how a full account recovers)
        if quota.db_bytes is not None and self._used_db_bytes() > quota.db_bytes:
            raise LimitError(507, "storage is full, try again later")
        if self._user_bytes(uid) + delta > quota.user_bytes:
            raise LimitError(413, "your account storage is full: delete some drawings or layouts")

    # ------------------------------------------------- checked (quota-aware) writes
    def put_drawing_checked(self, uid: str, drawing_id: str, symbol: str, name: str, data_json: str, *,
                            max_total: int, max_per_symbol: int, quota: Quota) -> None:
        new = _b(data_json) + _b(name) + _b(symbol)
        now = _now()
        with self._tx() as c:
            old = c.execute("SELECT symbol, LENGTH(CAST(data AS BLOB)) + LENGTH(CAST(name AS BLOB)) + LENGTH(CAST(symbol AS BLOB)) "
                            "FROM drawings WHERE user_id=? AND id=?", (uid, drawing_id)).fetchone()
            if old is None and c.execute("SELECT COUNT(*) FROM drawings WHERE user_id=?", (uid,)).fetchone()[0] >= max_total:
                raise LimitError(409, f"drawing limit reached ({max_total})")
            if (old is None or old[0] != symbol) and \
                    c.execute("SELECT COUNT(*) FROM drawings WHERE user_id=? AND symbol=?", (uid, symbol)).fetchone()[0] >= max_per_symbol:
                raise LimitError(409, f"drawing limit for this symbol reached ({max_per_symbol})")
            self._grow_ok(uid, new - (old[1] if old else 0), quota)
            c.execute(
                "INSERT INTO drawings(user_id, id, symbol, name, data, created_at, updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id, id) DO UPDATE SET symbol=excluded.symbol, name=excluded.name, data=excluded.data, "
                "updated_at=excluded.updated_at", (uid, drawing_id, symbol, name, data_json, now, now))
            self._sync_drawing_alerts(c, uid, drawing_id, name, data_json)

    def update_drawings_checked(self, uid: str, items: list[tuple[str, str, str, str]], *, quota: Quota) -> int:
        """Rewrite several EXISTING drawings in one transaction (lock all / hide all); unknown ids are skipped.
        items: (id, symbol, name, data_json). Returns how many were updated."""
        n = 0
        now = _now()
        with self._tx() as c:
            for drawing_id, symbol, name, data_json in items:
                old = c.execute("SELECT symbol, LENGTH(CAST(data AS BLOB)) + LENGTH(CAST(name AS BLOB)) + LENGTH(CAST(symbol AS BLOB)) "
                                "FROM drawings WHERE user_id=? AND id=?", (uid, drawing_id)).fetchone()
                if old is None or old[0] != symbol:
                    continue
                self._grow_ok(uid, _b(data_json) + _b(name) + _b(symbol) - old[1], quota)
                c.execute("UPDATE drawings SET name=?, data=?, updated_at=? WHERE user_id=? AND id=?",
                          (name, data_json, now, uid, drawing_id))
                self._sync_drawing_alerts(c, uid, drawing_id, name, data_json)
                n += 1
        return n

    def seed_watchlists_checked(self, uid: str, lists: list[tuple[str, list[str]]], *, max_lists: int, quota: Quota) -> bool:
        """Create the default lists only if the account still has none (atomic with the check)."""
        with self._tx() as c:
            if c.execute("SELECT COUNT(*) FROM watchlists WHERE user_id=?", (uid,)).fetchone()[0]:
                return False
            for pos, (name, items) in enumerate(lists[:max_lists], start=1):
                items_json = json.dumps(items)
                self._grow_ok(uid, _b(items_json) + _b(name), quota)
                c.execute("INSERT INTO watchlists(user_id, name, items, position, created_at) VALUES(?,?,?,?,?)",
                          (uid, name, items_json, pos, _now()))
            return True

    def drawings_page(self, uid: str, symbol: str, offset: int, max_rows: int, max_bytes: int) -> tuple[list[dict], int | None]:
        """At most `max_rows` drawings and about `max_bytes` of JSON (always at least one), oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, data, updated_at, LENGTH(CAST(data AS BLOB)) FROM drawings WHERE user_id=? AND symbol=? "
                "ORDER BY created_at, id LIMIT ? OFFSET ?", (uid, symbol, max_rows + 1, offset)).fetchall()
        out, size = [], 0
        for r in rows[:max_rows]:
            if out and size + r[4] > max_bytes:
                break
            size += r[4]
            out.append({"id": r["id"], "name": r["name"], **json.loads(r["data"]), "updated_at": r["updated_at"]})
        more = len(out) < len(rows)
        return out, (offset + len(out) if more else None)

    def create_watchlist_checked(self, uid: str, name: str, items: list[str], *, max_lists: int, quota: Quota) -> dict:
        items_json = json.dumps(items)
        with self._tx() as c:
            if c.execute("SELECT COUNT(*) FROM watchlists WHERE user_id=?", (uid,)).fetchone()[0] >= max_lists:
                raise LimitError(409, f"watchlist limit reached ({max_lists})")
            self._grow_ok(uid, _b(items_json) + _b(name), quota)
            pos = c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM watchlists WHERE user_id=?", (uid,)).fetchone()[0]
            cur = c.execute("INSERT INTO watchlists(user_id, name, items, position, created_at) VALUES(?,?,?,?,?)",
                            (uid, name, items_json, pos, _now()))
            return {"id": cur.lastrowid, "name": name, "items": items, "position": pos}

    def update_watchlist_checked(self, uid: str, wid: int, name: str | None, items: list[str] | None,
                                 position: int | None, *, quota: Quota) -> bool:
        with self._tx() as c:
            old = c.execute("SELECT LENGTH(CAST(items AS BLOB)) + LENGTH(CAST(name AS BLOB)), name, items FROM watchlists "
                            "WHERE id=? AND user_id=?", (wid, uid)).fetchone()
            if old is None:
                return False
            new_name = name if name is not None else old[1]
            new_items = json.dumps(items) if items is not None else old[2]
            self._grow_ok(uid, _b(new_items) + _b(new_name) - old[0], quota)
            sets, args = ["name=?", "items=?"], [new_name, new_items]
            if position is not None:
                sets.append("position=?"); args.append(position)
            c.execute(f"UPDATE watchlists SET {', '.join(sets)} WHERE id=? AND user_id=?", (*args, wid, uid))
            return True

    def put_layout_checked(self, uid: str, name: str, state_json: str, *, max_layouts: int, quota: Quota) -> None:
        with self._tx() as c:
            old = c.execute("SELECT LENGTH(CAST(state AS BLOB)) + LENGTH(CAST(name AS BLOB)) FROM layouts WHERE user_id=? AND name=?",
                            (uid, name)).fetchone()
            if old is None and c.execute("SELECT COUNT(*) FROM layouts WHERE user_id=?", (uid,)).fetchone()[0] >= max_layouts:
                raise LimitError(409, f"layout limit reached ({max_layouts})")
            self._grow_ok(uid, _b(state_json) + _b(name) - (old[0] if old else 0), quota)
            c.execute("INSERT INTO layouts(user_id, name, state, updated_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(user_id, name) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                      (uid, name, state_json, _now()))

    def put_settings_checked(self, uid: str, values: dict, *, max_keys: int, max_bytes: int, quota: Quota) -> None:
        enc = {k: _dump(v) for k, v in values.items()}
        with self._tx() as c:
            cur = {r[0]: r[1] for r in c.execute("SELECT key, value FROM settings WHERE user_id=?", (uid,)).fetchall()}
            merged = {**cur, **enc}
            if len(merged) > max_keys:
                raise LimitError(413, f"at most {max_keys} settings")
            size = sum(_b(k) + _b(v) for k, v in merged.items())
            if size > max_bytes:
                raise LimitError(413, "settings are too large")
            self._grow_ok(uid, size - sum(_b(k) + _b(v) for k, v in cur.items()), quota)
            c.executemany("INSERT INTO settings(user_id, key, value) VALUES(?,?,?) ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                          [(uid, k, v) for k, v in enc.items()])

    def drawing_bytes(self, uid: str, drawing_id: str) -> int:
        with self._lock:
            r = self._conn.execute("SELECT LENGTH(data) + LENGTH(name) + LENGTH(symbol) FROM drawings WHERE user_id=? AND id=?",
                                   (uid, drawing_id)).fetchone()
        return r[0] if r else 0

    def file_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal"):
            try:
                total += Path(str(self.path) + suffix).stat().st_size
            except OSError:
                pass
        return total

    def has_drawing(self, uid: str, drawing_id: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM drawings WHERE user_id=? AND id=?", (uid, drawing_id)).fetchone() is not None

    def upsert_drawing(self, uid: str, drawing_id: str, symbol: str, name: str, data: dict) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO drawings(user_id, id, symbol, name, data, created_at, updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id, id) DO UPDATE SET symbol=excluded.symbol, name=excluded.name, data=excluded.data, "
                "updated_at=excluded.updated_at",
                (uid, drawing_id, symbol, name, _dump(data), now, now))

    def delete_drawing(self, uid: str, drawing_id: str) -> int:
        with self._tx() as c:  # alerts on the drawing go with it
            if c.execute("DELETE FROM alerts WHERE user_id=? AND drawing_id=?", (uid, drawing_id)).rowcount:
                self.alerts_rev += 1
            return c.execute("DELETE FROM drawings WHERE user_id=? AND id=?", (uid, drawing_id)).rowcount

    def delete_drawings(self, uid: str, symbol: str) -> int:
        with self._tx() as c:
            if c.execute("DELETE FROM alerts WHERE user_id=? AND symbol=? AND drawing_id IS NOT NULL", (uid, symbol)).rowcount:
                self.alerts_rev += 1
            return c.execute("DELETE FROM drawings WHERE user_id=? AND symbol=?", (uid, symbol)).rowcount

    # ------------------------------------------------------------- watchlists
    def watchlists(self, uid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT id, name, items, position FROM watchlists WHERE user_id=? ORDER BY position, id",
                                      (uid,)).fetchall()
        return [{"id": r["id"], "name": r["name"], "items": json.loads(r["items"]), "position": r["position"]} for r in rows]

    def all_watchlist_items(self) -> set[str]:
        """Every symbol on anybody's watchlist (for background jobs)."""
        with self._lock:
            rows = self._conn.execute("SELECT items FROM watchlists").fetchall()
        return {i for r in rows for i in json.loads(r["items"])}

    def create_watchlist(self, uid: str, name: str, items: list[str]) -> dict:
        with self._lock:
            pos = self._conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM watchlists WHERE user_id=?", (uid,)).fetchone()[0]
            cur = self._conn.execute("INSERT INTO watchlists(user_id, name, items, position, created_at) VALUES(?,?,?,?,?)",
                                     (uid, name, json.dumps(items), pos, _now()))
            return {"id": cur.lastrowid, "name": name, "items": items, "position": pos}

    def update_watchlist(self, uid: str, wid: int, name: str | None = None, items: list[str] | None = None,
                         position: int | None = None) -> bool:
        sets, args = [], []
        if name is not None:
            sets.append("name=?"); args.append(name)
        if items is not None:
            sets.append("items=?"); args.append(json.dumps(items))
        if position is not None:
            sets.append("position=?"); args.append(position)
        if not sets:
            return False
        args += [wid, uid]
        with self._lock:
            return self._conn.execute(f"UPDATE watchlists SET {', '.join(sets)} WHERE id=? AND user_id=?", args).rowcount > 0

    def delete_watchlist(self, uid: str, wid: int) -> bool:
        with self._lock:
            return self._conn.execute("DELETE FROM watchlists WHERE id=? AND user_id=?", (wid, uid)).rowcount > 0

    # ---------------------------------------------------------------- layouts
    def layouts(self, uid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT name, updated_at FROM layouts WHERE user_id=? ORDER BY name", (uid,)).fetchall()
        return [{"name": r["name"], "updated_at": r["updated_at"]} for r in rows]

    def layout(self, uid: str, name: str) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT state, updated_at FROM layouts WHERE user_id=? AND name=?", (uid, name)).fetchone()
        return {"name": name, "state": json.loads(r["state"]), "updated_at": r["updated_at"]} if r else None

    def put_layout(self, uid: str, name: str, state: dict) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO layouts(user_id, name, state, updated_at) VALUES(?,?,?,?) "
                               "ON CONFLICT(user_id, name) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                               (uid, name, _dump(state), _now()))

    def delete_layout(self, uid: str, name: str) -> bool:
        with self._lock:
            return self._conn.execute("DELETE FROM layouts WHERE user_id=? AND name=?", (uid, name)).rowcount > 0

    # --------------------------------------------------------------- settings
    def settings(self, uid: str) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings WHERE user_id=?", (uid,)).fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def put_settings(self, uid: str, values: dict) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(
                    "INSERT INTO settings(user_id, key, value) VALUES(?,?,?) ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                    [(uid, k, json.dumps(v)) for k, v in values.items()])
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def optimize(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("PRAGMA optimize")

    # ----------------------------------------------------------------- backup
    def backup_to(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self._conn.backup(target)
            finally:
                target.close()
