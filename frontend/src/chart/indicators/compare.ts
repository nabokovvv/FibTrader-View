import type { Chart, IndicatorTemplate, KLineData } from 'klinecharts'
import { useEffect, type RefObject } from 'react'
import { api } from '../../api/client'
import { t } from '../../i18n'
import { tickerOf, type IndicatorState, type Interval, type SymbolInfo } from '../../lib/types'
import { useStore } from '../../state/store'

/**
 * Compare: another symbol drawn over the chart as a line, rebased so both start together at the first visible bar
 * (the legend shows its % change from there). The rebase point follows scrolling. Up to MAX_COMPARE symbols.
 */
export const MAX_COMPARE = 3
export const COMPARE_COLORS = ['#f59e0b', '#c084fc', '#d7dbe0']  // orange, violet, light grey — no blue

interface Series { bars: Array<[number, number]>; more: boolean; fetchedAt: number; retryAt: number; backoff: number; busy: Promise<void> | null }
const cache = new Map<string, Series>()

function merge(s: Series, rows: number[][]) {
  const m = new Map(s.bars)
  for (const b of rows) m.set(b[0], b[4])
  s.bars = [...m.entries()].sort((a, b) => a[0] - b[0])
}

/**
 * Closes of `id` covering bars from `from` on: the newest page is refreshed every 30 s, at most 3 older pages per
 * call. Calls made while a load runs share it; after a failure (429 / 503) nothing is requested for 5 s → 1 min.
 */
async function ensure(id: string, iv: Interval, from: number): Promise<Series> {
  const key = `${id}|${iv}`
  let s = cache.get(key)
  if (!s) { s = { bars: [], more: true, fetchedAt: 0, retryAt: 0, backoff: 5000, busy: null }; cache.set(key, s) }
  const ser = s
  if (ser.busy) { await ser.busy; return ser }
  const now = Date.now()
  const stale = !ser.bars.length || now - ser.fetchedAt > 30_000
  const older = ser.more && ser.bars.length > 0 && ser.bars[0][0] > from
  if (now < ser.retryAt || (!stale && !older)) return ser
  ser.busy = (async () => {
    try {
      if (stale) {
        const r = await api.bars(id, iv, null, 1000)
        merge(ser, r.bars)
        ser.fetchedAt = Date.now()
        if (!r.bars.length || !r.more) ser.more = false
      }
      for (let i = 0; i < 3 && ser.more && ser.bars.length && ser.bars[0][0] > from; i++) {
        const r = await api.bars(id, iv, ser.bars[0][0], 1000)
        if (!r.bars.length) { ser.more = false; break }
        merge(ser, r.bars)
        ser.more = r.more
      }
      ser.backoff = 5000
    } catch {
      ser.retryAt = Date.now() + ser.backoff
      ser.backoff = Math.min(ser.backoff * 2, 60_000)
    }
  })().finally(() => { ser.busy = null })
  await ser.busy
  return ser
}

interface Ext { symbol?: string; label?: string; base?: number }
interface Row { v?: number; pct?: number }

