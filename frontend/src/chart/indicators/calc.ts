/* ------------------------------------------------------------------------------------------------------------
 * Pure calculations behind several indicators. No chart, store or i18n imports on purpose: scripts/test-indicators.mjs
 * runs these in Node on hand-made bars (the build and the public deploy stop when it fails).
 * Every function sees only the bars it is given, so a bar replay (which hands over bars up to its moment) cannot
 * leak later data through them.
 * ---------------------------------------------------------------------------------------------------------- */

export interface Bar { timestamp: number; open: number; high: number; low: number; close: number; volume?: number }
export type Num = number | undefined

const DAY = 86_400_000

/** RSI with Wilder's smoothing (as in common charting apps); undefined until `n` changes are seen. */
export function rsiSeries(close: number[], n: number): Num[] {
  n = Math.max(1, Math.round(n))
  const out: Num[] = new Array(close.length).fill(undefined)
  let up = 0
  let dn = 0
  for (let i = 1; i < close.length; i++) {
    const ch = close[i] - close[i - 1]
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
    out[i] = dn === 0 ? 100 : 100 - 100 / (1 + up / dn)
  }
  return out
}

/** Simple moving average over defined values (undefined while the window has a gap). */
export function smaSeries(v: Num[], m: number): Num[] {
  m = Math.max(1, Math.round(m))
  return v.map((_, i) => {
    if (i < m - 1) return undefined
    let s = 0
    for (let j = 0; j < m; j++) { const x = v[i - j]; if (x === undefined) return undefined; s += x }
    return s / m
  })
}

/** Stochastic RSI: the stochastic of the RSI line, %K smoothed, %D = average of %K. */
export function stochRsi(bars: Bar[], rsiLen: number, stochLen: number, smoothK: number, smoothD: number): Array<{ k: Num; d: Num }> {
  const rsi = rsiSeries(bars.map((b) => b.close), rsiLen)
  const n = Math.max(1, Math.round(stochLen))
  const raw: Num[] = rsi.map((_, i) => {
    if (i < n - 1) return undefined
    let hh = -Infinity
    let ll = Infinity
    for (let j = 0; j < n; j++) {
      const x = rsi[i - j]
      if (x === undefined) return undefined
      if (x > hh) hh = x
      if (x < ll) ll = x
    }
    return hh === ll ? 50 : ((rsi[i]! - ll) / (hh - ll)) * 100
  })
  const k = smaSeries(raw, smoothK)
  const d = smaSeries(k, smoothD)
  return bars.map((_, i) => ({ k: k[i], d: d[i] }))
}

/** Money Flow Index: a volume-weighted RSI of the typical price (high + low + close) / 3. */
export function mfi(bars: Bar[], len: number): Num[] {
  const n = Math.max(1, Math.round(len))
  const tp = bars.map((b) => (b.high + b.low + b.close) / 3)
  const pos: number[] = new Array(bars.length).fill(0)
  const neg: number[] = new Array(bars.length).fill(0)
  for (let i = 1; i < bars.length; i++) {
    const flow = tp[i] * (bars[i].volume ?? 0)
    if (tp[i] > tp[i - 1]) pos[i] = flow
    else if (tp[i] < tp[i - 1]) neg[i] = flow
  }
  const out: Num[] = new Array(bars.length).fill(undefined)
  let p = 0
  let q = 0
  for (let i = 1; i < bars.length; i++) {
    p += pos[i]
    q += neg[i]
    if (i > n) { p -= pos[i - n]; q -= neg[i - n] }
    if (i < n) continue
    if (p <= 0 && q <= 0) continue            // no volume (or a flat price) in the window: nothing to say
    out[i] = q <= 0 ? 100 : 100 - 100 / (1 + p / q)
  }
  return out
}

/** Chaikin Money Flow: the share of volume with closes near the bar's high (+) or low (−), −1…1. */
export function cmf(bars: Bar[], len: number): Num[] {
  const n = Math.max(1, Math.round(len))
  const mfv = bars.map((b) => {
    const r = b.high - b.low
    return r > 0 ? ((2 * b.close - b.low - b.high) / r) * (b.volume ?? 0) : 0
  })
  const out: Num[] = new Array(bars.length).fill(undefined)
  let s = 0
  let v = 0
  for (let i = 0; i < bars.length; i++) {
    s += mfv[i]
    v += bars[i].volume ?? 0
    if (i >= n) { s -= mfv[i - n]; v -= bars[i - n].volume ?? 0 }
    if (i >= n - 1 && v > 0) out[i] = s / v
  }
  return out
}

