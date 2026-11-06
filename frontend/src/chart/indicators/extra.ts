import type { IndicatorTemplate, KLineData } from 'klinecharts'
import { api } from '../../api/client'
import { getLang, t } from '../../i18n'
import type { Interval } from '../../lib/types'
import { useStore } from '../../state/store'
import { hexAlpha } from '../theme'
import { live } from '../live'
import { barEnd } from '../periods'

/* ------------------------------------------------------------------------------------------------------------
 * Crypto panels (Bybit perpetuals): funding rate, open interest, liquidations. Data comes from /api/derivs,
 * cached here per (kind, symbol, interval); each calc makes at most one request (initial range, a fresh tail, or
 * older history) and asks for another calc while older history is still being filled in.
 * ---------------------------------------------------------------------------------------------------------- */
type Kind = 'funding' | 'oi' | 'liq'
type Status = 'interval' | 'not-recorded' | 'perp-only' | 'source' | 'error' | undefined
interface Entry {
  points: number[][]; coveredFrom: number; exhausted: boolean; status: Status
  ok: boolean            // one successful answer seen (the coverage cursor is valid)
  fetchedAt: number      // last successful newest-data fetch
  retryAt: number        // after a failure: no request before this time
  backoff: number
  busy: Promise<void> | null
}
const cache = new Map<string, Entry>()
const TTL: Record<Kind, number> = { funding: 300_000, oi: 60_000, liq: 60_000 }

function recalc(indicatorId: string) {
  const ind = live.chart?.getIndicators({ id: indicatorId })[0]
  if (ind) live.chart?.overrideIndicator({ id: indicatorId, name: ind.name, extendData: { rev: Date.now() } })
}

/** At most one request per calc and per series at a time; failures back off (15 s → 2 min). */
async function ensure(kind: Kind, id: string, iv: Interval, from: number, indicatorId: string): Promise<Entry> {
  const key = `${kind}|${id}|${iv}`
  let en = cache.get(key)
  if (!en) {
    en = { points: [], coveredFrom: Infinity, exhausted: false, status: undefined, ok: false, fetchedAt: 0, retryAt: 0, backoff: 15_000, busy: null }
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
  else if (from < e.coveredFrom && !e.exhausted) { range = [from, e.coveredFrom]; older = true }
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
      if (!older) e.fetchedAt = Date.now()
      e.ok = true
      e.backoff = 15_000
      e.status = r.unavailable === 'interval' ? 'interval' : r.unavailable ? 'source' : kind === 'liq' && r.recorded === false ? 'not-recorded' : undefined
      // more history is still missing and this answer made progress: ask again shortly
      if (from < e.coveredFrom && !e.exhausted && e.coveredFrom < before) window.setTimeout(() => recalc(indicatorId), 1200)
    } catch {
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

function context(): { id: string; iv: Interval; perp: boolean } | null {
  const st = useStore.getState()
  if (!st.symbol) return null
  return { id: st.symbol.id, iv: st.interval, perp: st.symbol.market === 'bybit' || st.symbol.market === 'hl' }
}

const statusText: Record<Exclude<Status, undefined>, string> = {
  interval: 'Open interest is available from the 5m interval.',
  'not-recorded': 'Liquidations are recorded for a fixed list: top 100 coins by market cap and top 50 stocks, ETFs and metals.',
  'perp-only': 'Available for perpetuals only.',
  source: 'This exchange does not publish liquidations.',
  error: 'No data right now.',
}
const lastStatus = new Map<string, Status>()  // indicator id → status shown in its legend

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
    legends: st ? [{ title: '', value: { text: t(statusText[st]), color: '#8b929c' } }]
      : parts.map((p) => ({ title: { text: `${p.title} `, color: p.color }, value: { text: p.value, color: p.color } })),
  }
}

const colors = () => {
  const s = useStore.getState().settings
  return { up: s.upColor, down: s.downColor }
}

