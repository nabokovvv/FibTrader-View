"""Runtime configuration (env + .env at the project root)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("LV_DATA_DIR", ROOT / "data"))
CANDLES_DIR = DATA_DIR / "candles"
SYMBOLS_CACHE_DIR = DATA_DIR / "symbols"
BACKUPS_DIR = DATA_DIR / "backups"
LOGS_DIR = DATA_DIR / "logs"
APP_DB = DATA_DIR / "app.sqlite"
FRONTEND_DIST = ROOT / "frontend" / "dist"

HOST = os.getenv("LV_HOST", "0.0.0.0")
PORT = int(os.getenv("LV_PORT", "8100"))

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")
# "iex" is real-time on the free plan; "sip" is full-market but must lag >= 15 min.
ALPACA_FEED = os.getenv("ALPACA_FEED", "iex")

DEFAULT_TIMEZONE = os.getenv("LV_TIMEZONE", "Europe/Nicosia")

# Markets to serve, comma separated; empty = every market that is configured.
# The public instance runs with LV_MARKETS=bybit,bybit-spot (stock/index data may not be redistributed).
MARKETS = [m.strip() for m in os.getenv("LV_MARKETS", "").split(",") if m.strip()]

# Auth: "none" = private instance, every request is the single local user;
#       "supabase" = public instance, requests carry a Supabase Auth access token (Authorization: Bearer).
AUTH = os.getenv("LV_AUTH", "none").strip().lower()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
# publishable (browser-safe) key, handed to the frontend via /api/config
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")

# Russian / English UI with a language switch (default: on for the public instance only)
I18N = os.getenv("LV_I18N", "1" if AUTH == "supabase" else "0").strip() in ("1", "true", "yes")

# Candle-cache upkeep (retention, disk guard, idle prewarm): default on for the public instance only
MAINTENANCE = os.getenv("LV_MAINTENANCE", "1" if AUTH == "supabase" else "0").strip() in ("1", "true", "yes")

# Force the public abuse-limit profile without Supabase auth (local testing only)
PROFILE = os.getenv("LV_PROFILE", "").strip().lower()

# Polling cadence (seconds) for providers without a push stream.
POLL_BARS_S = {"us": 5, "moex": 5, "yahoo": 30}
POLL_QUOTES_S = {"us": 10, "moex": 10, "yahoo": 60}


def market_enabled(market: str) -> bool:
    return not MARKETS or market in MARKETS


def has_alpaca() -> bool:
    return bool(ALPACA_API_KEY and ALPACA_API_SECRET)


def ensure_dirs() -> None:
    for d in (DATA_DIR, CANDLES_DIR, SYMBOLS_CACHE_DIR, BACKUPS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)

# Public address of the site (links in Telegram alerts), e.g. https://charts.fibtrader.dev
PUBLIC_URL = os.getenv("LV_PUBLIC_URL", "").rstrip("/")
# Telegram alerts bot (one instance per bot token: long polling is exclusive)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()  # gets a ping on new feedback
# Feedback → GitHub issues through a GitHub App (Issues: write on one repo)
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "").strip()
GITHUB_APP_INSTALLATION_ID = os.getenv("GITHUB_APP_INSTALLATION_ID", "").strip()
GITHUB_APP_KEY_FILE = os.getenv("GITHUB_APP_KEY_FILE", "").strip()   # PEM private key of the App
GITHUB_FEEDBACK_REPO = os.getenv("GITHUB_FEEDBACK_REPO", "").strip()  # owner/name
# Liquidations recorder (Bybit WS, top coins) — on by default where Bybit perps are enabled
LIQUIDATIONS = os.getenv("LV_LIQUIDATIONS", "1").strip() in ("1", "true", "yes")
