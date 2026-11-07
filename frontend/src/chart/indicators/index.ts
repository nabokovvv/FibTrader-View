import { registerIndicator, type IndicatorTemplate, type KLineData } from 'klinecharts'
import { t as tr } from '../../i18n'
import { PLUGIN_INDICATORS } from '../../plugins'
import { FNG, FUNDING, LIQ, OI, PDHL, SESSIONS, VPVR, YTS } from './extra'
import { ALERT_LINES_TEMPLATE } from './alertLines'
import { alphaTrend, atrSeries, cmf, mfi, rsiSeries, stochRsi, superTrend } from './calc'
import { drawSignals } from './signals'
import { useStore } from '../../state/store'
import { hexAlpha } from '../theme'
import { MFI_COLOR, RSI_COLOR } from './levels'

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
  name: 'SUPERTREND', shortName: 'SuperTrend', series: 'price', calcParams: [10, 3, 1],
  figures: [
    { key: 'up', title: 'Up: ', type: 'line', styles: () => ({ color: '#089981', size: 2 }) },
    { key: 'dn', title: 'Down: ', type: 'line', styles: () => ({ color: '#f23645', size: 2 }) },
  ],
  calc: (d, ind) => {
    const [n, mult] = ind.calcParams
    return superTrend(d, n ?? 10, mult ?? 3).map((r) => ({ up: r.up, dn: r.dn, dir: r.dir, signal: r.signal }))
  },
  // buy / sell markers where the direction turns (3rd parameter: 0 hides them); the lines are drawn as usual
  draw: ({ ctx, chart, indicator, xAxis, yAxis }) => {
    if (Number(indicator.calcParams[2] ?? 1) === 0) return false
    const rows = indicator.result as Rec[]
    drawSignals(ctx, chart, xAxis, yAxis, (i) => rows[i]?.signal as 1 | -1 | undefined, (i) => rows[i]?.up ?? rows[i]?.dn)
    return false
  },
}

/** AlphaTrend; params: multiplier, period, signals (0 = off). Drawn here: two lines, the band between them in the
 *  trend's colour, and buy / sell markers. */
const ALPHATREND: IndicatorTemplate<Rec, number> = {
  name: 'ALPHATREND', shortName: 'AlphaTrend', series: 'price', calcParams: [1, 14, 1],
  figures: [
    { key: 'at', title: 'AT: ', type: 'line' },
    { key: 'at2', title: 'AT[2]: ', type: 'line' },
  ],
  calc: (d, ind) => {
    const [coeff, period] = ind.calcParams
    return alphaTrend(d, coeff ?? 1, period ?? 14).map((r) => ({ at: r.at, at2: r.at2, rising: r.rising === undefined ? undefined : r.rising ? 1 : 0, signal: r.signal }))
  },
  draw: ({ ctx, chart, indicator, xAxis, yAxis }) => {
    const rows = indicator.result as Rec[]
    const vr = chart.getVisibleRange()
    const from = Math.max(1, Math.floor(vr.from) - 1)
    const to = Math.min(rows.length, Math.ceil(vr.to) + 1)
    const { upColor, downColor } = useStore.getState().settings
    ctx.save()
    for (let i = from; i < to; i++) {
      const a = rows[i - 1], b = rows[i]
      if (a?.at === undefined || a.at2 === undefined || b?.at === undefined || b.at2 === undefined) continue
      const color = b.rising ? upColor : downColor
      const x1 = xAxis.convertToPixel(i - 1), x2 = xAxis.convertToPixel(i)
      const y = (v: number) => yAxis.convertToPixel(v)
      ctx.fillStyle = hexAlpha(color, 0.14)
      ctx.beginPath()
      ctx.moveTo(x1, y(a.at)); ctx.lineTo(x2, y(b.at)); ctx.lineTo(x2, y(b.at2)); ctx.lineTo(x1, y(a.at2))
      ctx.closePath()
      ctx.fill()
      ctx.strokeStyle = color
      ctx.lineWidth = 2.5
      ctx.beginPath(); ctx.moveTo(x1, y(a.at)); ctx.lineTo(x2, y(b.at)); ctx.stroke()
      ctx.lineWidth = 1.5
      ctx.beginPath(); ctx.moveTo(x1, y(a.at2)); ctx.lineTo(x2, y(b.at2)); ctx.stroke()
    }
    ctx.restore()
    // as in the original, the badge sits on the lagging line (AT two bars back)
    if (Number(indicator.calcParams[2] ?? 1) !== 0) drawSignals(ctx, chart, xAxis, yAxis, (i) => rows[i]?.signal as 1 | -1 | undefined, (i) => rows[i]?.at2)
    return true
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
  calc: (d, ind) => rsiSeries(d.map((c) => c.close), ind.calcParams[0] ?? 14).map((rsi) => (rsi === undefined ? {} : { rsi })),
}

/** Stochastic RSI; params: RSI length, stochastic length, %K smoothing, %D smoothing, upper band, lower band. */
const STOCHRSI: IndicatorTemplate<Rec, number> = {
  name: 'STOCHRSI', shortName: 'Stoch RSI', calcParams: [14, 14, 3, 3, 80, 20], precision: 2,
  figures: [
    { key: 'k', title: 'K: ', type: 'line' },
    { key: 'd', title: 'D: ', type: 'line' },
  ],
  calc: (d, ind) => {
    const [rl, sl, k, dd] = ind.calcParams
    return stochRsi(d, rl ?? 14, sl ?? 14, k ?? 3, dd ?? 3)
  },
}

/** Money Flow Index, one line; params: length, upper band, lower band. */
const MFI: IndicatorTemplate<Rec, number> = {
  name: 'MFI', shortName: 'MFI', calcParams: [14, 80, 20], precision: 2,
  figures: [{ key: 'mfi', title: 'MFI: ', type: 'line', styles: () => ({ color: MFI_COLOR }) }],
  calc: (d, ind) => mfi(d, ind.calcParams[0] ?? 14).map((v) => ({ mfi: v })),
}

/** Chaikin Money Flow around zero; params: length. */
const CMF: IndicatorTemplate<Rec, number> = {
  name: 'CMF', shortName: 'CMF', calcParams: [20], precision: 3,
  figures: [{ key: 'cmf', title: 'CMF: ', type: 'line' }],
  calc: (d, ind) => cmf(d, ind.calcParams[0] ?? 20).map((v) => ({ cmf: v })),
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

export const CUSTOM_INDICATORS = [ATR, VWAP, SUPERTREND, ALPHATREND, STOCH, ICHIMOKU, PIVOTS, KELTNER, DONCHIAN, HMA, RSI, WR, STOCHRSI, MFI, CMF]
// templates with their own legends / async data (registered as they are)
const EXTRA_INDICATORS = [VPVR, PDHL, SESSIONS, FUNDING, OI, LIQ, FNG, YTS, ALERT_LINES_TEMPLATE] as unknown as IndicatorTemplate[]

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

export { INDICATOR_CATALOG, type IndicatorMeta } from './catalog'