/* ------------------------------------------------------------------------------------------------------------
 * Volume profile: volume at price over a set of bars, split into rising (close ≥ open) and falling bars. Each bar's
 * volume is spread evenly over the rows its high–low range touches. POC = the fullest row; the value area grows
 * from the POC towards the bigger neighbour until it holds 70 % of the volume.
 * ---------------------------------------------------------------------------------------------------------- */
export interface Profile {
  lo: number; hi: number; step: number; rows: number
  up: number[]; dn: number[]; total: number[]; max: number
  poc: number; vaLo: number; vaHi: number
}

export function volumeProfile(bars: Bar[], rowsWanted: number, valueArea = 0.7): Profile | null {
  const rows = Math.max(6, Math.min(80, Math.round(rowsWanted) || 24))
  let lo = Infinity
  let hi = -Infinity
  for (const b of bars) { if (b.low < lo) lo = b.low; if (b.high > hi) hi = b.high }
  if (!bars.length || !(hi > lo)) return null
  const step = (hi - lo) / rows
  const up: number[] = new Array(rows).fill(0)
  const dn: number[] = new Array(rows).fill(0)
  for (const b of bars) {
    const v = b.volume ?? 0
    if (!(v > 0)) continue
    const a = Math.max(0, Math.min(rows - 1, Math.floor((b.low - lo) / step)))
    const z = Math.max(0, Math.min(rows - 1, Math.floor((b.high - lo) / step)))
    const share = v / (z - a + 1)
    const tgt = b.close >= b.open ? up : dn
    for (let k = a; k <= z; k++) tgt[k] += share
  }
  const total = up.map((u, k) => u + dn[k])
  const max = Math.max(...total)
  if (!(max > 0)) return null
  const poc = total.indexOf(max)
  const all = total.reduce((s, v) => s + v, 0)
  let va = total[poc]
  let vaLo = poc
  let vaHi = poc
  while (va < all * valueArea && (vaLo > 0 || vaHi < rows - 1)) {
    const below = vaLo > 0 ? total[vaLo - 1] : -1
    const above = vaHi < rows - 1 ? total[vaHi + 1] : -1
    if (above >= below) { vaHi++; va += total[vaHi] } else { vaLo--; va += total[vaLo] }
  }
  return { lo, hi, step, rows, up, dn, total, max, poc, vaLo, vaHi }
}

/* ------------------------------------------------------------------------------------------------------------
 * Previous day / week / month high and low, from daily bars. The server labels every daily bar with 00:00 UTC of
 * its date in the exchange's time zone (markets/_util.py: utc_midnight_of_local_date), so a chart bar's day is its
 * calendar date in that time zone (SymbolInfo.timezone; 'UTC' for crypto); weeks start on Monday, months by the
 * calendar. A chart bar gets the levels of the last COMPLETE period before its own one, so the levels never
 * depend on the period the bar is in (nothing from later bars, also in a bar replay).
 * ---------------------------------------------------------------------------------------------------------- */
export interface DailyBar { timestamp: number; high: number; low: number }
export interface PeriodLevels { pdh?: number; pdl?: number; pwh?: number; pwl?: number; pmh?: number; pml?: number }

const dateOf = (open: number) => Math.floor((open + DAY / 2) / DAY)    // days since 1970-01-01
const weekOf = (date: number) => Math.floor((date + 3) / 7)             // 1970-01-01 was a Thursday: weeks from Monday
const monthOf = (date: number) => { const x = new Date(date * DAY); return x.getUTCFullYear() * 12 + x.getUTCMonth() }
const weekStart = (w: number) => w * 7 - 3
const monthStart = (m: number) => Math.round(Date.UTC(Math.floor(m / 12), m % 12, 1) / DAY)

interface Agg { key: number; high: number; low: number }

function group(days: Array<{ date: number; high: number; low: number }>, keyOf: (date: number) => number,
  startOf: (key: number) => number, firstDate: number, complete: boolean): Agg[] {
  const out: Agg[] = []
  for (const d of days) {
    const k = keyOf(d.date)
    const last = out[out.length - 1]
    if (last && last.key === k) { if (d.high > last.high) last.high = d.high; if (d.low < last.low) last.low = d.low }
    else out.push({ key: k, high: d.high, low: d.low })
  }
  // the oldest period may have started before the first daily bar we have: only trust it when nothing is older
  if (out.length && !complete && startOf(out[0].key) < firstDate) out.shift()
  return out
}

