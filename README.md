# FibTrader View

Free charts of crypto, stocks, indices and commodities with drawing tools — live at
**[charts.fibtrader.dev](https://charts.fibtrader.dev)**.

- Bybit perpetuals and spot, candles from 1 minute to 1 month, live updates.
- Stocks, indices, commodities and FX as Hyperliquid perpetuals (trade.xyz and other HIP-3 exchanges): they trade
  around the clock, so prices can differ from the exchange price. Hyperliquid serves only its newest 5000 bars, so
  the server keeps 1h / 4h / 1D history of every market as it goes.
- 30+ drawing tools (trend lines, Fibonacci, long/short positions, channels, waves, annotations…), saved to your
  account and shown on every interval and device.
- **No limit on indicators**: moving averages, Bollinger, Ichimoku, SuperTrend, RSI, MACD, volume profile and more.
- Perpetual panels: funding rate, open interest, liquidations (Bybit).
- Price and trend-line **alerts in Telegram**.
- Compare symbols, watchlists, saved layouts, share a chart as an image or a read-only link.
- English and Russian.

Ideas and bugs: use **Suggest a feature** in the account menu on the site (it opens an issue here), or open an issue.

## Stack

- Chart engine: [KLineChart v10](https://klinecharts.com) plus drawing tools ported from KLineChart Pro (Apache-2.0).
- Backend: FastAPI (Python ≥ 3.12). Candles are cached in SQLite per market; user data (drawings, watchlists,
  layouts, alerts) in another SQLite file. One process serves the REST API, the WebSocket and the built frontend.
- Frontend: Vite, React 19, TypeScript, Tailwind 4, zustand.

## Run it yourself

```bash
python3 -m venv venv && ./venv/bin/pip install -r backend/requirements.txt
cp .env.example .env
./venv/bin/uvicorn app.main:app --app-dir backend --port 8100 --reload
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxies /api and /ws to :8100)
```

Without sign-in (`LV_AUTH=none`) the app runs for a single local user. A public setup uses
`LV_AUTH=supabase` (Supabase Auth: email code and Google), `LV_MARKETS=bybit,bybit-spot,hl` and the stricter limits
in `backend/app/limits.py`. Optional: `TELEGRAM_BOT_TOKEN` for alerts, a GitHub App for the feedback form
(`GITHUB_APP_*`, see `backend/app/config.py`).

Tests: `cd backend && ../venv/bin/python -m pytest -q`; type check and build: `cd frontend && npx tsc -b && npm run build`.

Notes for AI coding agents: [AGENTS.md](AGENTS.md).
