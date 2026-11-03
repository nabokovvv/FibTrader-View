from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, WebSocket

from .. import config

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.app.state.hub.handle(ws)


@router.get("/api/realtime/status")
async def realtime_status(request: Request) -> dict:
    """Which streams are open — private instance only (on the public one it would show what others watch)."""
    if config.AUTH == "supabase":
        raise HTTPException(404)
    return request.app.state.hub.status()