export const COMPARE: IndicatorTemplate<Row, number, Ext> = {
  name: 'CMP',
  shortName: 'CMP',
  series: 'price',
  figures: [{ key: 'v', title: '', type: 'line' }],
  calc: async (dataList: KLineData[], indicator) => {
    const ext = (indicator.extendData ?? {}) as Ext
    if (!ext.symbol || !dataList.length) return dataList.map(() => ({}))
    const s = await ensure(ext.symbol, useStore.getState().interval, dataList[0].timestamp)
    const aligned: Array<number | undefined> = new Array(dataList.length)
    let j = 0
    let last: number | undefined
    for (let i = 0; i < dataList.length; i++) {
      while (j < s.bars.length && s.bars[j][0] <= dataList[i].timestamp) { last = s.bars[j][1]; j++ }
      aligned[i] = last
    }
    let bi = ext.base ? dataList.findIndex((d) => d.timestamp >= (ext.base as number)) : 0
    if (bi < 0) bi = 0
    while (bi < dataList.length && aligned[bi] === undefined) bi++
    if (bi >= dataList.length) return dataList.map(() => ({}))
    const mainBase = dataList[bi].close
    const cmpBase = aligned[bi] as number
    return aligned.map((c) => (c === undefined ? {} : { v: (c * mainBase) / cmpBase, pct: (c / cmpBase - 1) * 100 }))
  },
  // recalculate when the rebase point moves (extendData), not only on new params
  shouldUpdate: (prev, cur) => {
    const calc = JSON.stringify(prev.extendData) !== JSON.stringify(cur.extendData) || JSON.stringify(prev.calcParams) !== JSON.stringify(cur.calcParams)
    return { calc, draw: true }
  },
  createTooltipDataSource: ({ indicator, crosshair }) => {
    const ext = (indicator.extendData ?? {}) as Ext
    const rows = indicator.result as Row[]
    const r = rows[crosshair.dataIndex ?? rows.length - 1] ?? rows[rows.length - 1]
    const color = (indicator.styles?.lines?.[0]?.color as string | undefined) ?? COMPARE_COLORS[0]
    const pct = r?.pct
    return {
      name: '', calcParamsText: '', features: [],
      legends: [{ title: { text: `${ext.label ?? ext.symbol ?? ''} `, color }, value: { text: pct === undefined ? '—' : `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`, color } }],
    }
  },
}

export function addCompare(s: SymbolInfo) {
  const st = useStore.getState()
  const cmps = st.indicators.filter((i) => i.name === 'CMP')
  if (s.id === st.symbol?.id) { useStore.setState({ error: t('This is the symbol of the chart itself.') }); return }
  if (cmps.some((i) => i.options?.symbol === s.id)) return
  if (cmps.length >= MAX_COMPARE) { useStore.setState({ error: t('Up to {n} symbols can be compared at once.', { n: MAX_COMPARE }) }); return }
  st.addIndicator('CMP', true, undefined, { symbol: s.id, label: tickerOf(s) })
}

/** Options for createIndicator: the compared symbol and its colour (by order among compares). */
export function compareCreate(ind: IndicatorState, list: IndicatorState[]) {
  const idx = list.filter((i) => i.name === 'CMP').indexOf(ind)
  return {
    extendData: { symbol: ind.options?.symbol, label: ind.options?.label },
    styles: { lines: [{ color: COMPARE_COLORS[Math.max(0, idx) % COMPARE_COLORS.length], size: 1.5 }] },
  }
}

/** Keeps every compare rebased at the first visible bar while the chart scrolls. */
export function useCompareRebase(chartRef: RefObject<Chart | null>, indicators: IndicatorState[]) {
  const keys = indicators.filter((i) => i.name === 'CMP').map((i) => `${i.key}=${String(i.options?.symbol)}`).join(',')
  useEffect(() => {
    const chart = chartRef.current
    const cmps = useStore.getState().indicators.filter((i) => i.name === 'CMP')
    if (!chart || !cmps.length) return
    let raf = 0
    let lastBase = 0
    const apply = () => {
      raf = 0
      const list = chart.getDataList()
      const bar = list[Math.max(0, Math.floor(chart.getVisibleRange().from))]
      if (!bar || bar.timestamp === lastBase) return
      lastBase = bar.timestamp
      for (const c of cmps) chart.overrideIndicator({ id: c.key, name: 'CMP', extendData: { symbol: c.options?.symbol, label: c.options?.label, base: bar.timestamp } })
    }
    const onRange = () => { if (!raf) raf = window.requestAnimationFrame(apply) }
    chart.subscribeAction('onVisibleRangeChange', onRange)
    apply()
    return () => { chart.unsubscribeAction('onVisibleRangeChange', onRange); window.cancelAnimationFrame(raf) }
  }, [chartRef, keys])
}