async function load(kind: Kind, dataList: KLineData[], indicatorId: string): Promise<number[][] | null> {
  const c = context()
  if (!c || !dataList.length) return null
  if (!c.perp) { lastStatus.set(indicatorId, 'perp-only'); return null }
  const e = await ensure(kind, c.id, c.iv, dataList[0].timestamp, indicatorId)
  lastStatus.set(indicatorId, e.status)
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
 * Volume profile (visible range): volume at price split into buying (close ≥ open) and selling bars, drawn from
 * the right edge; the point of control is brighter, the 70 % value area is denser. Recomputed on every frame
 * from the visible bars (no server work).
 * ---------------------------------------------------------------------------------------------------------- */
export const VPVR: IndicatorTemplate<Record<string, never>, number, unknown> = {
  name: 'VPVR', shortName: 'VP', calcParams: [24], figures: [],
  calc: (d) => d.map(() => ({})),
  createTooltipDataSource: ({ indicator }) => ({ name: t('Volume profile'), calcParamsText: `(${indicator.calcParams[0]})`, features: [], legends: [] }),
  draw: ({ ctx, chart, indicator, bounding, yAxis }) => {
    const list = chart.getDataList()
    const vr = chart.getVisibleRange()
    const bars = list.slice(Math.max(0, Math.floor(vr.from)), Math.min(list.length, Math.ceil(vr.to)))
    const rows = Math.max(6, Math.min(80, Math.round(Number(indicator.calcParams[0]) || 24)))
    let lo = Infinity, hi = -Infinity
    for (const b of bars) { if (b.low < lo) lo = b.low; if (b.high > hi) hi = b.high }
    if (!bars.length || !(hi > lo)) return true
    const step = (hi - lo) / rows
    const up = new Array<number>(rows).fill(0)
    const dn = new Array<number>(rows).fill(0)
    for (const b of bars) {
      const v = b.volume ?? 0
      if (!(v > 0)) continue
      const a = Math.min(rows - 1, Math.floor((b.low - lo) / step))
      const z = Math.min(rows - 1, Math.floor((b.high - lo) / step))
      const share = v / (z - a + 1)
      const tgt = b.close >= b.open ? up : dn
      for (let k = a; k <= z; k++) tgt[k] += share
    }
    const tot = up.map((u, k) => u + dn[k])
    const max = Math.max(...tot)
    if (!(max > 0)) return true
    const poc = tot.indexOf(max)
    // value area: grow from the POC towards the bigger neighbour until 70 % of the volume
    const all = tot.reduce((s, v) => s + v, 0)
    let va = tot[poc], lo2 = poc, hi2 = poc
    while (va < all * 0.7 && (lo2 > 0 || hi2 < rows - 1)) {
      const below = lo2 > 0 ? tot[lo2 - 1] : -1
      const above = hi2 < rows - 1 ? tot[hi2 + 1] : -1
      if (above >= below) { hi2++; va += tot[hi2] } else { lo2--; va += tot[lo2] }
    }
    const { up: upC, down: dnC } = colors()
    const width = bounding.width * 0.22
    const right = bounding.width - 2
    ctx.save()
    for (let k = 0; k < rows; k++) {
      if (!tot[k]) continue
      const y1 = yAxis.convertToPixel(lo + (k + 1) * step)
      const y2 = yAxis.convertToPixel(lo + k * step)
      const h = Math.max(1, Math.abs(y2 - y1) - 1)
      const top = Math.min(y1, y2)
      const inVa = k >= lo2 && k <= hi2
      const wUp = (up[k] / max) * width
      const wDn = (dn[k] / max) * width
      const alpha = k === poc ? 0.6 : inVa ? 0.3 : 0.14
      ctx.fillStyle = hexAlpha(upC, alpha)
      ctx.fillRect(right - wUp - wDn, top, wUp, h)
      ctx.fillStyle = hexAlpha(dnC, alpha)
      ctx.fillRect(right - wDn, top, wDn, h)
    }
    ctx.restore()
    return true
  },
}
