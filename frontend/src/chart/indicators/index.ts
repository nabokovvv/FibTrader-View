import { registerIndicator, type IndicatorTemplate, type KLineData } from 'klinecharts'
import { t as tr } from '../../i18n'
import { PLUGIN_INDICATORS } from '../../plugins'
import { COMPARE } from './compare'
import { FUNDING, LIQ, OI, VPVR } from './extra'
import { ALERT_LINES_TEMPLATE } from './alertLines'
import { RSI_COLOR } from './levels'

/**
 * Custom indicators missing from KLineChart's built-in set. All `calc` functions return one record per
 * bar (aligned with dataList); missing values are left undefined so the line simply starts later.
 */

type Rec = Record<string, number | undefined>

const SESSION_GAP_MS = 4 * 3600_000

function ema(values: Array<number | undefined>, n: number): Array<number | undefined> {
  const k = 2 / (n + 1)
  const out: Array<number | undefined> = new Array(values.length)
  let prev: number | undefined
  let seed: number[] = []
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v === undefined) { out[i] = undefined; continue }
    if (prev === undefined) {
      seed.push(v)
      if (seed.length === n) { prev = seed.reduce((a, b) => a + b, 0) / n; out[i] = prev; seed = [] }
      continue
    }
    prev = v * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}

function wma(values: Array<number | undefined>, n: number): Array<number | undefined> {
  const out: Array<number | undefined> = new Array(values.length)
  const denom = (n * (n + 1)) / 2
  for (let i = n - 1; i < values.length; i++) {
    let s = 0
    let ok = true
    for (let j = 0; j < n; j++) {
      const v = values[i - j]
      if (v === undefined) { ok = false; break }
      s += v * (n - j)
    }
    out[i] = ok ? s / denom : undefined
  }
  return out
}

function trueRange(d: KLineData[], i: number): number {
  const c = d[i]
  if (i === 0) return c.high - c.low
  const pc = d[i - 1].close
  return Math.max(c.high - c.low, Math.abs(c.high - pc), Math.abs(c.low - pc))
}

function atrSeries(d: KLineData[], n: number): Array<number | undefined> {
  // Wilder's smoothing
  const out: Array<number | undefined> = new Array(d.length)
  let prev: number | undefined
  let acc = 0
  for (let i = 0; i < d.length; i++) {
    const tr = trueRange(d, i)
    if (i < n) { acc += tr; if (i === n - 1) { prev = acc / n; out[i] = prev }; continue }
    prev = ((prev as number) * (n - 1) + tr) / n
    out[i] = prev
  }
  return out
}

function highest(d: KLineData[], i: number, n: number, key: 'high' | 'low' = 'high'): number {
  let m = -Infinity
  for (let j = Math.max(0, i - n + 1); j <= i; j++) m = Math.max(m, d[j][key])
  return m
}
function lowest(d: KLineData[], i: number, n: number, key: 'high' | 'low' = 'low'): number {
  let m = Infinity
  for (let j = Math.max(0, i - n + 1); j <= i; j++) m = Math.min(m, d[j][key])
  return m
}

/** New trading session when the UTC date changes or there is a long gap (overnight / weekend). */
function newSession(d: KLineData[], i: number): boolean {
  if (i === 0) return true
  const a = d[i - 1].timestamp
  const b = d[i].timestamp
  if (b - a >= SESSION_GAP_MS) return true
  return Math.floor(a / 86_400_000) !== Math.floor(b / 86_400_000)
}

// ------------------------------------------------------------------------------------------------
const ATR: IndicatorTemplate<Rec, number> = {
  name: 'ATR', shortName: 'ATR', series: 'price', calcParams: [14],
  figures: [{ key: 'atr', title: 'ATR: ', type: 'line' }],
  calc: (d, ind) => atrSeries(d, ind.calcParams[0]).map((atr) => ({ atr })),
}

const VWAP: IndicatorTemplate<Rec, number> = {
  name: 'VWAP', shortName: 'VWAP', series: 'price', calcParams: [],
  figures: [{ key: 'vwap', title: 'VWAP: ', type: 'line' }],
  calc: (d) => {
    let pv = 0
    let vol = 0
    return d.map((c, i) => {
      if (newSession(d, i)) { pv = 0; vol = 0 }
      const tp = (c.high + c.low + c.close) / 3
      const v = c.volume ?? 0
      pv += tp * v
      vol += v
      return { vwap: vol > 0 ? pv / vol : tp }
    })
  },
}