/** Index of the last element with key < k (or -1). */
function lastBelow(list: Array<{ key: number }>, k: number): number {
  let lo = 0
  let hi = list.length
  while (lo < hi) { const m = (lo + hi) >> 1; if (list[m].key < k) lo = m + 1; else hi = m }
  return lo - 1
}

/** Where the levels of the oldest chart bar start: the first day of the month before its month (exchange dates). */
export function levelsNeedFrom(firstBar: number, tz: string): number {
  const x = new Date(Math.floor((firstBar + tzOffset(tz, firstBar)) / DAY) * DAY)
  return Date.UTC(x.getUTCFullYear(), x.getUTCMonth() - 1, 1)
}

/**
 * Levels for each chart bar (by its open time). `daily` sorted by time; `tz`: the exchange's time zone;
 * `complete`: no older daily bars exist (the oldest week / month in the list is then whole).
 */
export function periodLevels(chartTimes: number[], daily: DailyBar[], tz: string, complete = false): PeriodLevels[] {
  if (!daily.length) return chartTimes.map(() => ({}))
  const days = daily.map((b) => ({ date: dateOf(b.timestamp), high: b.high, low: b.low }))
  const dayList: Agg[] = days.map((d) => ({ key: d.date, high: d.high, low: d.low }))
  const firstDate = days[0].date
  const weeks = group(days, weekOf, weekStart, firstDate, complete)
  const months = group(days, monthOf, monthStart, firstDate, complete)
  return chartTimes.map((t) => {
    const date = Math.floor((t + tzOffset(tz, t)) / DAY)
    if (date < firstDate) return {}                  // older than the daily bars we have: unknown
    const out: PeriodLevels = {}
    const d = lastBelow(dayList, date)
    if (d >= 0) { out.pdh = dayList[d].high; out.pdl = dayList[d].low }
    const w = lastBelow(weeks, weekOf(date))
    if (w >= 0) { out.pwh = weeks[w].high; out.pwl = weeks[w].low }
    const m = lastBelow(months, monthOf(date))
    if (m >= 0) { out.pmh = months[m].high; out.pml = months[m].low }
    return out
  })
}

/* ------------------------------------------------------------------------------------------------------------
 * Trading sessions in their cities' local time (daylight saving included): a bar belongs to a session when it
 * opens inside [from, to) local hours; `to` < `from` wraps over midnight; `from` = `to` switches the session off.
 * ---------------------------------------------------------------------------------------------------------- */
export interface SessionSpec { tz: string; from: number; to: number }

const fmt = new Map<string, Intl.DateTimeFormat>()
const offsets = new Map<string, number>()

/** UTC offset of a time zone at a moment, ms (cached per hour: offsets change on whole hours). */
export function tzOffset(tz: string, t: number): number {
  const hour = Math.floor(t / 3_600_000)
  const key = `${tz}|${hour}`
  const hit = offsets.get(key)
  if (hit !== undefined) return hit
  let f = fmt.get(tz)
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric' })
    fmt.set(tz, f)
  }
  const p: Record<string, number> = {}
  for (const x of f.formatToParts(hour * 3_600_000)) if (x.type !== 'literal') p[x.type] = Number(x.value)
  const v = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute) - hour * 3_600_000
  if (offsets.size > 100_000) offsets.clear()
  offsets.set(key, v)
  return v
}

const validHour = (h: number) => Number.isFinite(h) && h >= 0 && h <= 24

/**
 * For each session and each bar: the index of the first bar of the session the bar is in, or -1.
 * Bars of one session share that index (it identifies the session on the chart).
 */
export function sessionStarts(times: number[], specs: SessionSpec[]): number[][] {
  return specs.map((s) => {
    const out: number[] = new Array(times.length).fill(-1)
    if (!validHour(s.from) || !validHour(s.to) || s.from === s.to) return out
    const wraps = s.to < s.from
    let curKey = NaN
    let start = -1
    for (let i = 0; i < times.length; i++) {
      const local = times[i] + tzOffset(s.tz, times[i])
      const day = Math.floor(local / DAY)
      const h = (local - day * DAY) / 3_600_000
      const inside = wraps ? h >= s.from || h < s.to : h >= s.from && h < s.to
      if (!inside) continue
      const key = wraps && h < s.to ? day - 1 : day
      if (key !== curKey) { curKey = key; start = i }
      out[i] = start
    }
    return out
  })
}

/* ------------------------------------------------------------------------------------------------------------
 * Trend lines with buy / sell signals.
 * ---------------------------------------------------------------------------------------------------------- */
