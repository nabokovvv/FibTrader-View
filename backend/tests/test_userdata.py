"""Multi-user data: v0 → v1 migration, isolation between users, Supabase token checks."""
import json
import sqlite3
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app import auth, config
from app.userdata.db import UserDB

V0 = """
CREATE TABLE drawings(id TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL, data TEXT NOT NULL,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE INDEX drawings_symbol ON drawings(symbol);
CREATE TABLE watchlists(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, items TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
CREATE TABLE layouts(name TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO drawings VALUES('d1', 'bybit:BTCUSDT', 'segment', '{"points":[]}', 1, 2);
INSERT INTO watchlists(name, items, position, created_at) VALUES('Crypto', '["bybit:BTCUSDT"]', 1, 1);
INSERT INTO layouts VALUES('last', '{"interval":"1h"}', 3);
INSERT INTO settings VALUES('ui', '{"theme":"dark"}');
"""


def test_migrates_v0_rows_to_local_user(tmp_path):
    path = tmp_path / "app.sqlite"
    con = sqlite3.connect(path)
    con.executescript(V0)
    con.close()
    db = UserDB(path)
    assert db.drawings("local", "bybit:BTCUSDT")[0]["id"] == "d1"
    assert db.watchlists("local")[0]["items"] == ["bybit:BTCUSDT"]
    assert db.layout("local", "last")["state"] == {"interval": "1h"}
    assert db.settings("local") == {"ui": {"theme": "dark"}}
    assert db.drawings("someone", "bybit:BTCUSDT") == []
    db.close()
    UserDB(path).close()  # idempotent on reopen


def test_users_are_isolated(tmp_path):
    db = UserDB(tmp_path / "app.sqlite")
    db.upsert_drawing("alice", "same-id", "bybit:BTCUSDT", "segment", {"v": 1})
    db.upsert_drawing("bob", "same-id", "bybit:BTCUSDT", "segment", {"v": 2})  # same client id must not overwrite
    assert db.drawings("alice", "bybit:BTCUSDT")[0]["v"] == 1
    assert db.drawings("bob", "bybit:BTCUSDT")[0]["v"] == 2
    assert db.delete_drawing("bob", "same-id") == 1
    assert len(db.drawings("alice", "bybit:BTCUSDT")) == 1

    wl = db.create_watchlist("alice", "Mine", ["bybit:ETHUSDT"])
    assert not db.update_watchlist("bob", wl["id"], name="hijack")
    assert not db.delete_watchlist("bob", wl["id"])
    assert db.watchlists("bob") == []
    assert db.watchlists("alice")[0]["name"] == "Mine"

    db.put_layout("alice", "last", {"a": 1})
    db.put_layout("bob", "last", {"b": 2})
    assert db.layout("alice", "last")["state"] == {"a": 1}
    db.put_settings("alice", {"ui": 1})
    assert db.settings("bob") == {}
    assert db.all_watchlist_items() == {"bybit:ETHUSDT"}


# ------------------------------------------------------------------ tokens
URL = "https://example.supabase.co"


class _Req:
    def __init__(self, header):
        self.headers = {"authorization": header} if header else {}
        self.scope = {"state": {"client_ip": "198.51.100.7"}}


def _cache_with(public):
    c = auth._Jwks()
    c.keys = {"k1": public}
    c.fetched_at = time.monotonic()
    refreshes = []

    async def no_network():
        refreshes.append(1)
        c.fetched_at = time.monotonic()
    c._refresh = no_network
    c.refreshes = refreshes
    return c


@pytest.fixture()
def supabase(monkeypatch):
    private = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(config, "AUTH", "supabase")
    monkeypatch.setattr(config, "SUPABASE_URL", URL)
    cache = _cache_with(private.public_key())
    monkeypatch.setattr(auth, "_jwks", cache)
    monkeypatch.setattr(auth, "_fails", None)

    def make(kid="k1", **over):
        now = int(time.time())
        claims = {"sub": "user-1", "aud": "authenticated", "iss": f"{URL}/auth/v1", "exp": now + 600, "iat": now,
                  "role": "authenticated", **over}
        return jwt.encode(claims, private, algorithm="ES256", headers={"kid": kid})
    make.cache = cache
    return make


async def test_local_mode_needs_no_token(monkeypatch):
    monkeypatch.setattr(config, "AUTH", "none")
    assert await auth.user_id(_Req(None)) == "local"


async def test_valid_token(supabase):
    assert await auth.user_id(_Req(f"Bearer {supabase()}")) == "user-1"


@pytest.mark.parametrize("over", [
    {"exp": int(time.time()) - 10},            # expired
    {"aud": "anon"},                           # wrong audience
    {"iss": "https://evil.example/auth/v1"},   # other project
    {"is_anonymous": True},                    # anonymous sign-in
])
async def test_rejected_tokens(supabase, over):
    with pytest.raises(HTTPException) as e:
        await auth.user_id(_Req(f"Bearer {supabase(**over)}"))
    assert e.value.status_code == 401


async def test_missing_or_foreign_token(supabase):
    for header in (None, "Basic abc", "Bearer not-a-jwt"):
        with pytest.raises(HTTPException) as e:
            await auth.user_id(_Req(header))
        assert e.value.status_code == 401
    other = ec.generate_private_key(ec.SECP256R1())
    forged = jwt.encode({"sub": "x", "aud": "authenticated", "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 60},
                        other, algorithm="ES256", headers={"kid": "k1"})
    with pytest.raises(HTTPException):
        await auth.user_id(_Req(f"Bearer {forged}"))


def test_public_config_hides_supabase_when_off(monkeypatch):
    monkeypatch.setattr(config, "AUTH", "none")
    monkeypatch.setattr(config, "I18N", False)
    assert auth.public_config() == {"auth": "none", "supabase_url": None, "supabase_key": None, "i18n": False}
    assert json.dumps(auth.public_config())


async def test_unknown_kids_do_not_hammer_jwks(supabase):
    supabase.cache.fetched_at = time.monotonic() - 120   # last refresh 2 min ago
    for i in range(50):
        with pytest.raises(HTTPException):
            await auth.user_id(_Req(f"Bearer {supabase(kid=f'random-{i}')}"))
    assert len(supabase.cache.refreshes) == 1             # one refresh, then the cooldown holds


async def test_oversized_and_hs256_tokens_rejected(supabase):
    with pytest.raises(HTTPException) as e:
        await auth.user_id(_Req("Bearer " + "a" * (auth.MAX_TOKEN + 1)))
    assert e.value.status_code == 401
    hs = jwt.encode({"sub": "x", "aud": "authenticated", "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 60},
                    "secret", algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(HTTPException):
        await auth.user_id(_Req(f"Bearer {hs}"))


async def test_failed_tokens_are_rate_limited(supabase, monkeypatch):
    monkeypatch.setattr(auth, "_fails", auth.limits.RateLimiter(0.001, 3))
    codes = []
    for _ in range(5):
        with pytest.raises(HTTPException) as e:
            await auth.user_id(_Req("Bearer not.a.jwt"))
        codes.append(e.value.status_code)
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