const SUPERTREND: IndicatorTemplate<Rec, number> = {
  name: 'SUPERTREND', shortName: 'SuperTrend', series: 'price', calcParams: [10, 3],
  figures: [
    { key: 'up', title: 'Up: ', type: 'line', styles: () => ({ color: '#089981' }) },
    { key: 'dn', title: 'Down: ', type: 'line', styles: () => ({ color: '#f23645' }) },
  ],
  calc: (d, ind) => {
    const [n, mult] = ind.calcParams
    const atr = atrSeries(d, n)
    const out: Rec[] = new Array(d.length)
    let upper = 0, lower = 0, dir = 1, st: number | undefined
    for (let i = 0; i < d.length; i++) {
      const a = atr[i]
      if (a === undefined) { out[i] = {}; continue }
      const hl2 = (d[i].high + d[i].low) / 2
      let bu = hl2 + mult * a
      let bl = hl2 - mult * a
      const pc = i > 0 ? d[i - 1].close : d[i].close
      if (i > 0 && st !== undefined) {
        bl = bl > lower || pc < lower ? bl : lower
        bu = bu < upper || pc > upper ? bu : upper
        if (dir > 0 && d[i].close < lower) dir = -1
        else if (dir < 0 && d[i].close > upper) dir = 1
      }
      // after a flip, the band is re-anchored
      if (st !== undefined && dir > 0 && d[i].close > upper && out[i - 1]?.dir === -1) bl = hl2 - mult * a
      if (st !== undefined && dir < 0 && d[i].close < lower && out[i - 1]?.dir === 1) bu = hl2 + mult * a
      upper = bu
      lower = bl
      st = dir > 0 ? lower : upper
      out[i] = { up: dir > 0 ? st : undefined, dn: dir < 0 ? st : undefined, dir }
    }
    return out
  },
}

const STOCH: IndicatorTemplate<Rec, number> = {
  name: 'STOCH', shortName: 'Stoch', calcParams: [14, 3, 3, 80, 20], precision: 2,
  figures: [
    { key: 'k', title: '%K: ', type: 'line' },
    { key: 'd', title: '%D: ', type: 'line' },
  ],
  calc: (d, ind) => {
    const [n, smoothK, smoothD] = ind.calcParams
    const raw: Array<number | undefined> = d.map((c, i) => {
      if (i < n - 1) return undefined
      const hh = highest(d, i, n)
      const ll = lowest(d, i, n)
      return hh === ll ? 50 : ((c.close - ll) / (hh - ll)) * 100
    })
    const sma = (v: Array<number | undefined>, m: number) => v.map((_, i) => {
      if (i < m - 1) return undefined
      let s = 0
      for (let j = 0; j < m; j++) { const x = v[i - j]; if (x === undefined) return undefined; s += x }
      return s / m
    })
    const k = sma(raw, smoothK)
    const dd = sma(k, smoothD)
    return d.map((_, i) => ({ k: k[i], d: dd[i] }))
  },
}

const ICHIMOKU: IndicatorTemplate<Rec, number> = {
  name: 'ICHIMOKU', shortName: 'Ichimoku', series: 'price', calcParams: [9, 26, 52, 26],
  figures: [
    { key: 'tenkan', title: 'Tenkan: ', type: 'line' },
    { key: 'kijun', title: 'Kijun: ', type: 'line' },
    { key: 'spanA', title: 'Span A: ', type: 'line' },
    { key: 'spanB', title: 'Span B: ', type: 'line' },
    { key: 'chikou', title: 'Chikou: ', type: 'line' },
  ],
  calc: (d, ind) => {
    const [t, k, s, disp] = ind.calcParams
    const mid = (i: number, n: number) => (highest(d, i, n) + lowest(d, i, n)) / 2
    const tenkan = d.map((_, i) => (i >= t - 1 ? mid(i, t) : undefined))
    const kijun = d.map((_, i) => (i >= k - 1 ? mid(i, k) : undefined))
    const spanBraw = d.map((_, i) => (i >= s - 1 ? mid(i, s) : undefined))
    return d.map((_, i) => {
      const j = i - disp
      const a = j >= 0 && tenkan[j] !== undefined && kijun[j] !== undefined ? ((tenkan[j] as number) + (kijun[j] as number)) / 2 : undefined
      const b = j >= 0 ? spanBraw[j] : undefined
      const ch = i + disp < d.length ? d[i + disp].close : undefined
      return { tenkan: tenkan[i], kijun: kijun[i], spanA: a, spanB: b, chikou: ch }
    })
  },
}