/** 1 = buy, −1 = sell, undefined = none. */
export type Signal = 1 | -1 | undefined

function trueRange(d: Bar[], i: number): number {
  const c = d[i]
  if (i === 0) return c.high - c.low
  const pc = d[i - 1].close
  return Math.max(c.high - c.low, Math.abs(c.high - pc), Math.abs(c.low - pc))
}

/** ATR with Wilder's smoothing. */
export function atrSeries(d: Bar[], n: number): Num[] {
  n = Math.max(1, Math.round(n))
  const out: Num[] = new Array(d.length)
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

/** SuperTrend (ATR bands around hl2 that only tighten); `signal` on the bar where the direction turns. */
export function superTrend(d: Bar[], n: number, mult: number): Array<{ up?: number; dn?: number; dir?: number; signal?: Signal }> {
  const atr = atrSeries(d, n)
  const out: Array<{ up?: number; dn?: number; dir?: number; signal?: Signal }> = new Array(d.length)
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
    const prevDir = out[i - 1]?.dir
    st = dir > 0 ? lower : upper
    out[i] = { up: dir > 0 ? st : undefined, dn: dir < 0 ? st : undefined, dir,
      signal: prevDir !== undefined && prevDir !== dir ? (dir > 0 ? 1 : -1) : undefined }
  }
  return out
}

/**
 * AlphaTrend (Kivanc Ozbilgic): low − ATR × coeff while the money flow is up (MFI ≥ 50), high + ATR × coeff while
 * it is down, the line never stepping back; ATR is the simple average of the true range. Where the window has no
 * volume, RSI replaces MFI bar by bar (decided from past bars only: volume appearing later changes nothing before).
 * Signals: the line crossing its own value two bars back, only alternating buy / sell. Colour as in the original:
 * up when AT > AT[2], down when below, on a tie by AT[1] vs AT[3].
 * Two deliberate differences from the original Pine script: it starts from 0 (nz) and can sit at zero until the
 * first up-flow, here the line starts at the first band (a zero line would squash the price scale of a chart whose
 * loaded history begins in a fall); and the side the line starts on counts as the previous signal, so the first
 * turn is shown.
 */
export function alphaTrend(d: Bar[], coeff: number, period: number): Array<{ at?: number; at2?: number; rising?: boolean; signal?: Signal }> {
  const n = Math.max(1, Math.round(period))
  const money = mfi(d, n)
  const rsi = rsiSeries(d.map((b) => b.close), n)
  const flow = d.map((_, i) => money[i] ?? rsi[i])
  const tr = smaSeries(d.map((_, i) => trueRange(d, i)), n)
  const at: Num[] = new Array(d.length).fill(undefined)
  let prev: number | undefined
  for (let i = 0; i < d.length; i++) {
    const a = tr[i]
    const f = flow[i]
    if (a === undefined || f === undefined) continue
    const upT = d[i].low - a * coeff
    const downT = d[i].high + a * coeff
    const v = f >= 50 ? (prev !== undefined && upT < prev ? prev : upT) : (prev !== undefined && downT > prev ? prev : downT)
    at[i] = v
    prev = v
  }
  const out: Array<{ at?: number; at2?: number; rising?: boolean; signal?: Signal }> = new Array(d.length)
  let lastBuy = -1
  let lastSell = -1
  for (let i = 0; i < d.length; i++) {
    const a = at[i]
    const a2 = i >= 2 ? at[i - 2] : undefined
    const row: { at?: number; at2?: number; rising?: boolean; signal?: Signal } = { at: a, at2: a2 }
    if (a !== undefined && a2 !== undefined) {
      const p1 = at[i - 1]
      const p3 = i >= 3 ? at[i - 3] : undefined
      row.rising = a > a2 ? true : a < a2 ? false : p1 !== undefined && p3 !== undefined && p1 > p3
      // the side the line starts on counts as the last signal, so the first turn is shown too
      if (lastBuy < 0 && lastSell < 0) { if (a > a2) lastBuy = i; else if (a < a2) lastSell = i }
      const pa = at[i - 1]
      const pa2 = i >= 3 ? at[i - 3] : undefined
      if (pa !== undefined && pa2 !== undefined) {
        const crossUp = pa <= pa2 && a > a2
        const crossDown = pa >= pa2 && a < a2
        if (crossUp) { if (lastSell > lastBuy) row.signal = 1; lastBuy = i }
        if (crossDown) { if (lastBuy > lastSell) row.signal = -1; lastSell = i }
      }
    }
    out[i] = row
  }
  return out
}
