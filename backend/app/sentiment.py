"""Market-wide daily sentiment for the 1D chart panels (the same series on every symbol).

* ``fng`` — Crypto Fear & Greed Index from alternative.me (0–100, one value per UTC day since 2018-02-01). The whole
  history is fetched once into ``fear-greed.json`` next to the candle files (not backed up: it can be fetched again);
  afterwards only the newest days are asked for, once the next day's value is due (alternative.me updates at 00:00 UTC).
* ``yt`` — pressure of crypto YouTube influencers: column ``pressure`` of a CSV ``date,index,...,pressure`` published
  by an external job once a day (value of UTC day D arrives on D+1 around 02:15 UTC). A cumulative line with no fixed
  scale (read its slope and turns), so it is not clamped. The file is only read, re-read when it changes.

A point is ``[ts of the UTC day in ms, value]``.
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from . import config, limits
from .intervals import DAY, MINUTE

log = logging.getLogger("lv.sentiment")

FNG_URL = "https://api.alternative.me/fng/"
FNG_FILE = config.CANDLES_DIR / "fear-greed.json"
YT_CSV = Path(os.path.expanduser(os.getenv("LV_YT_SENTIMENT_CSV", "~/feeds/yt-sentiment/sentiment.csv")))
MAX_POINTS = 20_000          # ~55 years of days: a damaged / hostile file cannot blow up memory
YT_MAX_BYTES = 5 * 1024**2


def _clean(points: dict[int, float]) -> list[list[float]]:
    return [[ts, v] for ts, v in sorted(points.items())][-MAX_POINTS:]


def parse_fng(payload: object) -> dict[int, float]:
    out: dict[int, float] = {}
    data = payload.get("data") if isinstance(payload, dict) else None
    for row in data if isinstance(data, list) else []:
        try:
            ts = int(row["timestamp"]) * 1000 // DAY * DAY
            v = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= v <= 100:
            out[ts] = v
    return out


def parse_yt_csv(text: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for row in csv.DictReader(text.splitlines()):
        try:
            d = datetime.strptime((row.get("date") or "").strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            v = float(row.get("pressure") or "")
        except ValueError:
            continue
        if math.isfinite(v) and abs(v) < 1e9:
            out[int(d.timestamp() * 1000)] = round(v, 3)
        if len(out) >= MAX_POINTS:
            break
    return out


class Sentiment:
    def __init__(self, fng_file: Path = FNG_FILE, yt_csv: Path = YT_CSV):
        self.fng_file, self.yt_csv = fng_file, yt_csv
        self.fng: list[list[float]] = []
        self.yt: list[list[float]] = []
        self._yt_sig: tuple[float, int] | None = None
        self._yt_checked = 0.0
        self._task: asyncio.Task | None = None
        try:
            self.fng = _clean({int(ts): float(v) for ts, v in json.loads(fng_file.read_text())})
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001 — a damaged cache is fetched again
            log.warning("fear & greed cache unreadable (%s): fetching it again", exc)

    # ---------------------------------------------------------------- Fear & Greed
    def _fng_due(self, now_ms: int) -> bool:
        # the value of today appears at 00:00 UTC: ask a few minutes later, and again hourly until it is there
        return not self.fng or self.fng[-1][0] < now_ms // DAY * DAY

    async def refresh_fng(self, client: httpx.AsyncClient) -> int:
        full = not self.fng or self.fng[-1][0] < time.time() * 1000 - 20 * DAY
        r = await client.get(FNG_URL, params={"limit": 0 if full else 30, "format": "json"}, timeout=30)
        r.raise_for_status()
        got = parse_fng(r.json())
        if not got:
            return 0
        merged = {int(ts): v for ts, v in self.fng}
        merged.update(got)
        self.fng = _clean(merged)
        tmp = self.fng_file.with_suffix(".tmp")
        await asyncio.to_thread(self._save, tmp)
        return len(got)

    def _save(self, tmp: Path) -> None:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(self.fng, separators=(",", ":")))
        tmp.replace(self.fng_file)

    async def _loop(self) -> None:
        async with httpx.AsyncClient(headers={"user-agent": "FibTrader View"}) as client:
            while True:
                now = int(time.time() * 1000)
                if self._fng_due(now) and now % DAY >= 5 * MINUTE:
                    try:
                        n = await self.refresh_fng(client)
                        log.info("fear & greed: %d days, newest %s", n,
                                 time.strftime("%Y-%m-%d", time.gmtime(self.fng[-1][0] / 1000)) if self.fng else "-")
                    except Exception as exc:  # noqa: BLE001
                        log.warning("fear & greed fetch failed: %s", exc)
                await asyncio.sleep(15 * 60 if self._fng_due(int(time.time() * 1000)) else 60 * 60)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---------------------------------------------------------------- YouTube influencers
    def _load_yt(self) -> None:
        try:
            st = self.yt_csv.stat()
        except OSError:
            self.yt, self._yt_sig = [], None
            return
        sig = (st.st_mtime, st.st_size)
        if sig == self._yt_sig or st.st_size > YT_MAX_BYTES:
            return
        try:
            self.yt = _clean(parse_yt_csv(self.yt_csv.read_text(encoding="utf-8", errors="replace")))
            self._yt_sig = sig
        except OSError as exc:
            log.warning("influencer sentiment unreadable: %s", exc)

    async def yt_points(self) -> list[list[float]]:
        if time.monotonic() - self._yt_checked > 60:   # the file changes once a day: a stat a minute is plenty
            self._yt_checked = time.monotonic()
            await asyncio.to_thread(self._load_yt)
        return self.yt


# ------------------------------------------------------------------------- API
router = APIRouter()
_rate = limits.RateLimiter(2, 20)


@router.get("/sentiment/{kind}")
async def sentiment(request: Request, kind: str, start: int = Query(0, alias="from"), end: int | None = Query(None, alias="to")) -> dict:
    s: Sentiment | None = getattr(request.app.state, "sentiment", None)
    if kind not in ("fng", "yt"):
        raise HTTPException(404)
    ip = request.scope.get("state", {}).get("client_ip", "unknown")
    if not _rate.allow(ip):
        raise HTTPException(429, "too many requests, slow down")
    pts = [] if s is None else (s.fng if kind == "fng" else await s.yt_points())
    hi = end if end is not None else 1 << 62
    return {"points": [p for p in pts if start <= p[0] < hi], "available": bool(pts)}
