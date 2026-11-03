"""Shared chart links: a small JSON snapshot (symbol, interval, chart type, indicators, drawings) behind a random id.

Anyone with the link can open it read-only without signing in; the chart then loads live candles like any visitor
(GET /api/bars is public and rate-limited). Links live 90 days; an account keeps at most `shares` of them (the
oldest makes room).
"""
from __future__ import annotations

import math
import re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import limits
from .auth import user_id
from .intervals import INTERVALS
from .userdata.routes import _payload, db, quota

router = APIRouter()
TTL_MS = 90 * 86_400_000
SID_RE = re.compile(r"[A-Za-z0-9_-]{8,32}")
_reads = limits.RateLimiter(2, 30)


class ShareIn(BaseModel):
    state: dict[str, Any]


NAME_RE = re.compile(r"[A-Za-z0-9_]{1,64}")


def _num(v, lo: float = -1e15, hi: float = 1e15) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise HTTPException(400, "bad number in the chart snapshot")
    return v


def _str(v, n: int) -> str:
    if not isinstance(v, str) or not 0 < len(v) <= n:
        raise HTTPException(400, "bad text in the chart snapshot")
    return v


def clean_snapshot(st: dict) -> dict:
    """Only the fields the viewer uses, with checked types and ranges (the link is opened by other people)."""
    out: dict[str, Any] = {"v": 1, "symbol": _str(st.get("symbol"), 64)}
    if st.get("interval") not in INTERVALS:
        raise HTTPException(400, "bad interval in the chart snapshot")
    out["interval"] = st["interval"]
    if isinstance(st.get("chartType"), str) and NAME_RE.fullmatch(st["chartType"]):
        out["chartType"] = st["chartType"]
    if st.get("theme") in ("dark", "light"):
        out["theme"] = st["theme"]
    if st.get("to") is not None:
        out["to"] = _num(st["to"], 0, 1e14)
    inds = st.get("indicators") or []
    if not isinstance(inds, list) or len(inds) > 30:
        raise HTTPException(400, "bad indicators in the chart snapshot")
    out["indicators"] = []
    for i in inds:
        if not isinstance(i, dict) or not NAME_RE.fullmatch(str(i.get("name", ""))):
            raise HTTPException(400, "bad indicator in the chart snapshot")
        params = i.get("calcParams") or []
        if not isinstance(params, list) or len(params) > 8:
            raise HTTPException(400, "bad indicator parameters in the chart snapshot")
        item: dict[str, Any] = {"key": _str(str(i.get("key") or i["name"]), 64), "name": i["name"], "main": bool(i.get("main")),
                                "calcParams": [_num(p, -1000, 1000) for p in params]}
        opts = i.get("options")
        if isinstance(opts, dict):
            item["options"] = {k: _str(opts[k], 64) for k in ("symbol", "label") if isinstance(opts.get(k), str) and opts[k]}
        out["indicators"].append(item)
    draws = st.get("drawings") or []
    if not isinstance(draws, list) or len(draws) > 300:
        raise HTTPException(400, "bad drawings in the chart snapshot")
    out["drawings"] = []
    for d in draws:
        if not isinstance(d, dict) or not NAME_RE.fullmatch(str(d.get("name", ""))) or not isinstance(d.get("points"), list) \
                or len(d["points"]) > 64:
            raise HTTPException(400, "bad drawing in the chart snapshot")
        pts = []
        for p in d["points"]:
            if not isinstance(p, dict):
                raise HTTPException(400, "bad drawing point in the chart snapshot")
            pts.append({"timestamp": _num(p.get("timestamp"), 0, 1e14), "value": _num(p.get("value"))})
        item = {"name": d["name"], "points": pts}
        for k in ("styles", "extendData"):
            if isinstance(d.get(k), dict):
                item[k] = d[k]
        out["drawings"].append(item)
    return out


@router.post("/shares")
async def create_share(request: Request, body: ShareIn, uid: str = Depends(user_id)) -> dict:
    p = limits.profile()
    state_json = _payload(clean_snapshot(body.state), p.share_bytes, "chart snapshot")
    sid = secrets.token_urlsafe(9)
    await db(request, "create_share_checked", uid, sid, state_json, TTL_MS, max_shares=p.shares, quota=quota())
    return {"id": sid, "expires_in_days": TTL_MS // 86_400_000}


@router.get("/shares/{sid}")
async def get_share(request: Request, sid: str) -> dict:
    """Public: no sign-in needed to open a shared chart."""
    ip = request.scope.get("state", {}).get("client_ip", "unknown")
    if not _reads.allow(ip):
        raise HTTPException(429, "too many requests, slow down")
    if not SID_RE.fullmatch(sid):
        raise HTTPException(404, "link not found")
    share = await db(request, "share", sid)
    if share is None:
        raise HTTPException(404, "this link has expired or was deleted")
    return share


@router.delete("/shares/{sid}")
async def delete_share(request: Request, sid: str, uid: str = Depends(user_id)) -> dict:
    return {"deleted": await db(request, "delete_share", uid, sid)}