const PIVOTS: IndicatorTemplate<Rec, number> = {
  name: 'PIVOTS', shortName: 'Pivots', series: 'price', calcParams: [],
  figures: [
    { key: 'r3', title: 'R3: ', type: 'line' }, { key: 'r2', title: 'R2: ', type: 'line' }, { key: 'r1', title: 'R1: ', type: 'line' },
    { key: 'p', title: 'P: ', type: 'line' },
    { key: 's1', title: 'S1: ', type: 'line' }, { key: 's2', title: 'S2: ', type: 'line' }, { key: 's3', title: 'S3: ', type: 'line' },
  ],
  calc: (d) => {
    // classic floor pivots from the previous session (session = day or gap-separated block)
    const out: Rec[] = new Array(d.length)
    let curH = -Infinity, curL = Infinity, curC = 0
    let prevH: number | undefined, prevL: number | undefined, prevC: number | undefined
    for (let i = 0; i < d.length; i++) {
      if (newSession(d, i) && i > 0) { prevH = curH; prevL = curL; prevC = curC; curH = -Infinity; curL = Infinity }
      curH = Math.max(curH, d[i].high)
      curL = Math.min(curL, d[i].low)
      curC = d[i].close
      if (prevH === undefined || prevL === undefined || prevC === undefined) { out[i] = {}; continue }
      const p = (prevH + prevL + prevC) / 3
      out[i] = {
        p, r1: 2 * p - prevL, s1: 2 * p - prevH, r2: p + (prevH - prevL), s2: p - (prevH - prevL),
        r3: prevH + 2 * (p - prevL), s3: prevL - 2 * (prevH - p),
      }
    }
    return out
  },
}

const KELTNER: IndicatorTemplate<Rec, number> = {
  name: 'KELTNER', shortName: 'Keltner', series: 'price', calcParams: [20, 10, 2],
  figures: [
    { key: 'up', title: 'Upper: ', type: 'line' },
    { key: 'mid', title: 'Mid: ', type: 'line' },
    { key: 'dn', title: 'Lower: ', type: 'line' },
  ],
  calc: (d, ind) => {
    const [n, an, mult] = ind.calcParams
    const mid = ema(d.map((c) => c.close), n)
    const atr = atrSeries(d, an)
    return d.map((_, i) => {
      const m = mid[i]; const a = atr[i]
      if (m === undefined || a === undefined) return {}
      return { up: m + mult * a, mid: m, dn: m - mult * a }
    })
  },
}

const DONCHIAN: IndicatorTemplate<Rec, number> = {
  name: 'DONCHIAN', shortName: 'Donchian', series: 'price', calcParams: [20],
  figures: [
    { key: 'up', title: 'Upper: ', type: 'line' },
    { key: 'mid', title: 'Mid: ', type: 'line' },
    { key: 'dn', title: 'Lower: ', type: 'line' },
  ],
  calc: (d, ind) => {
    const [n] = ind.calcParams
    return d.map((_, i) => {
      if (i < n - 1) return {}
      const up = highest(d, i, n); const dn = lowest(d, i, n)
      return { up, dn, mid: (up + dn) / 2 }
    })
  },
}

const HMA: IndicatorTemplate<Rec, number> = {
  name: 'HMA', shortName: 'HMA', series: 'price', calcParams: [9],
  figures: [{ key: 'hma', title: 'HMA: ', type: 'line' }],
  calc: (d, ind) => {
    const [n] = ind.calcParams
    const closes = d.map((c) => c.close)
    const half = wma(closes, Math.max(1, Math.floor(n / 2)))
    const full = wma(closes, n)
    const diff = closes.map((_, i) => (half[i] !== undefined && full[i] !== undefined ? 2 * (half[i] as number) - (full[i] as number) : undefined))
    const h = wma(diff, Math.max(1, Math.round(Math.sqrt(n))))
    return d.map((_, i) => ({ hma: h[i] }))
  },
}

