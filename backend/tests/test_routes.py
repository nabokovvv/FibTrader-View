"""Every API route is classified, and each class keeps its promise (docs/MAP.md, AGENTS.md checklist).

A new route fails `test_every_route_is_classified` until it is added to ROUTES below. That is the point: adding it
means deciding whether it returns a time series (then it must go through the history guard, app/history.py),
belongs to an account (then it needs a signed-in user) or is public (then say why that is safe).
"""
from __future__ import annotations

import importlib
import inspect

from fastapi.routing import APIRoute, APIWebSocketRoute

from app import auth, main

SERIES = "series"      # returns history of a time series: must call history.gate, and the second field names the
                       # test in tests/test_history.py that shows a guest cannot get older history through it
ACCOUNT = "account"    # the caller's own data: must require a signed-in user (auth.user_id)
PUBLIC = "public"      # open to guests: the reason says why it cannot be used to pull history

ROUTES: dict[tuple[str, str], tuple[str, str]] = {
    # ---------------------------------------------------------------- time series
    ("GET", "/api/bars"): (SERIES, "test_guests_get_the_fresh_window_only"),
    ("GET", "/api/derivs/{kind}"): (SERIES, "test_liquidation_history_is_guarded_like_candles"),
    # ---------------------------------------------------------------- public
    ("GET", "/api/health"): (PUBLIC, "status only"),
    ("GET", "/api/config"): (PUBLIC, "sign-in settings for the browser"),
    ("GET", "/api/markets"): (PUBLIC, "list of markets"),
    ("GET", "/api/symbols/search"): (PUBLIC, "symbol metadata, no prices over time"),
    ("GET", "/api/symbols"): (PUBLIC, "symbol metadata and the latest quote"),
    ("GET", "/api/symbol"): (PUBLIC, "symbol metadata"),
    ("GET", "/api/stats"): (PUBLIC, "404 on the public instance"),
    ("GET", "/api/sentiment/{kind}"): (PUBLIC, "two market-wide daily series of a few thousand points, the same for "
                                               "every symbol; not market data"),
    ("GET", "/api/shares/{sid}"): (PUBLIC, "a chart snapshot; its candles still go through /api/bars"),
    ("GET", "/api/realtime/status"): (PUBLIC, "connection status"),
    ("WS", "/ws"): (PUBLIC, "live bars and quotes only, no history"),
    ("GET", "/{path:path}"): (PUBLIC, "the web app's files"),
    # ---------------------------------------------------------------- the caller's own data
    ("GET", "/api/drawings"): (ACCOUNT, ""),
    ("PUT", "/api/drawings/{drawing_id}"): (ACCOUNT, ""),
    ("PUT", "/api/drawings"): (ACCOUNT, ""),
    ("DELETE", "/api/drawings/{drawing_id}"): (ACCOUNT, ""),
    ("DELETE", "/api/drawings"): (ACCOUNT, ""),
    ("GET", "/api/watchlists"): (ACCOUNT, ""),
    ("POST", "/api/watchlists"): (ACCOUNT, ""),
    ("PUT", "/api/watchlists/{wid}"): (ACCOUNT, ""),
    ("DELETE", "/api/watchlists/{wid}"): (ACCOUNT, ""),
    ("GET", "/api/layouts"): (ACCOUNT, ""),
    ("GET", "/api/layouts/{name}"): (ACCOUNT, ""),
    ("PUT", "/api/layouts/{name}"): (ACCOUNT, ""),
    ("DELETE", "/api/layouts/{name}"): (ACCOUNT, ""),
    ("GET", "/api/settings"): (ACCOUNT, ""),
    ("PUT", "/api/settings"): (ACCOUNT, ""),
    ("GET", "/api/alerts"): (ACCOUNT, ""),
    ("POST", "/api/alerts"): (ACCOUNT, ""),
    ("PATCH", "/api/alerts/{aid}"): (ACCOUNT, ""),
    ("DELETE", "/api/alerts/{aid}"): (ACCOUNT, ""),
    ("GET", "/api/telegram"): (ACCOUNT, ""),
    ("POST", "/api/telegram/link"): (ACCOUNT, ""),
    ("DELETE", "/api/telegram"): (ACCOUNT, ""),
    ("POST", "/api/feedback"): (ACCOUNT, ""),
    ("POST", "/api/shares"): (ACCOUNT, ""),
    ("DELETE", "/api/shares/{sid}"): (ACCOUNT, ""),
}


def _walk(routes, prefix=""):
    for r in routes:
        inc = getattr(r, "include_context", None)          # FastAPI ≥ 0.160 wraps included routers
        if inc is not None:
            yield from _walk(inc.included_router.routes, prefix + inc.prefix)
        elif isinstance(r, APIWebSocketRoute):
            yield "WS", prefix + r.path, r
        elif isinstance(r, APIRoute):
            for m in r.methods:
                yield m, prefix + r.path, r


def _routes():
    # optional plugins are private extensions with their own rules; the public build ships without them
    return {(m, p): r for m, p, r in _walk(main.app.routes) if not r.endpoint.__module__.startswith("app.plugins.")}


def _depends_on(route, fn) -> bool:
    stack = list(route.dependant.dependencies)
    while stack:
        d = stack.pop()
        if d.call is fn:
            return True
        stack.extend(d.dependencies)
    return False


def test_every_route_is_classified():
    found = set(_routes())
    new = sorted(found - set(ROUTES))
    gone = sorted(set(ROUTES) - found)
    assert not new, (f"new route(s) {new}: add them to ROUTES in tests/test_routes.py — does it return history "
                     f"(history guard), the caller's data (sign-in) or something public (why is it safe)? "
                     f"See docs/MAP.md.")
    assert not gone, f"route(s) {gone} no longer exist: remove them from ROUTES"


def test_series_routes_go_through_the_history_guard():
    """A cheap structural signal only (a branch returning before the gate would still pass): the behaviour is
    proved by the test each series route names, which must exist."""
    behaviour = importlib.import_module("tests.test_history")
    for key, route in _routes().items():
        kind, test = ROUTES[key]
        if kind != SERIES:
            continue
        src = inspect.getsource(route.endpoint)
        assert "history.gate(" in src and "history.finish(" in src, f"{key} returns history without the guard"
        assert callable(getattr(behaviour, test, None)), \
            f"{key}: write a test in tests/test_history.py showing a guest cannot get older history, and name it here"


def test_account_routes_need_a_signed_in_user():
    for key, route in _routes().items():
        if ROUTES[key][0] == ACCOUNT:
            assert _depends_on(route, auth.user_id), f"{key} is account data but does not require sign-in"


def test_public_routes_say_why():
    for key, (kind, why) in ROUTES.items():
        if kind == PUBLIC:
            assert why, f"{key}: public routes need a reason"
