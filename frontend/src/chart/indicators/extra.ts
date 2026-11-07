import type { IndicatorTemplate, IndicatorTooltipData, KLineData } from 'klinecharts'
import { ApiError, api } from '../../api/client'
import { lockOf, type HistoryLock } from '../../api/history'
import { getLang, t } from '../../i18n'
import type { Bar, Interval } from '../../lib/types'
import { useStore } from '../../state/store'
import { hexAlpha } from '../theme'
import { live } from '../live'
import { barEnd } from '../periods'
import { levelsNeedFrom, periodLevels, sessionStarts, volumeProfile, type DailyBar, type PeriodLevels } from './calc'
import { indicatorMeta, unavailable } from './catalog'

/* ------------------------------------------------------------------------------------------------------------
 * Crypto panels (Bybit perpetuals): funding rate, open interest, liquidations. Data comes from /api/derivs,
 * cached here per (kind, symbol, interval); each calc makes at most one request (initial range, a fresh tail, or
 * older history) and asks for another calc while older history is still being filled in.
 * ---------------------------------------------------------------------------------------------------------- */
type Kind = 'funding' | 'oi' | 'liq'
// what the server says it cannot give (where an indicator works at all comes from catalog.ts)
type Status = 'interval' | 'not-recorded' | 'source' | 'error' | undefined
interface Entry {
  points: number[][]; coveredFrom: number; exhausted: boolean; status: Status
  ok: boolean            // one successful answer seen (the coverage cursor is valid)
  fetchedAt: number      // last successful newest-data fetch
  retryAt: number        // after a failure: no request before this time
  closedUntil: number    // older points closed until then (sign in: for good; history limit: until it ends)
  backoff: number
  busy: Promise<void> | null
}
const cache = new Map<string, Entry>()
const TTL: Record<Kind, number> = { funding: 300_000, oi: 60_000, liq: 60_000 }

function recalc(indicatorId: string) {
  const ind = live.chart?.getIndicators({ id: indicatorId })[0]
  if (ind) live.chart?.overrideIndicator({ id: indicatorId, name: ind.name, extendData: { rev: Date.now() } })
}

const closedUntil = (lock: ReturnType<typeof lockOf>) => (lock?.kind === 'stopped' ? lock.until : Infinity)

/** At most one request per calc and per series at a time; failures back off (15 s → 2 min). */
async function ensure(kind: Kind, id: string, iv: Interval, from: number, indicatorId: string): Promise<Entry> {
  const key = `${kind}|${id}|${iv}`
  let en = cache.get(key)
  if (!en) {
    en = { points: [], coveredFrom: Infinity, exhausted: false, status: undefined, ok: false, fetchedAt: 0, retryAt: 0, closedUntil: 0, backoff: 15_000, busy: null }
    cache.set(key, en)
  }
  const e = en
  if (e.busy) { await e.busy; return e }  // share the request in flight, do not queue another
  const now = Date.now()
  if (now < e.retryAt) return e
  let range: [number, number] | null = null
  let older = false
  if (!e.ok) range = [from, now + 60_000]
  else if (now - e.fetchedAt > TTL[kind]) range = [Math.max(from, (e.points.at(-1)?.[0] ?? from) - 1), now + 60_000]
  else if (from < e.coveredFrom && !e.exhausted && now >= e.closedUntil) { range = [from, e.coveredFrom]; older = true }
  if (!range || !Number.isFinite(range[0]) || !Number.isFinite(range[1]) || range[0] >= range[1]) return e
  const initial = !e.ok
  const before = e.coveredFrom
  const [lo, hi] = range
  e.busy = (async () => {
    try {
      const r = await api.derivs(kind, id, iv, Math.floor(lo), Math.ceil(hi))
      const m = new Map(e.points.map((p) => [p[0], p]))
      for (const p of r.points) m.set(p[0], p)
      e.points = [...m.values()].sort((a, b) => a[0] - b[0])
      if (initial || older) { e.coveredFrom = Math.min(e.coveredFrom, r.covered_from); e.exhausted = r.exhausted }
      if (r.locked) e.closedUntil = closedUntil(lockOf(r))   // older points exist, closed for now: not the end
      if (!older) e.fetchedAt = Date.now()
      e.ok = true
      e.backoff = 15_000
      e.status = r.unavailable === 'interval' ? 'interval' : r.unavailable ? 'source' : kind === 'liq' && r.recorded === false ? 'not-recorded' : undefined
      // more history is still missing and this answer made progress: ask again shortly
      if (from < e.coveredFrom && !e.exhausted && !r.locked && e.coveredFrom < before) window.setTimeout(() => recalc(indicatorId), 1200)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'slow') {   // older history is paced: ask again when told
        e.retryAt = Date.now() + (err.retryAfter ?? 5) * 1000
        window.setTimeout(() => recalc(indicatorId), (err.retryAfter ?? 5) * 1000 + 200)
        return
      }
      const lock = lockOf(err)
      if (lock) {   // older history closed for now: what we have stays, no error, asked again once it opens
        e.closedUntil = closedUntil(lock)
        if (!e.ok) e.retryAt = e.closedUntil
        return
      }
      e.retryAt = Date.now() + e.backoff
      e.backoff = Math.min(e.backoff * 2, 120_000)
      if (!e.ok) e.status = 'error'
    }
  })().finally(() => { e.busy = null })
  await e.busy
  return e
}

