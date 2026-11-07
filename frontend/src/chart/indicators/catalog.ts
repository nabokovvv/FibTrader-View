import type { AlertMetric } from '../../api/client'
import { serverFeatures } from '../../auth/session'
import { t } from '../../i18n'
import { INTERVALS, MARKET_LABEL, type Interval, type SymbolInfo } from '../../lib/types'
import { PLUGIN_INDICATORS } from '../../plugins'

/* ------------------------------------------------------------------------------------------------------------
 * The indicator catalogue: what the Indicators dialog offers, and for every indicator WHERE it works.
 * `where` is required, so a new indicator cannot be added without deciding its markets, symbol types, intervals,
 * data source and alert. Everything that asks "does this indicator work here?" reads it from here:
 *  - the Indicators dialog (disabled entries with the reason),
 *  - the panel of an indicator already on the chart when the symbol or interval changes (extra.ts),
 *  - the metrics offered in the Alerts dialog.
 * docs/MAP.md lists what else a new indicator has to check (bar replay, history guard, shared links, phone).
 * ---------------------------------------------------------------------------------------------------------- */

export interface Where {
  /**
   * Where the values come from:
   *  - 'bars': computed from the chart's own bars (works on every market; a bar replay is safe by construction);
   *  - 'server': loads its own series (must follow the history protocol of api/history.ts and stop at the last
   *    bar of a replay — see extra.ts).
   */
  data: 'bars' | 'server'
  /** Markets that have the data (market ids: 'bybit', 'hl', …); missing = every market. */
  markets?: readonly string[]
  /** Symbol types ('crypto', 'stock', …); missing = every type. */
  types?: readonly string[]
  /** Chart intervals it works on: one list, or a list per market (markets not named: every interval). */
  intervals?: readonly Interval[] | Readonly<Record<string, readonly Interval[]>>
  /** Server feature it needs (hidden from the dialog when the server does not offer it). */
  feature?: 'derivs'
  /** The alert metric that watches this indicator's value, if any. */
  alert?: AlertMetric
}

export interface IndicatorMeta {
  name: string
  label: string
  main: boolean
  params?: number[]
  paramNames?: string[]
  precision?: number
  group: 'Trend' | 'Momentum' | 'Volatility' | 'Volume' | 'Levels' | 'Crypto' | 'Sentiment'
  /** Non-numeric settings (plugin indicators). Shared links keep only numeric parameters. */
  options?: Record<string, unknown>
  where: Where
}

const BARS: Where = { data: 'bars' }
const PERPS = ['bybit', 'hl'] as const
const FROM = (iv: Interval) => INTERVALS.slice(INTERVALS.indexOf(iv))
const UPTO = (iv: Interval) => INTERVALS.slice(0, INTERVALS.indexOf(iv) + 1)
const FUNDING_WHERE: Where = { data: 'server', markets: PERPS, feature: 'derivs', alert: 'funding' }
// Bybit has no open-interest history under 5 minutes; Hyperliquid's is recorded by us and fits any interval
const OI_WHERE: Where = { data: 'server', markets: PERPS, intervals: { bybit: FROM('5m') }, feature: 'derivs', alert: 'oi' }
// Hyperliquid does not publish liquidations
const LIQ_WHERE: Where = { data: 'server', markets: ['bybit'], feature: 'derivs' }
// one daily series for the whole crypto market
const SENTIMENT: Where = { data: 'server', types: ['crypto'], intervals: FROM('1D') }
// previous day / week / month levels come from the symbol's daily bars; on weekly and monthly charts they say nothing
const PERIOD_LEVELS: Where = { data: 'server', intervals: UPTO('1D') }
// session boxes need bars shorter than a session
const SESSIONS_WHERE: Where = { data: 'bars', intervals: UPTO('1h') }

