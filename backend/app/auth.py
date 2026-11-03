"""Who is asking: the local user on the private instance, a Supabase Auth user on the public one.

Supabase access tokens are verified locally against the project's JWKS (asymmetric signing keys); no request goes
to Supabase per API call. Hardening (Astra review 2026-11-03): tokens over 8 KB are rejected before parsing; the key
set is cached and refreshed at most once a minute even when tokens name unknown `kid`s, by a single in-flight fetch
with a short timeout; verification runs inline (ES256/RS256 verify is sub-millisecond), so it never occupies the
thread pool; rejected tokens are rate limited per IP.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx
import jwt
from fastapi import HTTPException, Request

from . import config, limits

log = logging.getLogger("lv.auth")
LOCAL_USER = "local"
MAX_TOKEN = 8 * 1024
REFRESH_EVERY_S = 3600       # routine refresh of the key set
UNKNOWN_KID_COOLDOWN_S = 60  # minimum gap between refreshes triggered by an unknown kid
ALGS = ["ES256", "RS256"]


class _Jwks:
    def __init__(self) -> None:
        self.keys: dict[str, object] = {}
        self.fetched_at = -1e9  # "never"
        self._lock = asyncio.Lock()

    def _url(self) -> str:
        return f"{config.SUPABASE_URL}/auth/v1/.well-known/jwks.json"

    async def _refresh(self) -> None:
        async with self._lock:
            if time.monotonic() - self.fetched_at < (UNKNOWN_KID_COOLDOWN_S if self.keys else 5):
                return  # someone else just refreshed
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    r = await c.get(self._url())
                    r.raise_for_status()
                    data = r.json()
                keys = {}
                for k in data.get("keys", []):
                    try:
                        jwk = jwt.PyJWK.from_dict(k)
                    except Exception:  # noqa: BLE001  unsupported key type
                        continue
                    if k.get("kid"):
                        keys[k["kid"]] = jwk.key
                if keys:
                    self.keys = keys
            except Exception as exc:  # noqa: BLE001
                log.warning("jwks refresh failed: %s", exc)
            finally:
                self.fetched_at = time.monotonic()

    async def key(self, kid: str | None):
        age = time.monotonic() - self.fetched_at
        if not self.keys:
            if age > 5:  # Supabase unreachable: retry, but not on every request
                await self._refresh()
        elif age > REFRESH_EVERY_S:
            await self._refresh()
        elif kid not in self.keys and age > UNKNOWN_KID_COOLDOWN_S:
            await self._refresh()
        return self.keys.get(kid) if kid else None


_jwks: _Jwks | None = None
_fails: limits.RateLimiter | None = None


def _jwks_cache() -> _Jwks:
    global _jwks
    if _jwks is None:
        _jwks = _Jwks()
    return _jwks


def _fail_limiter() -> limits.RateLimiter:
    global _fails
    if _fails is None:
        n = limits.profile().auth_fail_per_min
        _fails = limits.RateLimiter(n / 60, n)
    return _fails


async def _verify(token: str) -> str:
    header = jwt.get_unverified_header(token)
    if header.get("alg") not in ALGS:
        raise jwt.InvalidTokenError("unexpected alg")
    key = await _jwks_cache().key(header.get("kid"))
    if key is None:
        raise jwt.InvalidTokenError("unknown signing key")
    claims = jwt.decode(
        token, key, algorithms=ALGS, audience="authenticated",
        issuer=f"{config.SUPABASE_URL}/auth/v1", options={"require": ["exp", "sub", "iss", "aud"]},
    )
    if claims.get("is_anonymous"):
        raise jwt.InvalidTokenError("anonymous sessions are not accepted")
    sub = str(claims["sub"])
    if not sub or len(sub) > 64:
        raise jwt.InvalidTokenError("bad subject")
    return sub


async def user_id(request: Request) -> str:
    """FastAPI dependency: the id that owns drawings, watchlists, layouts and settings."""
    if config.AUTH != "supabase":
        return LOCAL_USER
    ip = request.scope.get("state", {}).get("client_ip", "unknown")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "sign in required")
    if len(token) > MAX_TOKEN:
        raise HTTPException(401, "invalid session")
    if not _fail_limiter().has(ip):  # out of failed attempts: do not even verify
        raise HTTPException(429, "too many failed sign-in attempts")
    try:
        return await _verify(token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(401, "session expired") from exc
    except (jwt.InvalidTokenError, jwt.PyJWKError, ValueError) as exc:
        if not _fail_limiter().allow(ip):
            raise HTTPException(429, "too many failed sign-in attempts") from exc
        log.info("rejected token from %s: %s", ip, exc)
        raise HTTPException(401, "invalid session") from exc


def public_config() -> dict:
    """What the frontend needs to know before it can sign in."""
    return {
        "auth": "supabase" if config.AUTH == "supabase" else "none",
        "supabase_url": config.SUPABASE_URL if config.AUTH == "supabase" else None,
        "supabase_key": config.SUPABASE_PUBLISHABLE_KEY if config.AUTH == "supabase" else None,
        "i18n": config.I18N,
    }
