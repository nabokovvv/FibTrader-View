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

export const INTERVAL_LABEL: Record<Interval, string> = {
  '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m', '1h': '1h', '2h': '2h', '4h': '4h', '1D': 'D', '1W': 'W', '1M': 'M',
}