export const INDICATOR_CATALOG: IndicatorMeta[] = [
  ...PLUGIN_INDICATORS.map((p) => p.meta),
  { name: 'MA', label: 'Moving Average', main: true, params: [20, 50, 200], group: 'Trend', where: BARS },
  { name: 'EMA', label: 'Exponential MA', main: true, params: [9, 21, 55], group: 'Trend', where: BARS },
  { name: 'SMA', label: 'Smoothed MA', main: true, params: [12, 2], group: 'Trend', where: BARS },
  { name: 'HMA', label: 'Hull MA', main: true, params: [9], group: 'Trend', where: BARS },
  { name: 'BOLL', label: 'Bollinger Bands', main: true, params: [20, 2], group: 'Volatility', where: BARS },
  { name: 'KELTNER', label: 'Keltner Channels', main: true, params: [20, 10, 2], group: 'Volatility', where: BARS },
  { name: 'DONCHIAN', label: 'Donchian Channels', main: true, params: [20], group: 'Volatility', where: BARS },
  { name: 'SUPERTREND', label: 'SuperTrend', main: true, params: [10, 3, 1], paramNames: ['ATR length', 'Multiplier', 'Signals (0 = off)'], group: 'Trend', where: BARS },
  { name: 'ALPHATREND', label: 'AlphaTrend', main: true, params: [1, 14, 1], paramNames: ['Multiplier', 'Length', 'Signals (0 = off)'], group: 'Trend', where: BARS },
  { name: 'ICHIMOKU', label: 'Ichimoku Cloud', main: true, params: [9, 26, 52, 26], group: 'Trend', where: BARS },
  { name: 'SAR', label: 'Parabolic SAR', main: true, params: [2, 2, 20], group: 'Trend', where: BARS },
  { name: 'VWAP', label: 'VWAP (session)', main: true, group: 'Volume', where: BARS },
  { name: 'VPVR', label: 'Volume profile (visible range)', main: true, params: [24], paramNames: ['Rows'], group: 'Volume', where: BARS },
  { name: 'MFI', label: 'Money Flow Index', main: false, params: [14, 80, 20], paramNames: ['Length', 'Upper', 'Lower'], precision: 2, group: 'Volume', where: BARS },
  { name: 'CMF', label: 'Chaikin Money Flow', main: false, params: [20], paramNames: ['Length'], precision: 3, group: 'Volume', where: BARS },
  { name: 'FUNDING', label: 'Funding rate', main: false, group: 'Crypto', where: FUNDING_WHERE },
  { name: 'OI', label: 'Open interest', main: false, group: 'Crypto', where: OI_WHERE },
  { name: 'LIQ', label: 'Liquidations', main: false, group: 'Crypto', where: LIQ_WHERE },
  { name: 'FNG', label: 'Fear & Greed Index (crypto, 1D)', main: false, params: [75, 25], paramNames: ['Upper', 'Lower'], group: 'Sentiment', where: { ...SENTIMENT, alert: 'fng' } },
  { name: 'YTS', label: 'Influencer pressure (YouTube, 1D)', main: false, group: 'Sentiment', where: { ...SENTIMENT, alert: 'yt' } },
  { name: 'PIVOTS', label: 'Pivot Points (classic)', main: true, group: 'Levels', where: BARS },
  { name: 'PDHL', label: 'Previous day / week / month high & low', main: true, params: [1, 1, 1], paramNames: ['Day (0 = off)', 'Week (0 = off)', 'Month (0 = off)'], group: 'Levels', where: PERIOD_LEVELS },
  { name: 'SESSIONS', label: 'Trading sessions (Tokyo, London, New York)', main: true, params: [9, 15.5, 8, 16.5, 9.5, 16],
    paramNames: ['Tokyo from, h', 'Tokyo to, h', 'London from, h', 'London to, h', 'New York from, h', 'New York to, h'], group: 'Levels', where: SESSIONS_WHERE },
  { name: 'BBI', label: 'Bull & Bear Index', main: true, params: [3, 6, 12, 24], group: 'Trend', where: BARS },
  { name: 'VOL', label: 'Volume', main: false, params: [5, 10, 20], group: 'Volume', where: BARS },
  { name: 'MACD', label: 'MACD', main: false, params: [12, 26, 9], precision: 2, group: 'Momentum', where: BARS },
  { name: 'RSI', label: 'RSI', main: false, params: [14, 70, 30], paramNames: ['Length', 'Upper', 'Lower'], precision: 2, group: 'Momentum', where: BARS },
  { name: 'STOCH', label: 'Stochastic', main: false, params: [14, 3, 3, 80, 20], paramNames: ['Length', 'Smooth K', 'Smooth D', 'Upper', 'Lower'], precision: 2, group: 'Momentum', where: BARS },
  { name: 'STOCHRSI', label: 'Stochastic RSI', main: false, params: [14, 14, 3, 3, 80, 20], paramNames: ['RSI length', 'Stoch length', 'Smooth K', 'Smooth D', 'Upper', 'Lower'], precision: 2, group: 'Momentum', where: BARS },
  { name: 'KDJ', label: 'KDJ', main: false, params: [9, 3, 3, 80, 20], paramNames: ['Length', 'Smooth K', 'Smooth D', 'Upper', 'Lower'], precision: 2, group: 'Momentum', where: BARS },
  { name: 'CCI', label: 'CCI', main: false, params: [20, 100, -100], paramNames: ['Length', 'Upper', 'Lower'], precision: 2, group: 'Momentum', where: BARS },
  { name: 'WR', label: 'Williams %R', main: false, params: [14, -20, -80], paramNames: ['Length', 'Upper', 'Lower'], precision: 2, group: 'Momentum', where: BARS },
  { name: 'DMI', label: 'DMI / ADX', main: false, params: [14, 6, 25], paramNames: ['Length', 'ADX', 'Level'], precision: 2, group: 'Trend', where: BARS },
  { name: 'ATR', label: 'ATR', main: false, params: [14], group: 'Volatility', where: BARS },
  { name: 'OBV', label: 'On Balance Volume', main: false, params: [30], precision: 0, group: 'Volume', where: BARS },
  { name: 'MTM', label: 'Momentum', main: false, params: [12, 6], precision: 2, group: 'Momentum', where: BARS },
  { name: 'ROC', label: 'Rate of Change', main: false, params: [12, 6], precision: 2, group: 'Momentum', where: BARS },
  { name: 'TRIX', label: 'TRIX', main: false, params: [12, 9], precision: 3, group: 'Momentum', where: BARS },
  { name: 'AO', label: 'Awesome Oscillator', main: false, params: [5, 34], precision: 2, group: 'Momentum', where: BARS },
  { name: 'PSY', label: 'Psychological Line', main: false, params: [12, 6], precision: 2, group: 'Momentum', where: BARS },
  { name: 'EMV', label: 'Ease of Movement', main: false, params: [14, 9], precision: 4, group: 'Volume', where: BARS },
  { name: 'PVT', label: 'Price Volume Trend', main: false, precision: 0, group: 'Volume', where: BARS },
  { name: 'VR', label: 'Volume Ratio', main: false, params: [26, 6], precision: 2, group: 'Volume', where: BARS },
  { name: 'BIAS', label: 'Bias', main: false, params: [6, 12, 24], precision: 2, group: 'Momentum', where: BARS },
  { name: 'DMA', label: 'DMA', main: false, params: [10, 50, 10], precision: 2, group: 'Trend', where: BARS },
  { name: 'CR', label: 'CR', main: false, params: [26, 10, 20, 40, 60], precision: 2, group: 'Momentum', where: BARS },
]

