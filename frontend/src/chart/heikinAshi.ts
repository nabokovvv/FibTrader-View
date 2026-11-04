import type { KLineData } from 'klinecharts'
import type { ChartType } from '../lib/types'

/* ------------------------------------------------------------------------------------------------------------
 * Heikin Ashi, like TradingView: the chart gets HA bars computed in the browser from the real ones, so candles,
 * indicators and the legend all show HA values. Each HA bar keeps the real OHLC in `real` (used where a real price
 * matters, e.g. whether a position hit its target or stop).
 *   close = (o + h + l + c) / 4,  open = (prev HA open + prev HA close) / 2 (first bar: (o + c) / 2),
 *   high = max(h, open, close),   low = min(l, open, close)
 * ---------------------------------------------------------------------------------------------------------- */
export const HEIKIN_ASHI: ChartType = 'heikin_ashi'
export const isHeikinAshi = (t: ChartType | undefined) => t === HEIKIN_ASHI

export interface RealOHLC { open: number; high: number; low: number; close: number }

/** The real OHLC of a chart bar (the bar itself unless it is a Heikin Ashi bar). */
export const realOf = (d: KLineData): RealOHLC => (d.real as RealOHLC | undefined) ?? d

/** One HA bar from a real bar and the HA bar before it (null: the first loaded bar). */
export function haBar(k: KLineData, prev: KLineData | null): KLineData {
  const r = realOf(k)
  const close = (r.open + r.high + r.low + r.close) / 4
  const open = prev ? (prev.open + prev.close) / 2 : (r.open + r.close) / 2
  return {
    ...k,
    open, close,
    high: Math.max(r.high, open, close),
    low: Math.min(r.low, open, close),
    real: { open: r.open, high: r.high, low: r.low, close: r.close },
  }
}

/** A chronological list of real bars as HA (older history loaded later starts its own chain: the HA open forgets
 *  its seed within a few bars, the same as on TradingView when more history is loaded). */
export function haList(list: KLineData[]): KLineData[] {
  const out: KLineData[] = []
  for (const k of list) out.push(haBar(k, out.at(-1) ?? null))
  return out
}

/** A live update (the forming bar, or a new one) as HA, chained to the bars already on the chart. */
export function haLive(k: KLineData, chartData: KLineData[]): KLineData {
  const n = chartData.length
  const last = chartData[n - 1]
  const prev = last && last.timestamp === k.timestamp ? chartData[n - 2] : last
  return haBar(k, prev ?? null)
}
