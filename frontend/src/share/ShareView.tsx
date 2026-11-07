import { dispose, init, type IndicatorCreate, type KLineData, type OverlayCreate } from 'klinecharts'
import { useEffect, useRef, useState } from 'react'
import { ApiError, api, setShareAccess } from '../api/client'
import { HistoryLocked, Stale, historyClosed, historyFetch, setHistoryLock } from '../api/history'
import { HistoryNotice } from '../components/HistoryNotice'
import { ws } from '../api/ws'
import { INDICATOR_CATALOG, registerCustomIndicators } from '../chart/indicators'
import { levelProps, padParams } from '../chart/indicators/levels'
import { live } from '../chart/live'
import { registerCustomOverlays } from '../chart/overlays'
import { INTERVAL_MS, PERIOD } from '../chart/periods'
import { haList, haLive, isHeikinAshi } from '../chart/heikinAshi'
import { buildStyles } from '../chart/theme'
import { Logo } from '../components/Logo'
import { MarketBadge } from '../components/ui'
import { t } from '../i18n'
import { INTERVALS, tickerOf, type ChartType, type IndicatorState, type Interval, type SymbolInfo } from '../lib/types'
import { DEFAULT_SETTINGS, applyCandleVars, useStore } from '../state/store'

interface Snapshot {
  symbol: string; interval: Interval; chartType?: ChartType; theme?: 'dark' | 'light'; to?: number
  indicators?: IndicatorState[]; drawings?: Array<{ name: string; points: OverlayCreate['points']; styles?: unknown; extendData?: unknown }>
}

const toKLine = (b: number[]): KLineData => ({ timestamp: b[0], open: b[1], high: b[2], low: b[3], close: b[4], volume: b[5] })