export const indicatorMeta = (name: string) => INDICATOR_CATALOG.find((m) => m.name === name)

/** Offered by this server at all (indicators needing a server feature it does not have are not listed). */
export const offered = (m: IndicatorMeta) => !m.where.feature || serverFeatures()[m.where.feature]

function intervalsFor(w: Where, market: string): readonly Interval[] | undefined {
  if (!w.intervals) return undefined
  return Array.isArray(w.intervals) ? w.intervals as readonly Interval[] : (w.intervals as Record<string, readonly Interval[]>)[market]
}

const ivLabel = (iv: Interval) => t(iv)   // '5m' → '5м', '1D' → '1Д'

/** Why the indicator cannot work on this symbol (translated, for the dialog and the panel), or null when it can.
 *  `interval` null: only the symbol is checked (alerts do not depend on the chart's interval). */
export function unavailable(m: IndicatorMeta | undefined, symbol: SymbolInfo | null, interval: Interval | null): string | null {
  if (!m) return null
  const w = m.where
  // before the symbol is known, an indicator with limits cannot be judged yet: not offered until then
  if (!symbol) return w.markets || w.types || w.intervals ? t('Loading the symbol…') : null
  if (w.markets && !w.markets.includes(symbol.market))
    return t('Available only for {markets}.', { markets: w.markets.map((x) => t(MARKET_LABEL[x] ?? x)).join(', ') })
  if (w.types && !w.types.includes(symbol.type))
    return w.types.length === 1 && w.types[0] === 'crypto'
      ? t('Crypto market indicator: not shown for stocks, ETFs, commodities and forex.')
      : t('Not available for this symbol.')
  const ivs = interval && intervalsFor(w, symbol.market)
  if (ivs && !ivs.includes(interval!)) {
    const tail = INTERVALS.slice(INTERVALS.length - ivs.length)
    const head = INTERVALS.slice(0, ivs.length)
    if (tail.every((x, i) => x === ivs[i])) return t('Available from the {interval} interval up.', { interval: ivLabel(ivs[0]) })
    if (head.every((x, i) => x === ivs[i])) return t('Available up to the {interval} interval.', { interval: ivLabel(ivs[ivs.length - 1]) })
    return t('Available on {intervals}.', { intervals: ivs.map(ivLabel).join(', ') })
  }
  return null
}

/** Alert metrics that can watch this symbol: price always, the others where their indicator works. */
export function alertAvailable(metric: AlertMetric, symbol: SymbolInfo | null): boolean {
  if (metric === 'price') return true
  const m = INDICATOR_CATALOG.find((x) => x.where.alert === metric)
  return !!m && !!symbol && unavailable(m, symbol, null) === null
}