/** Where the last bar ends: nothing after it belongs on the chart (a bar replay stops in the past). */
const lastEnd = (d: KLineData[]) => barEnd(d[d.length - 1].timestamp, useStore.getState().interval)

/** Last value at or before each bar's end (step line / bars), from point column `col`. */
function forward(d: KLineData[], pts: number[][], col: number, scale = 1): Array<number | undefined> {
  const out: Array<number | undefined> = new Array(d.length)
  let j = 0
  let last: number | undefined
  for (let i = 0; i < d.length; i++) {
    const end = i + 1 < d.length ? d[i + 1].timestamp : lastEnd(d)
    while (j < pts.length && pts[j][0] < end) { last = pts[j][col] * scale; j++ }
    out[i] = last
  }
  return out
}

/** Last value inside each bar (none if the bar has no point yet: today's value is published later). */
function lastInside(d: KLineData[], pts: number[][]): Array<number | undefined> {
  const out: Array<number | undefined> = new Array(d.length)
  let j = 0
  for (let i = 0; i < d.length; i++) {
    const end = i + 1 < d.length ? d[i + 1].timestamp : lastEnd(d)
    while (j < pts.length && pts[j][0] < d[i].timestamp) j++
    while (j < pts.length && pts[j][0] < end) { out[i] = pts[j][1]; j++ }
  }
  return out
}

/** Sum of the points inside each bar, per column. */
function sums(d: KLineData[], pts: number[][], cols: number[]): number[][] {
  const out = d.map(() => cols.map(() => 0))
  let j = 0
  for (let i = 0; i < d.length; i++) {
    const end = i + 1 < d.length ? d[i + 1].timestamp : lastEnd(d)
    while (j < pts.length && pts[j][0] < d[i].timestamp) j++
    while (j < pts.length && pts[j][0] < end) { cols.forEach((c, k) => { out[i][k] += pts[j][c] }); j++ }
  }
  return out
}

function context(): { id: string; iv: Interval } | null {
  const st = useStore.getState()
  if (!st.symbol) return null
  return { id: st.symbol.id, iv: st.interval }
}

/** The catalogue's answer for the current symbol and interval: why `name` cannot work here, or null. */
function whyNot(name: string): string | null {
  const st = useStore.getState()
  return unavailable(indicatorMeta(name), st.symbol, st.interval)
}

const statusText: Record<Exclude<Status, undefined>, string> = {
  interval: 'Open interest is available from the 5m interval.',
  'not-recorded': 'Liquidations are recorded for a fixed list: top 100 coins by market cap and top 50 stocks, ETFs and metals.',
  source: 'This exchange does not publish liquidations.',
  error: 'No data right now.',
}
const lastStatus = new Map<string, string | undefined>()  // indicator id → message shown in its legend (translated)
const statusMsg = (st: Status) => (st ? t(statusText[st]) : undefined)

