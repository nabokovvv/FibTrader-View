export type Interval = '1m' | '3m' | '5m' | '15m' | '30m' | '1h' | '2h' | '4h' | '1D' | '1W' | '1M'
export const INTERVALS: Interval[] = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '1D', '1W', '1M']

export interface SymbolInfo {
  id: string
  market: string
  symbol: string
  name: string
  type: string
  exchange: string
  currency: string
  price_precision: number
  volume_precision: number
  timezone: string
  session: string
  aliases: string[]
  /** UI name (SPX for yahoo:^GSPC); the provider ticker stays in `symbol`. */
  display?: string
}

export const tickerOf = (s: Pick<SymbolInfo, 'symbol' | 'display'>) => s.display || s.symbol

/** [ts_ms, open, high, low, close, volume] */
export type Bar = [number, number, number, number, number, number]

export interface BarsResponse {
  id: string
  interval: Interval
  native: Interval
  more: boolean
  bars: Bar[]
}

export interface Quote {
  id: string
  p: number
  ch: number | null
  t: number | null
}

export interface Watchlist {
  id: number
  name: string
  items: string[]
  position: number
}

export type ChartType = 'candle_solid' | 'candle_stroke' | 'candle_up_stroke' | 'candle_down_stroke' | 'ohlc' | 'area'

export interface IndicatorState {
  key: string
  name: string
  main: boolean          // drawn on the price pane
  calcParams?: number[]
  paneId?: string
  /** Non-numeric settings of plugin indicators (modes, thresholds). */
  options?: Record<string, unknown>
}

export interface DrawingRecord {
  id: string
  name: string
  points: Array<{ timestamp?: number; dataIndex?: number; value?: number }>
  styles?: unknown
  lock?: boolean
  visible?: boolean
  mode?: 'normal' | 'weak_magnet' | 'strong_magnet'
  extendData?: unknown
  groupId?: string
  updated_at?: number
}

export const MARKET_LABEL: Record<string, string> = {
  bybit: 'Bybit',
  'bybit-spot': 'Bybit Spot',
  us: 'US',
  moex: 'MOEX',
  yahoo: 'Global',
}