/** RSI as in TradingView: Wilder's smoothing (RMA), one line; params: length, upper band, lower band. */
const RSI: IndicatorTemplate<Rec, number> = {
  name: 'RSI', shortName: 'RSI', calcParams: [14, 70, 30], precision: 2,
  figures: [{ key: 'rsi', title: 'RSI: ', type: 'line', styles: () => ({ color: RSI_COLOR }) }],
  calc: (d, ind) => {
    const n = Math.max(1, Math.round(ind.calcParams[0] ?? 14))
    const out: Rec[] = d.map(() => ({}))
    let up = 0
    let dn = 0
    for (let i = 1; i < d.length; i++) {
      const ch = d[i].close - d[i - 1].close
      const u = ch > 0 ? ch : 0
      const w = ch < 0 ? -ch : 0
      if (i <= n) {
        up += u
        dn += w
        if (i < n) continue
        up /= n
        dn /= n
      } else {
        up = (up * (n - 1) + u) / n
        dn = (dn * (n - 1) + w) / n
      }
      out[i] = { rsi: dn === 0 ? 100 : 100 - 100 / (1 + up / dn) }
    }
    return out
  },
}

/** Williams %R, one line; params: length, upper band, lower band. */
const WR: IndicatorTemplate<Rec, number> = {
  name: 'WR', shortName: '%R', calcParams: [14, -20, -80], precision: 2,
  figures: [{ key: 'wr', title: '%R: ', type: 'line' }],
  calc: (d, ind) => {
    const n = Math.max(1, Math.round(ind.calcParams[0] ?? 14))
    return d.map((c, i) => {
      if (i < n - 1) return {}
      const hh = highest(d, i, n)
      const ll = lowest(d, i, n)
      return { wr: hh === ll ? -50 : ((hh - c.close) / (hh - ll)) * -100 }
    })
  },
}

export const CUSTOM_INDICATORS = [ATR, VWAP, SUPERTREND, STOCH, ICHIMOKU, PIVOTS, KELTNER, DONCHIAN, HMA, RSI, WR]
// templates with their own legends / async data (registered as they are)
const EXTRA_INDICATORS = [COMPARE, VPVR, FUNDING, OI, LIQ, ALERT_LINES_TEMPLATE] as unknown as IndicatorTemplate[]

/** Finite, within ±1000 (periods, multipliers). */
export function safeParam(p: unknown): number {
  const n = Number(p)
  return Number.isFinite(n) ? Math.max(-1000, Math.min(1000, n)) : 1
}

let registered = false
export function registerCustomIndicators() {
  if (registered) return
  registered = true
  // legend titles in the UI language (templates are module constants, the language is known only now)
  for (const t of CUSTOM_INDICATORS) {
    const calc = t.calc
    registerIndicator({
      ...t,
      figures: t.figures?.map((f) => ({ ...f, title: f.title ? tr(f.title) : f.title })),
      // loops are O(bars × period): clamp what the calc sees (layouts and shared links carry user-typed values)
      calc: (d, ind) => calc(d, { ...ind, calcParams: ind.calcParams.map((p) => safeParam(p)) } as typeof ind),
    })
  }
  for (const x of EXTRA_INDICATORS) registerIndicator(x)
  for (const p of PLUGIN_INDICATORS) registerIndicator(p.template as unknown as IndicatorTemplate)
}

