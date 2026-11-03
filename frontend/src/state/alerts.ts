import { create } from 'zustand'
import { api, type Alert } from '../api/client'
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
  loaded: boolean
  draft: AlertDraft | null
  preset: number | null      // price picked on the chart (crosshair bell / right-click), used once by the form
  load: () => Promise<void>
  setDraft: (d: AlertDraft | null) => void
}

export const useAlerts = create<AlertsState>((set) => ({
  alerts: [],
  telegram: false,
  loaded: false,
  draft: null,
  preset: null,
  load: async () => {
    const r = await api.alerts()
    set({ alerts: r.alerts, telegram: r.telegram, loaded: true })
  },
  setDraft: (d) => set({ draft: d }),
}))

/** Open the alert form for the current symbol with `price` filled in. */
export function openAlertAt(price: number) {
  if (!Number.isFinite(price) || price <= 0) return
  useAlerts.setState({ draft: null, preset: price })
  useStore.getState().setSheet('alerts')
}

/** Markets with alerts on this server (empty = feature off). */
export const alertMarkets = () => serverFeatures().alerts

export const COND_LABEL: Record<Alert['cond'], string> = { cross: 'Crossing', up: 'Crossing up', down: 'Crossing down' }

export function alertTarget(a: Pick<Alert, 'level' | 'line'>, precision?: number): string {
  return a.line ? t('your line') : fmtPrice(a.level, precision)
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
      const a = fired[0]
      const name = idTicker(a.symbol)
      const more = fired.length > 1 ? ` (+${fired.length - 1})` : ''
      useStore.setState({ notice: `🔔 ${t('{name}: price {cond} {target}', { name, cond: t(COND_LABEL[a.cond]).toLowerCase(), target: alertTarget(a) })}${more}` })
    }
  }
  void check()
  const id = window.setInterval(() => void check(), 30_000)
  return () => { stopped = true; window.clearInterval(id) }
}
