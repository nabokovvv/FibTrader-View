"""Alerts, Telegram links, feedback and shared charts (schema v2), as a mixin of UserDB.

Every method runs under the UserDB lock; check-then-write sequences use one IMMEDIATE transaction (`_tx`).
"""
from __future__ import annotations

import json
import time

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, symbol TEXT NOT NULL, cond TEXT NOT NULL,
  level REAL, line TEXT, drawing_id TEXT, note TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL, fired_at INTEGER, fired_price REAL, rev INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS alerts_user ON alerts(user_id, drawing_id);
CREATE INDEX IF NOT EXISTS alerts_active ON alerts(active);
CREATE TABLE IF NOT EXISTS tg_links(
  user_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, lang TEXT NOT NULL, linked_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tg_codes(
  code TEXT PRIMARY KEY, user_id TEXT NOT NULL, lang TEXT NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
  context TEXT NOT NULL, created_at INTEGER NOT NULL, issue_url TEXT, attempts INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS feedback_user ON feedback(user_id, created_at);
CREATE TABLE IF NOT EXISTS shares(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS shares_user ON shares(user_id);
"""

# drawings whose two points define a sloped line an alert can follow (extended to the right)
LINE_DRAWINGS = {"segment", "rayLine", "straightLine"}
# drawings that are one price level
LEVEL_DRAWINGS = {"horizontalStraightLine", "horizontalRayLine", "horizontalSegment", "priceLine"}


def _now() -> int:
    return int(time.time() * 1000)


def _alert_row(r) -> dict:
    return {"id": r["id"], "symbol": r["symbol"], "cond": r["cond"], "level": r["level"],
            "line": json.loads(r["line"]) if r["line"] else None, "drawing_id": r["drawing_id"], "note": r["note"],
            "active": bool(r["active"]), "created_at": r["created_at"], "fired_at": r["fired_at"],
            "fired_price": r["fired_price"]}


def drawing_target(name: str, points: list) -> tuple[float | None, list[float] | None]:
    """(level, line) an alert on this drawing follows; (None, None) if the drawing cannot carry an alert."""
    try:
        pts = [(float(p["timestamp"]), float(p["value"])) for p in points[:2]]
    except (KeyError, TypeError, ValueError):
        return None, None
    if name in LEVEL_DRAWINGS and pts:
        return pts[0][1], None
    if name in LINE_DRAWINGS and len(pts) == 2 and pts[0][0] != pts[1][0]:
        (t1, p1), (t2, p2) = sorted(pts)
        return None, [t1, p1, t2, p2]
    return None, None


class ExtraMixin:
    # Set by UserDB.__init__ (shared connection, lock and transaction helper).
    alerts_rev = 0  # bumped on every alert change: the engine reloads when it differs

    # ------------------------------------------------------------------ alerts
    def alerts(self, uid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alerts WHERE user_id=? ORDER BY active DESC, COALESCE(fired_at, created_at) DESC",
                (uid,)).fetchall()
        return [_alert_row(r) for r in rows]

    def create_alert_checked(self, uid: str, symbol: str, cond: str, level: float | None, line: list[float] | None,
                             drawing_id: str | None, note: str, *, max_active: int, max_total: int) -> dict:
        from .db import LimitError
        with self._tx() as c:
            if c.execute("SELECT COUNT(*) FROM alerts WHERE user_id=? AND active=1", (uid,)).fetchone()[0] >= max_active:
                raise LimitError(409, f"alert limit reached ({max_active} active)")
            total = c.execute("SELECT COUNT(*) FROM alerts WHERE user_id=?", (uid,)).fetchone()[0]
            if total >= max_total:  # make room by forgetting the oldest FIRED alerts (paused ones are the user's)
                c.execute("DELETE FROM alerts WHERE id IN (SELECT id FROM alerts WHERE user_id=? AND active=0 "
                          "AND fired_at IS NOT NULL ORDER BY fired_at LIMIT ?)", (uid, total - max_total + 1))
                if c.execute("SELECT COUNT(*) FROM alerts WHERE user_id=?", (uid,)).fetchone()[0] >= max_total:
                    raise LimitError(409, f"too many paused alerts ({max_total} in total): delete some")
            cur = c.execute(
                "INSERT INTO alerts(user_id, symbol, cond, level, line, drawing_id, note, active, created_at) "
                "VALUES(?,?,?,?,?,?,?,1,?)",
                (uid, symbol, cond, level, json.dumps(line) if line else None, drawing_id, note, _now()))
            row = c.execute("SELECT * FROM alerts WHERE id=?", (cur.lastrowid,)).fetchone()
        self.alerts_rev += 1
        return _alert_row(row)

    def update_alert_checked(self, uid: str, aid: int, *, active: bool | None, level: float | None, cond: str | None,
                             note: str | None, max_active: int) -> dict | None:
        from .db import LimitError
        with self._tx() as c:
            row = c.execute("SELECT * FROM alerts WHERE id=? AND user_id=?", (aid, uid)).fetchone()
            if row is None:
                return None
            if active and not row["active"]:
                n = c.execute("SELECT COUNT(*) FROM alerts WHERE user_id=? AND active=1", (uid,)).fetchone()[0]
                if n >= max_active:
                    raise LimitError(409, f"alert limit reached ({max_active} active)")
                c.execute("UPDATE alerts SET active=1, fired_at=NULL, fired_price=NULL WHERE id=?", (aid,))
            elif active is False:
                c.execute("UPDATE alerts SET active=0 WHERE id=?", (aid,))
            # any change makes the engine's snapshot of this alert stale (it must not fire on the old target)
            c.execute("UPDATE alerts SET rev=rev+1 WHERE id=?", (aid,))
            if level is not None and row["line"] is None:
                c.execute("UPDATE alerts SET level=? WHERE id=?", (level, aid))
            if cond is not None:
                c.execute("UPDATE alerts SET cond=? WHERE id=?", (cond, aid))
            if note is not None:
                c.execute("UPDATE alerts SET note=? WHERE id=?", (note, aid))
            row = c.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
        self.alerts_rev += 1
        return _alert_row(row)

    def delete_alert(self, uid: str, aid: int) -> bool:
        with self._lock:
            n = self._conn.execute("DELETE FROM alerts WHERE id=? AND user_id=?", (aid, uid)).rowcount
        self.alerts_rev += 1
        return n > 0

    def active_alerts(self) -> list[dict]:
        """All armed alerts (for the engine)."""
        with self._lock:
            rows = self._conn.execute("SELECT id, user_id, symbol, cond, level, line, note, rev FROM alerts WHERE active=1").fetchall()
        return [{"id": r["id"], "user_id": r["user_id"], "symbol": r["symbol"], "cond": r["cond"], "level": r["level"],
                 "line": json.loads(r["line"]) if r["line"] else None, "note": r["note"], "rev": r["rev"]} for r in rows]

    def fire_alerts(self, fired: list[tuple[int, int, float]]) -> set[int]:
        """Switch fired alerts off; (id, rev, price). Only alerts still armed AND unchanged since the engine read them
        fire (no double notifications, no firing on a target the user has just moved). Returns the fired ids."""
        done: set[int] = set()
        now = _now()
        with self._tx() as c:
            for aid, rev, price in fired:
                if c.execute("UPDATE alerts SET active=0, fired_at=?, fired_price=? WHERE id=? AND active=1 AND rev=?",
                             (now, price, aid, rev)).rowcount:
                    done.add(aid)
        self.alerts_rev += 1
        return done

    def _sync_drawing_alerts(self, c, uid: str, drawing_id: str, name: str, data_json: str) -> None:
        """A drawing with alerts moved: the alerts follow it. Called inside the drawing's transaction."""
        if not c.execute("SELECT 1 FROM alerts WHERE user_id=? AND drawing_id=? LIMIT 1", (uid, drawing_id)).fetchone():
            return
        try:
            points = json.loads(data_json).get("points") or []
        except (ValueError, AttributeError):
            return
        level, line = drawing_target(name, points)
        if level is None and line is None:
            c.execute("DELETE FROM alerts WHERE user_id=? AND drawing_id=?", (uid, drawing_id))
        else:
            c.execute("UPDATE alerts SET level=?, line=?, rev=rev+1 WHERE user_id=? AND drawing_id=?",
                      (level, json.dumps(line) if line else None, uid, drawing_id))
        self.alerts_rev += 1

    # ---------------------------------------------------------------- telegram
    def tg_new_code(self, uid: str, code: str, lang: str, ttl_ms: int) -> None:
        now = _now()
        with self._tx() as c:
            c.execute("DELETE FROM tg_codes WHERE user_id=? OR expires_at<?", (uid, now))
            c.execute("INSERT INTO tg_codes(code, user_id, lang, expires_at) VALUES(?,?,?,?)", (code, uid, lang, now + ttl_ms))

    def tg_claim(self, code: str, chat_id: int) -> tuple[str, str] | None:
        """Link the chat to the account that created `code` (one use). Returns (user_id, lang)."""
        with self._tx() as c:
            row = c.execute("SELECT user_id, lang, expires_at FROM tg_codes WHERE code=?", (code,)).fetchone()
            if row is None or row["expires_at"] < _now():
                return None
            c.execute("DELETE FROM tg_codes WHERE code=?", (code,))
            c.execute("INSERT INTO tg_links(user_id, chat_id, lang, linked_at) VALUES(?,?,?,?) ON CONFLICT(user_id) "
                      "DO UPDATE SET chat_id=excluded.chat_id, lang=excluded.lang, linked_at=excluded.linked_at",
                      (row["user_id"], chat_id, row["lang"], _now()))
            return row["user_id"], row["lang"]

    def tg_link(self, uid: str) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT chat_id, lang, linked_at FROM tg_links WHERE user_id=?", (uid,)).fetchone()
        return dict(r) if r else None

    def tg_unlink(self, uid: str) -> bool:
        with self._lock:
            return self._conn.execute("DELETE FROM tg_links WHERE user_id=?", (uid,)).rowcount > 0

    def tg_unlink_chat(self, chat_id: int) -> int:
        with self._lock:
            return self._conn.execute("DELETE FROM tg_links WHERE chat_id=?", (chat_id,)).rowcount

    def tg_chats(self, uids: list[str]) -> dict[str, tuple[int, str]]:
        out: dict[str, tuple[int, str]] = {}
        for i in range(0, len(uids), 500):  # stay far below SQLite's bound-parameter limit
            part = uids[i:i + 500]
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT user_id, chat_id, lang FROM tg_links WHERE user_id IN ({','.join('?' * len(part))})", part).fetchall()
            out.update({r["user_id"]: (r["chat_id"], r["lang"]) for r in rows})
        return out

    # ---------------------------------------------------------------- feedback
    def add_feedback_checked(self, uid: str, kind: str, text: str, context_json: str, *, per_day: int,
                             global_per_day: int = 100) -> int:
        from .db import LimitError
        now = _now()
        with self._tx() as c:
            if c.execute("SELECT COUNT(*) FROM feedback WHERE user_id=? AND created_at>?",
                         (uid, now - 86_400_000)).fetchone()[0] >= per_day:
                raise LimitError(429, f"you can send {per_day} messages a day, try again tomorrow")
            # many fresh accounts must not flood the public issue tracker either
            if c.execute("SELECT COUNT(*) FROM feedback WHERE created_at>?", (now - 86_400_000,)).fetchone()[0] >= global_per_day:
                raise LimitError(429, "too many messages today, please try again tomorrow")
            cur = c.execute("INSERT INTO feedback(user_id, kind, text, context, created_at) VALUES(?,?,?,?,?)",
                            (uid, kind, text, context_json, now))
            return cur.lastrowid

    def feedback_pending(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM feedback WHERE issue_url IS NULL AND attempts<10 ORDER BY id LIMIT ?",
                                      (limit,)).fetchall()
        return [dict(r) for r in rows]

    def feedback_result(self, fid: int, issue_url: str | None) -> None:
        with self._lock:
            if issue_url:
                self._conn.execute("UPDATE feedback SET issue_url=? WHERE id=?", (issue_url, fid))
            else:
                self._conn.execute("UPDATE feedback SET attempts=attempts+1 WHERE id=?", (fid,))

    # ------------------------------------------------------------------ shares
    def create_share_checked(self, uid: str, sid: str, state_json: str, ttl_ms: int, *, max_shares: int, quota) -> None:
        from .db import LimitError
        now = _now()
        with self._tx() as c:
            c.execute("DELETE FROM shares WHERE user_id=? AND expires_at<?", (uid, now))
            n = c.execute("SELECT COUNT(*) FROM shares WHERE user_id=?", (uid,)).fetchone()[0]
            if n >= max_shares:  # the oldest link makes room
                c.execute("DELETE FROM shares WHERE id IN (SELECT id FROM shares WHERE user_id=? ORDER BY created_at LIMIT ?)",
                          (uid, n - max_shares + 1))
            self._grow_ok(uid, len(state_json.encode()), quota)
            if c.execute("SELECT 1 FROM shares WHERE id=?", (sid,)).fetchone():
                raise LimitError(409, "try again")
            c.execute("INSERT INTO shares(id, user_id, state, created_at, expires_at) VALUES(?,?,?,?,?)",
                      (sid, uid, state_json, now, now + ttl_ms))

    def share(self, sid: str) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT state, created_at, expires_at FROM shares WHERE id=?", (sid,)).fetchone()
        if r is None or r["expires_at"] < _now():
            return None
        return {"state": json.loads(r["state"]), "created_at": r["created_at"], "expires_at": r["expires_at"]}

    def delete_share(self, uid: str, sid: str) -> bool:
        with self._lock:
            return self._conn.execute("DELETE FROM shares WHERE id=? AND user_id=?", (sid, uid)).rowcount > 0

    # --------------------------------------------------------------- upkeep
    def prune_extra(self) -> dict:
        """Nightly: expired links and codes, fired alerts older than 90 days, sent feedback older than a year."""
        now = _now()
        with self._lock:
            c = self._conn
            out = {
                "shares": c.execute("DELETE FROM shares WHERE expires_at<?", (now,)).rowcount,
                "tg_codes": c.execute("DELETE FROM tg_codes WHERE expires_at<?", (now,)).rowcount,
                "alerts": c.execute("DELETE FROM alerts WHERE active=0 AND fired_at<?", (now - 90 * 86_400_000,)).rowcount,
                "feedback": c.execute("DELETE FROM feedback WHERE issue_url IS NOT NULL AND created_at<?",
                                      (now - 365 * 86_400_000,)).rowcount,
            }
        return out