/** Catalogue for the Indicators dialog: built-ins + custom, with where they are drawn by default. */
export interface IndicatorMeta { name: string; label: string; main: boolean; params?: number[]; paramNames?: string[]; precision?: number; group: 'Trend' | 'Momentum' | 'Volatility' | 'Volume' | 'Levels' | 'Crypto'; options?: Record<string, unknown>; perpOnly?: boolean }
export const INDICATOR_CATALOG: IndicatorMeta[] = [
  ...PLUGIN_INDICATORS.map((p) => p.meta),
  { name: 'MA', label: 'Moving Average', main: true, params: [20, 50, 200], group: 'Trend' },
  { name: 'EMA', label: 'Exponential MA', main: true, params: [9, 21, 55], group: 'Trend' },
  { name: 'SMA', label: 'Smoothed MA', main: true, params: [12, 2], group: 'Trend' },
  { name: 'HMA', label: 'Hull MA', main: true, params: [9], group: 'Trend' },
  { name: 'BOLL', label: 'Bollinger Bands', main: true, params: [20, 2], group: 'Volatility' },
  { name: 'KELTNER', label: 'Keltner Channels', main: true, params: [20, 10, 2], group: 'Volatility' },
  { name: 'DONCHIAN', label: 'Donchian Channels', main: true, params: [20], group: 'Volatility' },
  { name: 'SUPERTREND', label: 'SuperTrend', main: true, params: [10, 3], group: 'Trend' },
  { name: 'ICHIMOKU', label: 'Ichimoku Cloud', main: true, params: [9, 26, 52, 26], group: 'Trend' },
  { name: 'SAR', label: 'Parabolic SAR', main: true, params: [2, 2, 20], group: 'Trend' },
  { name: 'VWAP', label: 'VWAP (session)', main: true, group: 'Volume' },
  { name: 'VPVR', label: 'Volume profile (visible range)', main: true, params: [24], group: 'Volume' },
  { name: 'FUNDING', label: 'Funding rate', main: false, group: 'Crypto', perpOnly: true },
  { name: 'OI', label: 'Open interest', main: false, group: 'Crypto', perpOnly: true },
  { name: 'LIQ', label: 'Liquidations', main: false, group: 'Crypto', perpOnly: true },
  { name: 'PIVOTS', label: 'Pivot Points (classic)', main: true, group: 'Levels' },
  { name: 'BBI', label: 'Bull & Bear Index', main: true, params: [3, 6, 12, 24], group: 'Trend' },
  { name: 'VOL', label: 'Volume', main: false, params: [5, 10, 20], group: 'Volume' },
  { name: 'MACD', label: 'MACD', main: false, params: [12, 26, 9], precision: 2, group: 'Momentum' },
  { name: 'RSI', label: 'RSI', main: false, params: [14, 70, 30], paramNames: ['Length', 'Upper', 'Lower'], precision: 2, group: 'Momentum' },
  { name: 'STOCH', label: 'Stochastic', main: false, params: [14, 3, 3, 80, 20], paramNames: ['Length', 'Smooth K', 'Smooth D', 'Upper', 'Lower'], precision: 2, group: 'Momentum' },
  { name: 'KDJ', label: 'KDJ', main: false, params: [9, 3, 3, 80, 20], paramNames: ['Length', 'Smooth K', 'Smooth D', 'Upper', 'Lower'], precision: 2, group: 'Momentum' },
  { name: 'CCI', label: 'CCI', main: false, params: [20, 100, -100], paramNames: ['Length', 'Upper', 'Lower'], precision: 2, group: 'Momentum' },
  { name: 'WR', label: 'Williams %R', main: false, params: [14, -20, -80], paramNames: ['Length', 'Upper', 'Lower'], precision: 2, group: 'Momentum' },
  { name: 'DMI', label: 'DMI / ADX', main: false, params: [14, 6, 25], paramNames: ['Length', 'ADX', 'Level'], precision: 2, group: 'Trend' },
  { name: 'ATR', label: 'ATR', main: false, params: [14], group: 'Volatility' },
  { name: 'OBV', label: 'On Balance Volume', main: false, params: [30], precision: 0, group: 'Volume' },
  { name: 'MTM', label: 'Momentum', main: false, params: [12, 6], precision: 2, group: 'Momentum' },
  { name: 'ROC', label: 'Rate of Change', main: false, params: [12, 6], precision: 2, group: 'Momentum' },
  { name: 'TRIX', label: 'TRIX', main: false, params: [12, 9], precision: 3, group: 'Momentum' },
  { name: 'AO', label: 'Awesome Oscillator', main: false, params: [5, 34], precision: 2, group: 'Momentum' },
  { name: 'PSY', label: 'Psychological Line', main: false, params: [12, 6], precision: 2, group: 'Momentum' },
  { name: 'EMV', label: 'Ease of Movement', main: false, params: [14, 9], precision: 4, group: 'Volume' },
  { name: 'PVT', label: 'Price Volume Trend', main: false, precision: 0, group: 'Volume' },
  { name: 'VR', label: 'Volume Ratio', main: false, params: [26, 6], precision: 2, group: 'Volume' },
  { name: 'BIAS', label: 'Bias', main: false, params: [6, 12, 24], precision: 2, group: 'Momentum' },
  { name: 'DMA', label: 'DMA', main: false, params: [10, 50, 10], precision: 2, group: 'Trend' },
  { name: 'CR', label: 'CR', main: false, params: [26, 10, 20, 40, 60], precision: 2, group: 'Momentum' },
]
