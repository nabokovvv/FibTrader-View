"""ASGI guard in front of the app: request body cap (before any JSON parsing), per-IP API rate limit and,
on the public instance, security headers."""
from __future__ import annotations

import asyncio
import json

from . import config, limits


def _csp() -> str:
    supa = config.SUPABASE_URL
    connect = " ".join(x for x in ("'self'", supa, supa.replace("https://", "wss://") if supa else "") if x)
    return "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        f"connect-src {connect}",
        "img-src 'self' data: https://*.googleusercontent.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "object-src 'none'",
    ])


class Guard:
    def __init__(self, app):
        self.app = app
        self.p = limits.profile()
        self.public = config.AUTH == "supabase"
        self.api = limits.RateLimiter(self.p.api_rate, self.p.api_burst)
        self.uploads = limits.KeyedGate(self.p.uploads_per_ip)
        self.uploads_total = 0
        self.headers = [
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"x-frame-options", b"DENY"),
            (b"content-security-policy", _csp().encode()),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        ] if self.public else []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path: str = scope.get("path", "")
        peer = (scope.get("client") or (None, None))[0]
        ip = limits.client_ip(scope.get("headers", []), peer)
        scope.setdefault("state", {})["client_ip"] = ip

        if path.startswith("/api/") and not self.api.allow(ip):
            return await _reply(send, 429, "too many requests, slow down")

        # body cap: read (at most) the whole body first, then replay it. Only API calls carry bodies; elsewhere a
        # write method is refused before anything is read.
        if scope.get("method") in ("POST", "PUT", "PATCH", "DELETE"):
            if not path.startswith("/api/"):
                return await _reply(send, 405, "method not allowed")
            for k, v in scope.get("headers", []):
                if k == b"content-length":
                    try:
                        if int(v) > self.p.body_bytes:
                            return await _reply(send, 413, "request body is too large")
                    except ValueError:
                        return await _reply(send, 400, "bad content-length")
            if self.uploads_total >= self.p.uploads_global or not self.uploads.enter(ip):
                return await _reply(send, 429, "too many uploads at once")
            self.uploads_total += 1
            try:
                body = await asyncio.wait_for(self._read_body(receive), timeout=self.p.body_deadline_s)
            except TimeoutError:
                return await _reply(send, 408, "request body took too long")
            except _TooLarge:
                return await _reply(send, 413, "request body is too large")
            except _Gone:
                return
            finally:
                self.uploads_total -= 1
                self.uploads.leave(ip)
            sent = False
            upstream = receive

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await upstream()  # later calls wait for the disconnect, as ASGI expects
            receive = replay

        if not self.headers:
            return await self.app(scope, receive, send)

        async def send_with_headers(msg):
            if msg["type"] == "http.response.start":
                msg = {**msg, "headers": list(msg.get("headers", [])) + self.headers}
            await send(msg)
        return await self.app(scope, receive, send_with_headers)


    async def _read_body(self, receive) -> bytes:
        chunks, size = [], 0
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                raise _Gone
            chunk = msg.get("body", b"")
            size += len(chunk)
            if size > self.p.body_bytes:
                raise _TooLarge
            chunks.append(chunk)
            if not msg.get("more_body"):
                return b"".join(chunks)


class _TooLarge(Exception):
    pass


class _Gone(Exception):
    pass


async def _reply(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if status == 429:
        headers.append((b"retry-after", b"2"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
