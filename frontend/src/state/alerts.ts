import { create } from 'zustand'
import { api, type Alert, type AlertMetric } from '../api/client'
import { serverFeatures } from '../auth/session'
import { t } from '../i18n'
import { fmtPrice } from '../components/ui'
import { idTicker } from '../lib/types'
import { useStore } from './store'

/** An alert being created from a drawing (bell in the selection toolbar). */
export interface AlertDraft { drawingId: string; drawingName: string; level?: number; line?: [number, number, number, number] }

interface AlertsState {
  alerts: Alert[]
  telegram: boolean
  metrics: AlertMetric[]     // what this server can watch (older servers: price only)
  loaded: boolean
  draft: AlertDraft | null
  preset: { value: number; metric: AlertMetric } | null  // picked on the chart (crosshair bell / right-click), used once by the form
  load: () => Promise<void>
  setDraft: (d: AlertDraft | null) => void
}

export const useAlerts = create<AlertsState>((set) => ({
  alerts: [],
  telegram: false,
  metrics: ['price'],
  loaded: false,
  draft: null,
  preset: null,
  load: async () => {
    const r = await api.alerts()
    set({ alerts: r.alerts, telegram: r.telegram, metrics: r.metrics ?? ['price'], loaded: true })
  },
  setDraft: (d) => set({ draft: d }),
}))

/** Open the alert form for the current symbol with `value` of `metric` (the price by default) filled in. */
export function openAlertAt(value: number, metric: AlertMetric = 'price') {
  if (!Number.isFinite(value) || ((metric === 'price' || metric === 'oi') && value <= 0)) return
  useAlerts.setState({ draft: null, preset: { value, metric } })
  useStore.getState().setSheet('alerts')
}

/** Indicator panels that can carry an alert, and on what (funding / OI: perpetuals only). */
export const INDICATOR_METRIC: Record<string, AlertMetric> = { FUNDING: 'funding', OI: 'oi', FNG: 'fng', YTS: 'yt' }

/** Markets with alerts on this server (empty = feature off). */
export const alertMarkets = () => serverFeatures().alerts

export const COND_LABEL: Record<Alert['cond'], string> = { cross: 'Crossing', up: 'Crossing up', down: 'Crossing down' }

export const METRIC_LABEL: Record<AlertMetric, string> = {
  price: 'Price', funding: 'Funding rate', oi: 'Open interest', fng: 'Fear & Greed Index', yt: 'Influencer pressure',
}
/** Funding and open interest exist for perpetuals only; the daily series are the same on every symbol. */
export const MARKET_METRICS: AlertMetric[] = ['fng', 'yt']

/** A value of a metric as shown to the user (funding in %, the others plain). */
export function fmtMetric(metric: AlertMetric | undefined, v: number | null | undefined, precision?: number): string {
  if (v == null || !Number.isFinite(v)) return '—'
  switch (metric ?? 'price') {
    case 'funding': return `${v.toFixed(4)}%`
    case 'oi': return v.toLocaleString('en-US', { maximumFractionDigits: 0 })
    case 'fng': return v.toFixed(0)
    case 'yt': return v.toFixed(1)
    default: return fmtPrice(v, precision)
  }
}

export function alertTarget(a: Pick<Alert, 'level' | 'line' | 'metric'>, precision?: number): string {
  return a.line ? t('your line') : fmtMetric(a.metric, a.level, precision)
}

/** "BTCUSDT: price crossed up 100" / "Fear & Greed Index crossed down 25" */
export function alertText(a: Alert): string {
  const m = a.metric ?? 'price'
  const cond = t(COND_LABEL[a.cond]).toLowerCase()
  if (MARKET_METRICS.includes(m)) return t('{what}: {cond} {target}', { what: t(METRIC_LABEL[m]), cond, target: alertTarget(a) })
  return t('{name}: {what} {cond} {target}', { name: idTicker(a.symbol), what: t(METRIC_LABEL[m]).toLowerCase(), cond, target: alertTarget(a) })
}

/**
 * While the site is open, armed alerts are re-read every 30 s; one that fired since the last look shows a notice
 * (Telegram gets it from the server either way). No polling at all while nothing is armed or the tab is hidden.
 */
export function startAlertWatcher(): () => void {
  if (!alertMarkets().length) return () => {}
  let stopped = false
  const check = async () => {
    const st = useAlerts.getState()
    if (stopped || document.hidden || (st.loaded && !st.alerts.some((a) => a.active))) return
    const armed = new Set(st.alerts.filter((a) => a.active).map((a) => a.id))
    try { await st.load() } catch { return }
    const fired = useAlerts.getState().alerts.filter((a) => armed.has(a.id) && !a.active && a.fired_at)
    if (fired.length) {
      const more = fired.length > 1 ? ` (+${fired.length - 1})` : ''
      useStore.setState({ notice: `🔔 ${alertText(fired[0])}${more}` })
    }
  }
  void check()
  const id = window.setInterval(() => void check(), 30_000)
  return () => { stopped = true; window.clearInterval(id) }
}