/** A shared chart (/s/<id>): read-only, no sign-in, live candles, the author's indicators and drawings. */
export function ShareView({ id }: { id: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [info, setInfo] = useState<SymbolInfo | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setShareAccess(id)   // guests see the stretch around the shared moment too (not only the newest bars)
    api.share(id).then((r) => setSnap(r.state as unknown as Snapshot))
      .catch((e: unknown) => setErr(e instanceof ApiError && e.status === 404 ? t('This link has expired or was deleted.') : (e as Error).message))
    return () => setShareAccess(null)
  }, [id])

  useEffect(() => {
    const el = ref.current
    if (!snap || !el) return
    let chartMade = false
    let alive = true
    let unsub: (() => void) | null = null
    ws.connect()
    void (async () => {
      let sym: SymbolInfo
      try { sym = await api.symbol(snap.symbol) } catch (e) { setErr((e as Error).message); return }
      if (!alive) return
      setInfo(sym)
      const iv: Interval = INTERVALS.includes(snap.interval) ? snap.interval : '1h'
      const theme = snap.theme === 'light' ? 'light' : 'dark'
      document.documentElement.dataset.theme = theme
      useStore.setState({ symbol: sym, interval: iv, theme, settings: DEFAULT_SETTINGS })  // the crypto panels read these
      applyCandleVars(DEFAULT_SETTINGS)
      registerCustomOverlays()
      registerCustomIndicators()
      const chart = init(el, {
        locale: 'en-US', timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        styles: buildStyles(theme, DEFAULT_SETTINGS, snap.chartType ?? 'candle_solid'),
        layout: { pane: { minHeight: 60 }, barSpaceLimit: { min: 1.5, max: 60 } },
      })
      if (!chart) return
      chartMade = true
      live.chart = chart
      let scrolled = false
      const ha = isHeikinAshi(snap.chartType)
      // the author's last visible bar; when it is older than the newest page, the link opens there as a still
      // snapshot (bars up to that moment, no live feed) instead of today's chart with the moment out of reach
      const moment = typeof snap.to === 'number' && Number.isFinite(snap.to) ? snap.to : null
      const still = moment !== null && moment < Date.now() - 500 * INTERVAL_MS[iv]
      chart.setDataLoader({
        async getBars({ type, timestamp, callback }) {
          if (type === 'backward') { callback([], { forward: false, backward: false }); return }
          // older history closed for now (a guest: sign in): "more later", without asking the server again
          if (type === 'forward' && historyClosed()) { callback([], { forward: true, backward: false }); return }
          if (type === 'init') setHistoryLock(null)
          try {
            // older history: a guest gets the stretch around the shared moment, then a sign-in note (HistoryNotice)
            const to = type === 'forward' ? timestamp : still ? moment! + 1 : null
            const res = await historyFetch(() => api.bars(sym.id, iv, to, type === 'init' ? 600 : 500), () => !alive)
            const list = res.bars.map(toKLine)
            callback(ha ? haList(list) : list, { forward: res.more || !!res.locked, backward: false })
            if (type === 'init' && moment !== null && !still && !scrolled) { scrolled = true; window.setTimeout(() => chart.scrollToTimestamp(moment, 0), 0) }
          } catch (e) {
            if (e instanceof Stale || !alive) return
            // closed or failed: not the end of the history (the next scroll to the edge asks again once it opens)
            callback([], { forward: type === 'forward', backward: false })
            if (!(e instanceof HistoryLocked) && type === 'init') setErr((e as Error).message)
          }
        },
        subscribeBar({ callback }) {
          unsub?.()
          if (still) return            // a snapshot of the past: no live bars after it
          unsub = ws.subscribeBars(sym.id, iv, (bar) => callback(ha ? haLive(toKLine(bar), chart.getDataList()) : toKLine(bar)))
        },
        unsubscribeBar() { unsub?.(); unsub = null },
      })
      chart.setSymbol({ ticker: sym.display || sym.symbol, pricePrecision: sym.price_precision, volumePrecision: sym.volume_precision })
      chart.setPeriod(PERIOD[iv])
      // the snapshot is someone else's JSON: known indicators only, sane parameters
      const raw = Array.isArray(snap.indicators) ? snap.indicators : []
      const inds = raw.filter((i) => i && typeof i.name === 'string' && INDICATOR_CATALOG.some((m) => m.name === i.name)).slice(0, 16)
        .map((i, n) => ({ ...i, key: `s${n}`, calcParams: Array.isArray(i.calcParams) ? i.calcParams.slice(0, 8).map((p) => Math.max(-1000, Math.min(1000, Number(p) || 0))) : undefined }))
      for (const ind of inds) {
        const create: IndicatorCreate = { id: ind.key, name: ind.name }
        const meta = INDICATOR_CATALOG.find((m) => m.name === ind.name)
        const params = padParams(ind.calcParams, meta?.params)
        if (params?.length) create.calcParams = params
        if (meta?.precision !== undefined) create.precision = meta.precision
        Object.assign(create, levelProps(ind.name, params, meta?.params))
        if (ind.main) create.paneId = 'candle_pane'
        const paneId = chart.createIndicator(create, ind.main)
        if (paneId && !ind.main) chart.setPaneOptions({ id: paneId, minHeight: 60, height: 120 })
      }
      for (const d of (Array.isArray(snap.drawings) ? snap.drawings : []).slice(0, 300)) {
        if (!d || typeof d.name !== 'string' || !Array.isArray(d.points)) continue
        chart.createOverlay({ name: d.name, points: d.points, styles: d.styles as OverlayCreate['styles'], extendData: d.extendData, lock: true, mode: 'normal' })
      }
    })()
    const ro = new ResizeObserver(() => live.chart?.resize())
    ro.observe(el)
    return () => { alive = false; unsub?.(); ro.disconnect(); if (chartMade) dispose(el); live.chart = null }
  }, [snap])

  return (
    <div className="flex h-full flex-col bg-bg">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-panel px-3" style={{ paddingTop: 'var(--safe-top)' }}>
        <Logo className="h-[22px] w-[22px] text-text" />
        <span className="hidden text-[13px] font-semibold sm:inline">FibTrader View</span>
        <span className="mx-1 h-5 w-px bg-line" />
        {info && <span className="truncate text-[14px] font-semibold">{tickerOf(info)}</span>}
        {info && <MarketBadge market={info.market} currency={info.currency} />}
        {snap && INTERVALS.includes(snap.interval) && <span className="text-[12px] text-muted">{t(snap.interval)}</span>}
        <span className="hidden text-[11px] text-faint sm:inline">· {t('shared chart, read-only')}</span>
        <a className="tb-btn ml-auto !h-8 !px-3 text-[12px]" style={{ background: 'var(--brand)', color: '#fff' }}
          href={snap ? `/?symbol=${encodeURIComponent(snap.symbol)}` : '/'}>{t('Open in FibTrader View')}</a>
      </header>
      <main className="relative min-h-0 flex-1">
        <div ref={ref} className="h-full w-full" />
        <HistoryNotice />
        {(err || !snap) && (
          <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-[13px] text-muted">{err ?? t('Loading…')}</div>
        )}
      </main>
    </div>
  )
}