function fmtBig(v: number): string {
  const a = Math.abs(v)
  const ru = getLang() === 'ru'
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}${ru ? ' млрд' : 'B'}`
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}${ru ? ' млн' : 'M'}`
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}${ru ? ' тыс' : 'K'}`
  return v.toFixed(a < 10 ? 2 : 0)
}

function legend(name: string, indicatorId: string, parts: Array<{ title: string; value: string; color: string }>) {
  const st = lastStatus.get(indicatorId)
  return {
    name, calcParamsText: '', features: [],
    legends: st ? [{ title: '', value: { text: st, color: '#8b929c' } }]
      : parts.map((p) => ({ title: { text: `${p.title} `, color: p.color }, value: { text: p.value, color: p.color } })),
  }
}

const colors = () => {
  const s = useStore.getState().settings
  return { up: s.upColor, down: s.downColor }
}

const KIND_NAME: Record<Kind, string> = { funding: 'FUNDING', oi: 'OI', liq: 'LIQ' }

async function load(kind: Kind, dataList: KLineData[], indicatorId: string): Promise<number[][] | null> {
  const c = context()
  if (!c || !dataList.length) return null
  const why = whyNot(KIND_NAME[kind])
  if (why) { lastStatus.set(indicatorId, why); return null }
  const e = await ensure(kind, c.id, c.iv, dataList[0].timestamp, indicatorId)
  lastStatus.set(indicatorId, statusMsg(e.status))
  return e.points
}

interface FundingRow { rate?: number }
export const FUNDING: IndicatorTemplate<FundingRow, number, unknown> = {
  name: 'FUNDING', shortName: 'Funding', precision: 4,
  figures: [{ key: 'rate', title: 'Funding: ', type: 'bar', baseValue: 0,
    styles: ({ data }) => ({ color: hexAlpha((data.current?.rate ?? 0) >= 0 ? colors().up : colors().down, 0.75) }) }],
  calc: async (d, ind) => {
    const pts = await load('funding', d, ind.id)
    if (!pts) return d.map(() => ({}))
    return forward(d, pts, 1, 100).map((v) => ({ rate: v }))
  },
  shouldUpdate: (prev, cur) => ({ calc: JSON.stringify(prev.extendData) !== JSON.stringify(cur.extendData), draw: true }),
  createTooltipDataSource: ({ indicator, crosshair }) => {
    const rows = indicator.result as FundingRow[]
    const v = (rows[crosshair.dataIndex ?? rows.length - 1] ?? rows.at(-1))?.rate
    return legend(t('Funding'), indicator.id, [{ title: '', value: v === undefined ? '—' : `${v.toFixed(4)}%`, color: (v ?? 0) >= 0 ? colors().up : colors().down }])
  },
}

interface OiRow { oi?: number }
export const OI: IndicatorTemplate<OiRow, number, unknown> = {
  name: 'OI', shortName: 'OI', precision: 0, shouldFormatBigNumber: true,
  figures: [{ key: 'oi', title: 'OI: ', type: 'line' }],
  calc: async (d, ind) => {
    const pts = await load('oi', d, ind.id)
    if (!pts) return d.map(() => ({}))
    return forward(d, pts, 1).map((v) => ({ oi: v }))
  },
  shouldUpdate: (prev, cur) => ({ calc: JSON.stringify(prev.extendData) !== JSON.stringify(cur.extendData), draw: true }),
  createTooltipDataSource: ({ indicator, crosshair }) => {
    const rows = indicator.result as OiRow[]
    const v = (rows[crosshair.dataIndex ?? rows.length - 1] ?? rows.at(-1))?.oi
    const color = (indicator.styles?.lines?.[0]?.color as string | undefined) ?? '#f59e0b'
    return legend(t('Open interest'), indicator.id, [{ title: '', value: v === undefined ? '—' : fmtBig(v), color }])
  },
}

interface LiqRow { longs?: number; shorts?: number }
export const LIQ: IndicatorTemplate<LiqRow, number, unknown> = {
  name: 'LIQ', shortName: 'Liq', precision: 0, shouldFormatBigNumber: true,
  // longs liquidated (price fell) below zero in the down colour, shorts above zero in the up colour
  figures: [
    { key: 'shorts', title: '', type: 'bar', baseValue: 0, styles: () => ({ color: hexAlpha(colors().up, 0.8) }) },
    { key: 'longs', title: '', type: 'bar', baseValue: 0, styles: () => ({ color: hexAlpha(colors().down, 0.8) }) },
  ],
  calc: async (d, ind) => {
    const pts = await load('liq', d, ind.id)
    if (!pts) return d.map(() => ({}))
    return sums(d, pts, [1, 2]).map(([l, s]) => ({ longs: l ? -l : undefined, shorts: s || undefined }))
  },
  shouldUpdate: (prev, cur) => ({ calc: JSON.stringify(prev.extendData) !== JSON.stringify(cur.extendData), draw: true }),
  createTooltipDataSource: ({ indicator, crosshair }) => {
    const rows = indicator.result as LiqRow[]
    const r = rows[crosshair.dataIndex ?? rows.length - 1] ?? rows.at(-1)
    const { up, down } = colors()
    return legend(t('Liquidations, $'), indicator.id, [
      { title: t('longs'), value: r?.longs ? fmtBig(-r.longs) : '0', color: down },
      { title: t('shorts'), value: r?.shorts ? fmtBig(r.shorts) : '0', color: up },
    ])
  },
}

/* ------------------------------------------------------------------------------------------------------------
 * Market sentiment (the same daily series on every symbol, 1D and above): Crypto Fear & Greed Index (0–100) and
 * the pressure of crypto YouTube influencers (a cumulative line with no fixed scale: its slope and turns matter). Each series is fetched whole (a few thousand
 * days) and refreshed every 10 minutes; a bar shows the last daily value inside it (nothing while it has none).
 * ---------------------------------------------------------------------------------------------------------- */
type SentKind = 'fng' | 'yt'
const sent = new Map<SentKind, { points: number[][]; at: number; ok: boolean; busy: Promise<void> | null }>()

async function loadSentiment(kind: SentKind, d: KLineData[], indicatorId: string): Promise<number[][] | null> {
  if (!d.length) return null
  const why = whyNot(kind === 'fng' ? 'FNG' : 'YTS')
  if (why) { lastStatus.set(indicatorId, why); return null }
  let e = sent.get(kind)
  if (!e) { e = { points: [], at: 0, ok: false, busy: null }; sent.set(kind, e) }
  const en = e
  if (!en.busy && Date.now() - en.at > (en.ok ? 600_000 : 30_000)) {
    en.at = Date.now()
    en.busy = api.sentiment(kind).then((r) => { en.points = r.points; en.ok = r.available }, () => {}).finally(() => { en.busy = null })
  }
  if (en.busy) await en.busy
  lastStatus.set(indicatorId, en.ok ? undefined : statusMsg('error'))
  return en.ok ? en.points : null
}

export const FNG_COLOR = '#f59e0b'
export const YTS_COLOR = '#e879f9'

function fngClass(v: number): string {
  return v < 25 ? 'Extreme fear' : v < 47 ? 'Fear' : v < 55 ? 'Neutral' : v < 76 ? 'Greed' : 'Extreme greed'
}

function sentimentTemplate(name: string, short: string, kind: SentKind, key: string, color: string, title: string,
  bands: number[], digits: number, classify?: (v: number) => string): IndicatorTemplate<Record<string, number | undefined>, number, unknown> {
  return {
    name, shortName: short, calcParams: bands, precision: digits,
    figures: [{ key, title: '', type: 'line', styles: () => ({ color }) }],
    calc: async (d, ind) => {
      const pts = await loadSentiment(kind, d, ind.id)
      if (!pts) return d.map(() => ({}))
      return lastInside(d, pts).map((v) => ({ [key]: v }))
    },
    shouldUpdate: (prev, cur) => ({ calc: JSON.stringify(prev.extendData) !== JSON.stringify(cur.extendData), draw: true }),
    createTooltipDataSource: ({ indicator, crosshair }) => {
      const rows = indicator.result as Array<Record<string, number | undefined>>
      const v = (rows[crosshair.dataIndex ?? rows.length - 1] ?? rows.at(-1))?.[key]
      const text = v === undefined ? '—' : `${v.toFixed(digits)}${classify ? ` · ${t(classify(v))}` : ''}`
      return legend(t(title), indicator.id, [{ title: '', value: text, color }])
    },
  }
}

export const FNG = sentimentTemplate('FNG', 'F&G', 'fng', 'fng', FNG_COLOR, 'Fear & Greed', [75, 25], 0, fngClass)
export const YTS = sentimentTemplate('YTS', 'Influencers', 'yt', 'yts', YTS_COLOR, 'Influencer pressure', [], 1)

/* ------------------------------------------------------------------------------------------------------------
 * Volume profile (visible range): volume at price split into buying (close ≥ open) and selling bars, drawn from
 * the right edge; the point of control is brighter, the 70 % value area is denser. Recomputed on every frame
 * from the visible bars (no server work; the calculation is shared with the fixed-range drawing tool: calc.ts).
 * ---------------------------------------------------------------------------------------------------------- */
export const VPVR: IndicatorTemplate<Record<string, never>, number, unknown> = {
  name: 'VPVR', shortName: 'VP', calcParams: [24], figures: [],
  calc: (d) => d.map(() => ({})),
  createTooltipDataSource: ({ indicator }) => ({ name: t('Volume profile'), calcParamsText: `(${indicator.calcParams[0]})`, features: [], legends: [] }),
  draw: ({ ctx, chart, indicator, bounding, yAxis }) => {
    const list = chart.getDataList()
    const vr = chart.getVisibleRange()
    const bars = list.slice(Math.max(0, Math.floor(vr.from)), Math.min(list.length, Math.ceil(vr.to)))
    const p = volumeProfile(bars, Number(indicator.calcParams[0]) || 24)
    if (!p) return true
    const { up: upC, down: dnC } = colors()
    const width = bounding.width * 0.22
    const right = bounding.width - 2
    ctx.save()
    for (let k = 0; k < p.rows; k++) {
      if (!p.total[k]) continue
      const y1 = yAxis.convertToPixel(p.lo + (k + 1) * p.step)
      const y2 = yAxis.convertToPixel(p.lo + k * p.step)
      const h = Math.max(1, Math.abs(y2 - y1) - 1)
      const top = Math.min(y1, y2)
      const inVa = k >= p.vaLo && k <= p.vaHi
      const wUp = (p.up[k] / p.max) * width
      const wDn = (p.dn[k] / p.max) * width
      const alpha = k === p.poc ? 0.6 : inVa ? 0.3 : 0.14
      ctx.fillStyle = hexAlpha(upC, alpha)
      ctx.fillRect(right - wUp - wDn, top, wUp, h)
      ctx.fillStyle = hexAlpha(dnC, alpha)
      ctx.fillRect(right - wDn, top, wDn, h)
    }
    ctx.restore()
    return true
  },
}

/* ------------------------------------------------------------------------------------------------------------
 * Previous day / week / month high & low (PDH/PDL, PWH/PWL, PMH/PML), drawn as steps with a label at the right
 * end. The levels come from the symbol's daily bars (/api/bars, 1D: a 1-minute chart does not hold a whole week),
 * fetched once per symbol and refreshed when a new day starts; older daily bars are fetched when the chart goes
 * further back. They do not widen the price scale (a monthly low far away must not squash the candles).
 * ---------------------------------------------------------------------------------------------------------- */
interface Daily {
  bars: DailyBar[]; ok: boolean; exhausted: boolean
  fetchedAt: number; retryAt: number; closedUntil: number; backoff: number
  lock: HistoryLock | null   // older daily bars are closed for now (sign in / history limit)
  dayChecked: number     // the day start that already caused a refetch (at most one per day)
  busy: Promise<void> | null
}
const dailyCache = new Map<string, Daily>()
const DAILY_PAGE = 400
const DAY_MS = 86_400_000

function mergeDaily(e: Daily, bars: Bar[]) {
  const m = new Map(e.bars.map((b) => [b.timestamp, b]))
  for (const b of bars) m.set(b[0], { timestamp: b[0], high: b[2], low: b[3] })
  e.bars = [...m.values()].sort((a, b) => a.timestamp - b.timestamp)
}

/** The newest daily bars, then older ones down to `needFrom`; at most one request at a time per symbol. */
async function ensureDaily(id: string, needFrom: number, newestBar: number, indicatorId: string): Promise<Daily> {
  let e = dailyCache.get(id)
  if (!e) {
    e = { bars: [], ok: false, exhausted: false, fetchedAt: 0, retryAt: 0, closedUntil: 0, backoff: 15_000, lock: null, dayChecked: 0, busy: null }
    dailyCache.set(id, e)
  }
  const en = e
  if (en.busy) { await en.busy; return en }
  const now = Date.now()
  if (now < en.retryAt) return en
  // the day of the newest chart bar began after the last fetch: yesterday's bar may have been unfinished then
  const last = en.bars.at(-1)
  const dayBegan = last ? Math.floor((newestBar - last.timestamp) / DAY_MS) * DAY_MS + last.timestamp : 0
  const newDay = en.fetchedAt < dayBegan + 60_000 && en.dayChecked !== dayBegan && now >= dayBegan + 60_000
  const stale = !en.ok || now - en.fetchedAt > 600_000 || newDay
  if (newDay) en.dayChecked = dayBegan
  const older = !stale && en.bars.length > 0 && needFrom < en.bars[0].timestamp && !en.exhausted && now >= en.closedUntil
  if (!stale && !older) return en
  const before = en.bars[0]?.timestamp ?? Infinity
  en.busy = (async () => {
    try {
      const r = await api.bars(id, '1D', older ? en.bars[0].timestamp : null, DAILY_PAGE)
      mergeDaily(en, r.bars)
      if (!older) en.fetchedAt = Date.now()
      // a page cut by the history guard says more=false too: that is a lock, not the first day of the symbol
      if ((older || !en.ok) && !r.more && !r.locked) en.exhausted = true
      if (r.locked) {
        en.lock = lockOf(r)
        en.closedUntil = closedUntil(en.lock)
        if (en.lock?.kind === 'stopped') window.setTimeout(() => recalc(indicatorId), Math.max(0, en.closedUntil - Date.now()) + 1000)
      } else if (older) en.lock = null
      en.ok = true
      en.backoff = 15_000
      // still older bars to get and this answer made progress: ask again shortly
      if (needFrom < (en.bars[0]?.timestamp ?? Infinity) && !en.exhausted && !r.locked && (en.bars[0]?.timestamp ?? Infinity) < before)
        window.setTimeout(() => recalc(indicatorId), 1200)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'slow') {
        en.retryAt = Date.now() + (err.retryAfter ?? 5) * 1000
        window.setTimeout(() => recalc(indicatorId), (err.retryAfter ?? 5) * 1000 + 200)
        return
      }
      const lock = lockOf(err)
      if (lock) {
        en.lock = lock
        en.closedUntil = closedUntil(lock)
        if (!en.ok) en.retryAt = en.closedUntil
        if (lock.kind === 'stopped') window.setTimeout(() => recalc(indicatorId), Math.max(0, en.closedUntil - Date.now()) + 1000)
        return
      }
      en.retryAt = Date.now() + en.backoff
      en.backoff = Math.min(en.backoff * 2, 120_000)
      window.setTimeout(() => recalc(indicatorId), en.backoff + 200)
    }
  })().finally(() => { en.busy = null })
  await en.busy
  return en
}

type LevelKey = keyof PeriodLevels
const LEVELS: Array<{ key: LevelKey; label: string; period: number; dash: number[] }> = [
  { key: 'pdh', label: 'PDH', period: 0, dash: [] },
  { key: 'pdl', label: 'PDL', period: 0, dash: [] },
  { key: 'pwh', label: 'PWH', period: 1, dash: [6, 4] },
  { key: 'pwl', label: 'PWL', period: 1, dash: [6, 4] },
  { key: 'pmh', label: 'PMH', period: 2, dash: [2, 3] },
  { key: 'pml', label: 'PML', period: 2, dash: [2, 3] },
]
const PERIOD_COLOR = ['#f59e0b', '#e879f9', '#a3a3a3']
const shown = (params: unknown[], period: number) => Number(params[period] ?? 1) !== 0

export const PDHL: IndicatorTemplate<PeriodLevels, number, unknown> = {
  name: 'PDHL', shortName: 'PDH/PDL', calcParams: [1, 1, 1], figures: [],
  calc: async (d, ind) => {
    const c = context()
    if (!c || !d.length) return d.map(() => ({}))
    const why = whyNot('PDHL')
    if (why) { lastStatus.set(ind.id, why); return d.map(() => ({})) }
    // the oldest chart bar needs the whole month before it
    const tz = useStore.getState().symbol?.timezone || 'UTC'
    const needFrom = levelsNeedFrom(d[0].timestamp, tz)
    const e = await ensureDaily(c.id, needFrom, d[d.length - 1].timestamp, ind.id)
    // older levels closed by the history guard: say why instead of leaving the old part of the chart blank
    const closed = e.lock && needFrom < (e.bars[0]?.timestamp ?? Infinity) && !(e.lock.kind === 'stopped' && e.lock.until <= Date.now())
    lastStatus.set(ind.id, !e.ok && !e.bars.length ? statusMsg('error')
      : closed ? t(e.lock!.kind === 'login' ? 'Sign in to see older history.' : 'The history limit is used up for now.') : undefined)
    return periodLevels(d.map((b) => b.timestamp), e.bars, tz, e.exhausted)
  },
  shouldUpdate: (prev, cur) => ({ calc: JSON.stringify(prev.extendData) !== JSON.stringify(cur.extendData), draw: true }),
  createTooltipDataSource: ({ indicator, crosshair }) => {
    const rows = indicator.result
    const r = rows[crosshair.dataIndex ?? rows.length - 1] ?? rows.at(-1) ?? {}
    const prec = live.chart?.getSymbol()?.pricePrecision ?? 2
    const legends: IndicatorTooltipData['legends'] = LEVELS.filter((l) => shown(indicator.calcParams, l.period) && r[l.key] !== undefined)
      .map((l) => ({ title: { text: `${l.label} `, color: PERIOD_COLOR[l.period] }, value: { text: r[l.key]!.toFixed(prec), color: PERIOD_COLOR[l.period] } }))
    // a note (closed older history, no data) goes after the values: the newest levels stay readable
    const note = lastStatus.get(indicator.id)
    if (note) legends.push({ title: '', value: { text: note, color: '#8b929c' } })
    return { name: t('Previous day / week / month'), calcParamsText: '', features: [], legends }
  },
  draw: ({ ctx, chart, indicator, bounding, xAxis, yAxis }) => {
    const rows = indicator.result
    if (!rows.length) return true
    const vr = chart.getVisibleRange()
    const from = Math.max(0, Math.floor(vr.from))
    const to = Math.min(rows.length, Math.ceil(vr.to))
    const half = (chart.getBarSpace().bar ?? 6) / 2
    ctx.save()
    ctx.lineWidth = 1
    ctx.font = '10px sans-serif'
    ctx.textBaseline = 'middle'
    const placed: Array<{ y: number; left: number; right: number }> = []
    for (const l of LEVELS) {
      if (!shown(indicator.calcParams, l.period)) continue
      const color = PERIOD_COLOR[l.period]
      ctx.strokeStyle = color
      ctx.setLineDash(l.dash)
      let i = from
      while (i < to) {
        const v = rows[i]?.[l.key]
        if (v === undefined) { i++; continue }
        let j = i
        while (j + 1 < to && rows[j + 1]?.[l.key] === v) j++
        const y = Math.round(yAxis.convertToPixel(v)) + 0.5
        if (y >= 0 && y <= bounding.height) {
          const x1 = xAxis.convertToPixel(i) - half
          const x2 = xAxis.convertToPixel(j) + half
          ctx.beginPath()
          ctx.moveTo(x1, y)
          ctx.lineTo(x2, y)
          ctx.stroke()
          // the label once, at the end of the newest step on screen; equal levels share a row side by side
          if (j + 1 >= to) {
            let right = Math.min(x2, bounding.width) - 2
            const w = ctx.measureText(l.label).width
            while (placed.some((q) => Math.abs(q.y - y) < 10 && right > q.left && right - w < q.right)) right = Math.min(...placed.filter((q) => Math.abs(q.y - y) < 10).map((q) => q.left)) - 6
            placed.push({ y, left: right - w, right })
            ctx.fillStyle = color
            ctx.textAlign = 'right'
            ctx.fillText(l.label, right, y - 7)
          }
        }
        i = j + 1
      }
    }
    ctx.restore()
    return true
  },
}

/* ------------------------------------------------------------------------------------------------------------
 * Trading sessions: Tokyo, London and New York in their local time (daylight saving included), each session a
 * box from its low to its high with the city's name. The hours are the parameters (9.5 = 09:30; "from" = "to"
 * hides a session). Computed from the chart's bars only.
 * ---------------------------------------------------------------------------------------------------------- */
const SESSION_SPECS = [
  { tz: 'Asia/Tokyo', label: 'Tokyo', color: '#f59e0b' },
  { tz: 'Europe/London', label: 'London', color: '#d946ef' },
  { tz: 'America/New_York', label: 'New York', color: '#14b8a6' },
] as const

interface SessionRow { s: number[] }

export const SESSIONS: IndicatorTemplate<SessionRow, number, unknown> = {
  name: 'SESSIONS', shortName: 'Sessions', calcParams: [9, 15.5, 8, 16.5, 9.5, 16], figures: [],
  calc: (d, ind) => {
    if (whyNot('SESSIONS')) return d.map(() => ({ s: [] }))
    const p = ind.calcParams.map(Number)
    const starts = sessionStarts(d.map((b) => b.timestamp), SESSION_SPECS.map((s, k) => ({ tz: s.tz, from: p[2 * k], to: p[2 * k + 1] })))
    return d.map((_, i) => ({ s: starts.map((col) => col[i]) }))
  },
  createTooltipDataSource: ({ indicator, crosshair }) => {
    const why = whyNot('SESSIONS')
    if (why) return { name: t('Sessions'), calcParamsText: '', features: [], legends: [{ title: '', value: { text: why, color: '#8b929c' } }] }
    const rows = indicator.result
    const r = rows[crosshair.dataIndex ?? rows.length - 1] ?? rows.at(-1)
    const now = SESSION_SPECS.filter((_, k) => (r?.s[k] ?? -1) >= 0)
    return {
      name: t('Sessions'), calcParamsText: '', features: [],
      legends: now.map((s) => ({ title: '', value: { text: t(s.label), color: s.color } })),
    }
  },
  draw: ({ ctx, chart, indicator, xAxis, yAxis }) => {
    const rows = indicator.result
    const list = chart.getDataList()
    if (!rows.length || rows.length !== list.length) return true
    const vr = chart.getVisibleRange()
    const from = Math.max(0, Math.floor(vr.from))
    const to = Math.min(rows.length, Math.ceil(vr.to))
    const half = (chart.getBarSpace().bar ?? 6) / 2
    const dark = useStore.getState().theme === 'dark'
    ctx.save()
    ctx.lineWidth = 1
    ctx.font = '10px sans-serif'
    ctx.textBaseline = 'top'
    ctx.textAlign = 'left'
    SESSION_SPECS.forEach((spec, k) => {
      const seen = new Set<number>()
      for (let i = from; i < to; i++) {
        const s = rows[i]?.s[k] ?? -1
        if (s < 0 || seen.has(s)) continue
        seen.add(s)
        // the whole session, also its part outside the screen (its high and low count)
        let e = s
        let hi = -Infinity
        let lo = Infinity
        while (e < rows.length && rows[e]?.s[k] === s) { hi = Math.max(hi, list[e].high); lo = Math.min(lo, list[e].low); e++ }
        const x1 = xAxis.convertToPixel(s) - half
        const x2 = xAxis.convertToPixel(e - 1) + half
        const y1 = yAxis.convertToPixel(hi)
        const y2 = yAxis.convertToPixel(lo)
        ctx.fillStyle = hexAlpha(spec.color, dark ? 0.1 : 0.08)
        ctx.fillRect(x1, y1, x2 - x1, y2 - y1)
        ctx.strokeStyle = hexAlpha(spec.color, 0.55)
        ctx.setLineDash([4, 3])
        ctx.strokeRect(Math.round(x1) + 0.5, Math.round(y1) + 0.5, Math.round(x2 - x1), Math.round(y2 - y1))
        // the city's name inside the box, top left (above the box it would run into the legend)
        if (y2 - y1 > 14 && x2 - x1 > 30) {
          ctx.fillStyle = spec.color
          ctx.fillText(t(spec.label), Math.max(x1, 0) + 3, y1 + 3)
        }
      }
    })
    ctx.restore()
    return true
  },
}
