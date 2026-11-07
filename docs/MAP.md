# Change map: what a change can touch

FibTrader View has a few features that cut across everything else. Most bugs come from a change that works on its
own but breaks one of them. Before a change, find the area you touch below and walk its "also check" list; before
calling it done, walk the checklist in [AGENTS.md](../AGENTS.md).

## Cross-cutting concerns (every change)

| Concern | Rule | Where |
|---|---|---|
| Language | Every visible string is English source text in `t()`, the Russian translation goes into `ru.ts` in the same change. Server texts that reach users (alert messages, Telegram) carry both languages. | `frontend/src/i18n/`, `backend/app/alerts.py`, `telegram.py` |
| Phone / tablet | Layout switches at 760 px (phone) and 1100 px (tablet) wide; touch screens (`pointer: coarse`) have no hover and need bigger targets; dialogs become bottom sheets on phones. Anything hover-only needs a touch path or must be optional. | `App.tsx` (breakpoints), `index.css`, `chart/theme.ts` |
| Colours | Readable for colour-blind people: strong contrast, no blue as a status colour, never colour alone. Dark and light themes both. | `chart/theme.ts`, `index.css` |
| Abuse limits | Public instance = 1 GB VM. Every endpoint: body size cap, rate limit, per-account quota, bounded work, slow work off the event loop. | `backend/app/limits.py`, `guard.py` |
| History guard | Market series (candles, funding, OI, liquidations) older than the newest 1200 bars go through `history.py`: guests get only the fresh window (plus ±1200 bars around a shared link's moment, if that moment was still fresh when the link was made); accounts pay from a wallet (slow-down, then a 24 h stop), and every network (IPv4 address / IPv6 /64) has its own, 5× larger wallet. Deliberate exception: the two market-wide daily sentiment series (`/api/sentiment`) are public. Every API route is classified in `backend/tests/test_routes.py`. | `backend/app/history.py`, `frontend/src/api/history.ts`, `components/HistoryNotice.tsx` |
| Private vs public | Two instances from one code base: the private one (single local user, all markets, generous limits) and the public one (`LV_AUTH=supabase`, strict limits, Russian/English). Optional plugins may be absent: the app must work without them. | `config.py`, `limits.py`, `plugins/` |
| Saved state | Layouts, drawings, settings and shared links outlive code. Renaming or removing an indicator, drawing tool or setting needs a migration or a filter for old saved data. | `state/store.ts`, `backend/app/userdata/`, `shares.py` |
| Sync across tabs / devices | One account, several tabs and devices: a tab writes only the fields it changed (read, merge, write). Indicators, chart type, theme and time zone are account-wide; symbol, interval and watchlist stay per tab. A new layout field or setting must be put on the right side of that line and marked dirty when it changes. | `state/store.ts` (account sync) |
| Time | Timestamps everywhere are UTC milliseconds of the bar open; weeks and months have calendar edges; the displayed time zone is a user setting and only changes labels. New series must use the same convention. | `backend/app/intervals.py`, `frontend/src/chart/periods.ts` |
| Chart type | Heikin Ashi recalculates the candles the chart shows, so indicators and drawings see HA values, while alerts and the top bar's price use real prices; shared links carry the chart type. | `chart/heikinAshi.ts`, `ChartView.tsx`, `share/ShareView.tsx` |

## Feature areas and how they touch each other

### Candles and markets
- One candle service for all markets: SQLite cache per market, missing bars fetched from the provider; an
  interval the provider does not serve natively is resampled from a base interval (`intervals.py`: 3m/5m/15m/30m
  from 1m, 2h/4h from 1h, 1W/1M from 1D).
- Markets differ in depth, intervals and live data:

| Market | Id prefix | Instance | Live | History depth | Derivatives |
|---|---|---|---|---|---|
| Bybit perpetuals | `bybit:` | both | WebSocket | full (re-fetched on demand; the public cache keeps 1m 30 d, 3m–30m 180 d) | funding, OI, liquidations (recorded for the top coins) |
| Bybit spot | `bybit-spot:` | both | WebSocket | as perpetuals | — |
| Hyperliquid (crypto, stocks, indices, commodities, FX) | `hl:` | both | WebSocket | API: newest 5000 bars; archive keeps 1h/4h/1D for good (+ minutes of HIP-3 markets) | funding, OI (recorded by us), no liquidations |
| CFD (biquote) | `bq:` | both | WebSocket | API: short window; archive keeps 1m…1D from the day we started | — |
| US stocks (Alpaca), MOEX, Yahoo | `us:` `moex:` `yahoo:` | private only | polling | per provider | — |

- **Also check:** a new market or provider → symbol search, live stream or poller (`realtime/hub.py`), alerts feed
  (`alerts.py`: without a feed there are no alerts), history guard, maintenance/retention, backups, the market
  table above, the symbol type (`crypto` / stock / …) that indicators and alerts filter on.

### Indicators
- **Where an indicator works in the UI: `chart/indicators/catalog.ts`.** Every entry of `INDICATOR_CATALOG` must
  declare `where`: data source (`bars` = computed from the chart's bars, `server` = loads its own series), markets,
  symbol types, intervals (also per market), the server feature it needs and its alert metric. The Indicators
  dialog (disabled entries with the reason), the panel of an indicator already on the chart (`extra.ts`) and the
  metrics of the Alerts dialog read it instead of their own checks. It is static UI compatibility, not a promise
  of data: which coins have recorded liquidations, how far Hyperliquid OI goes back and which feeds alerts have are
  decided on the server (`derivs.py`, `alerts.py`), and the panels still show what the server answers. `data: 'bars'`
  does not prove a formula is free of look-ahead. Formulas live in `chart/indicators/index.ts`, data-driven panels
  and drawn-on-the-chart ones in `chart/indicators/extra.ts`; pure calculations that need checking (Stoch RSI,
  MFI, CMF, volume profile, previous day / week / month levels, sessions) in `chart/indicators/calc.ts`, checked
  by `frontend/scripts/test-indicators.mjs` (part of `npm run build` and the public deploy). Before proposing a
  "new" indicator, check the catalogue: it already has e.g. a volume profile (`VPVR`: visible range, recomputed on
  every frame in the browser, so it follows scrolling, replay and Heikin Ashi with no server work) and a fixed
  range one (a drawing tool, `overlays/volumeProfile.ts`).
- Indicators drawn on the price pane with their own `draw` and no figures (VPVR, PDHL, SESSIONS) do not widen the
  price scale. PDHL loads the symbol's daily bars (`/api/bars`, 1D) and takes each bar's levels from the last
  complete day / week / month before it, so a replay sees no later data; SESSIONS works up to 1h, PDHL up to 1D.
- Buy / sell markers of trend indicators (SuperTrend, AlphaTrend) are drawn by `chart/indicators/signals.ts` from
  a `signal` field of the calc result (1 / −1), with a word next to the triangle (not colour alone); the last
  parameter `Signals (0 = off)` hides them. Old layouts without that parameter get the default (shown).
- **Also check:** a new indicator →
  1. its `where` in the catalogue (the compiler insists); a reason text for a new kind of limit goes into
     `unavailable()` there;
  2. bar replay: `calc` must use only the bars it gets (no future data); data indicators must stop at the last
     replay bar (`extra.ts`, "where the last bar ends");
  3. history: data indicators loading their own series follow the history protocol (`slow`, locked, `stopped`)
     and must not mark history as ended when it is only locked;
  4. alerts: a new alert metric needs `alerts.py` `METRICS` (server feed), `AlertMetric` in `api/client.ts`,
     labels in `state/alerts.ts` and `where.alert` in the catalogue;
  5. shared links: `shares.py` keeps only `name`, `calcParams` (≤ 8 numbers in −1000…1000) and `main`; anything
     else (options, extendData) is lost in a share;
  6. saved layouts: names are stored; renaming or removing needs a filter (`liveIndicators()` in `store.ts`);
  7. phone: legend and settings fit a narrow screen; the panel is usable by touch;
  8. both themes and colour-blind readable colours; `t()` for the label, parameter names and messages.

### Bar replay
- Browser-only (`chart/replay.ts`, `components/ReplayBar.tsx`): the chart shows history up to a moment and reveals
  the next bars; no live subscription while it runs.
- Touches: the chart data loader (`ChartView.tsx`), every indicator (no future data), the history guard (a replay
  start in closed history exits with a message), the price in the top bar, the position tool's result
  (`overlays/position.ts`), symbol/interval switching (the replay keeps its moment).
- **Also check:** anything that reads "now", the live feed or the newest bar must behave during a replay.

### Alerts and Telegram
- Server-side checks every few seconds over per-market feeds (`alerts.py`); delivery in the site and through the
  Telegram bot (`telegram.py`). Metrics: price (level or drawn line), funding and OI (perpetuals), Fear & Greed and
  influencer pressure (market-wide).
- Touches: drawings (line alerts follow a drawn line), indicators (alert lines on the chart: `alertLines.ts`),
  markets (feeds), limits (alerts per account), language (message text in both languages), phone (the on-chart
  alert button is mouse-only; the dialog is the touch path).

### Shared links
- `shares.py` stores a cleaned snapshot (symbol, interval, chart type, theme, moment, indicators, drawings);
  `share/ShareView.tsx` opens it read-only, also for guests.
- Touches: the history guard (guests get ±1200 bars around the shared moment, but only if the moment was still in
  the fresh window when the link was made: a link cannot open old history; a moment older than 500 bars opens as a
  still snapshot without live data), indicators and drawings (only fields `clean_snapshot` keeps), language, phone.
- **Also check:** a new drawing tool or indicator field that should survive a share must be added to
  `clean_snapshot` with type and range checks (other people open the link).

### Drawings
- Overlays in `chart/overlays/` (fib, measure, position, …), stored per symbol in user data.
- Touches: shares (snapshot), alerts (line alerts), replay (position result), phone (touch dragging, toolbar width),
  themes, saved state (renames).

### Accounts and user data
- Public: Supabase sign-in (email code, Google), token checked locally (`auth.py`); private: one local user.
- Watchlists, layouts, drawings, settings, alerts, shares are scoped by user id (`userdata/`), with per-account
  quotas (`limits.py`). Guests on the public site can open shared links and the newest screens only.

### Data jobs and storage
- Archives (`hl_archive.py`, `bq_archive.py`), liquidation recorder (`derivs.py`), sentiment (`sentiment.py`),
  cache upkeep (`maintenance.py`), backups (`ops/server/fv-backup-r2`), history-guard state
  (`data/history.sqlite`).
- **Also check:** new stored data → retention, disk guard, backup or "can be fetched again", the memory budget of
  the public VM.

### Feedback
- The feedback form becomes public GitHub issues (`feedback.py`). Issue text is untrusted input: see AGENTS.md.
