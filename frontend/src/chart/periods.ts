import type { Period } from 'klinecharts'
import type { Interval } from '../lib/types'

export const PERIOD: Record<Interval, Period> = {
  '1m': { span: 1, type: 'minute' },
  '3m': { span: 3, type: 'minute' },
  '5m': { span: 5, type: 'minute' },
  '15m': { span: 15, type: 'minute' },
  '30m': { span: 30, type: 'minute' },
  '1h': { span: 1, type: 'hour' },
  '2h': { span: 2, type: 'hour' },
  '4h': { span: 4, type: 'hour' },
  '1D': { span: 1, type: 'day' },
  '1W': { span: 1, type: 'week' },
  '1M': { span: 1, type: 'month' },
}

export function intervalOf(period: Period): Interval {
  const found = (Object.keys(PERIOD) as Interval[]).find((k) => PERIOD[k].span === period.span && PERIOD[k].type === period.type)
  return found ?? '1h'
}

const MIN = 60_000
/** Bar length in ms (a month counts as 31 days: only used to spot missing bars). */
export const INTERVAL_MS: Record<Interval, number> = {
  '1m': MIN, '3m': 3 * MIN, '5m': 5 * MIN, '15m': 15 * MIN, '30m': 30 * MIN, '1h': 60 * MIN, '2h': 120 * MIN,
  '4h': 240 * MIN, '1D': 1440 * MIN, '1W': 7 * 1440 * MIN, '1M': 31 * 1440 * MIN,
}

/** When a bar of interval `iv` opened at `ts` closes (months by the calendar, in the bar's own time zone offset). */
export function barEnd(ts: number, iv: Interval): number {
  if (iv !== '1M') return ts + INTERVAL_MS[iv]
  const d = new Date(ts + 15 * 1440 * MIN)    // safely inside the bar's month whatever the exchange's offset
  const y = d.getUTCFullYear(), m = d.getUTCMonth()
  return Date.UTC(y, m + 1, 1) + (ts - Date.UTC(y, m, 1))
}

export const INTERVAL_LABEL: Record<Interval, string> = {
  '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m', '1h': '1h', '2h': '2h', '4h': '4h', '1D': 'D', '1W': 'W', '1M': 'M',
}
