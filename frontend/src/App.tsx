import { List, Pencil, Search, SlidersHorizontal } from 'lucide-react'
import { t } from './i18n'
import { useEffect, useState } from 'react'
import { ws } from './api/ws'
import { DrawingToolbar } from './components/DrawingToolbar'
import { FibSettingsModal } from './components/FibSettingsModal'
import { PositionSettingsModal } from './components/PositionSettingsModal'
import { ChartView, chartHolder } from './chart/ChartView'
import { IndicatorModal } from './components/IndicatorModal'
import { SelectionToolbar } from './components/SelectionToolbar'
import { ToolOptionsBar } from './components/ToolOptionsBar'
import { SettingsModal } from './components/SettingsModal'
import { SymbolSearch } from './components/SymbolSearch'
import { TopBar } from './components/TopBar'
import { Watchlist } from './components/Watchlist'
import { useStore } from './state/store'
import { startAlertWatcher, useAlerts } from './state/alerts'
import { AlertsModal } from './components/AlertsModal'
import { ShareModal } from './components/ShareModal'
import { DonateModal } from './components/DonateModal'
import { FeedbackModal } from './components/FeedbackModal'
import { ReplayBar } from './components/ReplayBar'

function useViewport() {
  const get = () => (window.innerWidth < 760 ? 'phone' : window.innerWidth < 1100 ? 'tablet' : 'desktop') as 'phone' | 'tablet' | 'desktop'
  const [vp, setVp] = useState(get)
  useEffect(() => {
    const on = () => setVp(get())
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return vp
}

export default function App() {
  const editOverlayId = useStore((s) => s.editOverlayId)
  const setEditOverlay = useStore((s) => s.setEditOverlay)
  const ready = useStore((s) => s.ready)
  const error = useStore((s) => s.error)
  const sheet = useStore((s) => s.sheet)
  const setSheet = useStore((s) => s.setSheet)
  const showWatchlist = useStore((s) => s.showWatchlist)
  const bootstrap = useStore((s) => s.bootstrap)
  const setWsStatus = useStore((s) => s.setWsStatus)
  const setQuotes = useStore((s) => s.setQuotes)
  const vp = useViewport()
  const [addMode, setAddMode] = useState(false)

  useEffect(() => {
    void bootstrap()
    ws.connect()
    const offStatus = ws.onStatus(setWsStatus)
    const offQuotes = ws.onQuotes(setQuotes)
    const offErrors = ws.onError((m) => useStore.setState({ error: t(m) }))
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setAddMode(false); setSheet('search') }
    }
    window.addEventListener('keydown', onKey)
    return () => { offStatus(); offQuotes(); offErrors(); window.removeEventListener('keydown', onKey) }
  }, [bootstrap, setWsStatus, setQuotes, setSheet])

  useEffect(() => {
    if (error) {
      const t = window.setTimeout(() => useStore.setState({ error: null }), 6000)
      return () => window.clearTimeout(t)
    }
  }, [error])
  const notice = useStore((s) => s.notice)
  useEffect(() => {
    if (notice) {
      const t = window.setTimeout(() => useStore.setState({ notice: null }), 8000)
      return () => window.clearTimeout(t)
    }
  }, [notice])
  useEffect(() => (ready ? startAlertWatcher() : undefined), [ready])

  const phone = vp === 'phone'
  const watchlistDocked = !phone && showWatchlist

  const openAdd = () => { setAddMode(true); setSheet('search') }

  return (
    <div className="app-shell flex h-full flex-col" style={{ paddingLeft: 'var(--safe-left)', paddingRight: 'var(--safe-right)', paddingBottom: phone ? 'calc(56px + var(--safe-bottom))' : 'var(--safe-bottom)' }}>
      <TopBar compact={phone} narrow={vp === 'tablet'} />
      <div className="flex min-h-0 flex-1">
        {!phone && (
          <div className="min-h-0 w-[45px] shrink-0 border-r border-line bg-panel"><DrawingToolbar /></div>
        )}
        <main className="relative min-w-0 flex-1 bg-bg">
          {ready ? <ChartView /> : <div className="flex h-full items-center justify-center text-muted">{t('Loading…')}</div>}
          <SelectionToolbar />
          <ToolOptionsBar />
          {ready && <ReplayBar />}
        </main>
        {watchlistDocked && (
          <div className="sheet min-w-0 shrink-0" style={{ width: vp === 'tablet' ? 'min(300px, 32%)' : 290 }}><Watchlist onAdd={openAdd} /></div>
        )}
      </div>

      {phone && (
        <nav className="fixed inset-x-0 bottom-0 z-30 flex items-stretch border-t border-line bg-panel" style={{ height: 'calc(56px + var(--safe-bottom))', paddingBottom: 'var(--safe-bottom)', paddingLeft: 'var(--safe-left)', paddingRight: 'var(--safe-right)' }}>
          {[
            { key: 'tools', label: 'Draw', Icon: Pencil },
            { key: 'indicators', label: 'Indicators', Icon: SlidersHorizontal },
            { key: 'watchlist', label: 'Watchlist', Icon: List },
            { key: 'search', label: 'Search', Icon: Search },
          ].map(({ key, label, Icon }) => (
            <button key={key} className="flex flex-1 flex-col items-center justify-center gap-0.5 text-[11px]"
              style={{ color: sheet === key ? 'var(--accent)' : 'var(--muted)' }}
              onClick={() => { if (key === 'search') setAddMode(false); setSheet(sheet === key ? null : (key as typeof sheet)) }}>
              <Icon className="h-5 w-5" />{t(label)}
            </button>
          ))}
        </nav>
      )}

      {phone && sheet === 'tools' && (
        <div className="sheet fixed inset-x-0 z-30 border-t" style={{ left: 'var(--safe-left)', right: 'var(--safe-right)', bottom: 'calc(56px + var(--safe-bottom))', height: 56 }}>
          <DrawingToolbar horizontal />
        </div>
      )}
      {phone && sheet === 'watchlist' && (
        <div className="sheet slide-up fixed inset-x-0 z-30 border-t" style={{ left: 'var(--safe-left)', right: 'var(--safe-right)', bottom: 'calc(56px + var(--safe-bottom))', height: '60dvh' }}>
          <Watchlist onAdd={openAdd} />
        </div>
      )}

      {sheet === 'search' && <SymbolSearch mode={addMode ? 'add' : 'open'} onClose={() => { setSheet(null); setAddMode(false) }} />}
      {sheet === 'compare' && <SymbolSearch mode="compare" onClose={() => setSheet(null)} />}
      {sheet === 'alerts' && <AlertsModal onClose={() => { setSheet(null); useAlerts.getState().setDraft(null) }} />}
      {sheet === 'share' && <ShareModal onClose={() => setSheet(null)} />}
      {sheet === 'donate' && <DonateModal onClose={() => setSheet(null)} />}
      {sheet === 'feedback' && <FeedbackModal onClose={() => setSheet(null)} />}
      {sheet === 'indicators' && <IndicatorModal onClose={() => setSheet(null)} />}
      {sheet === 'settings' && <SettingsModal onClose={() => setSheet(null)} />}
      {editOverlayId && (chartHolder.chart?.getOverlays({ id: editOverlayId })[0]?.name === 'fibRetracement'
        ? <FibSettingsModal key={editOverlayId} overlayId={editOverlayId} onClose={() => setEditOverlay(null)} />
        : <PositionSettingsModal key={editOverlayId} overlayId={editOverlayId} onClose={() => setEditOverlay(null)} />)}

      {notice && !error && (
        <div role="status" className="fixed bottom-16 left-1/2 z-50 max-w-[92vw] -translate-x-1/2 rounded-lg border border-line bg-panel px-4 py-2 text-[12px] shadow-xl sm:bottom-4"
          onClick={() => useStore.setState({ notice: null })}>
          {notice}
        </div>
      )}
      {error && (
        <div className="fixed bottom-16 left-1/2 z-50 -translate-x-1/2 rounded-lg border border-line bg-panel px-4 py-2 text-[12px] shadow-xl sm:bottom-4" style={{ borderColor: 'var(--warn)' }}>
          {error}
        </div>
      )}
    </div>
  )
}
