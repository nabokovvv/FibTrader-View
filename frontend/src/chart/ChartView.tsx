import { useEffect, useRef, useState } from 'react'
import { cancelPendingSave } from './pendingSave'
import { dispose, init, type Chart, type IndicatorCreate, type KLineData, type Overlay, type OverlayCreate } from 'klinecharts'
import { ApiError, api } from '../api/client'
import { getLang, t } from '../i18n'
import { ws } from '../api/ws'
import type { Bar, DrawingRecord, Interval, SymbolInfo } from '../lib/types'
import { toolStyle, useStore } from '../state/store'
import { PERIOD, intervalOf } from './periods'
import { ALERT_FEATURE_ID, buildStyles } from './theme'
import { alertMarkets, openAlertAt, useAlerts } from '../state/alerts'
import { ALERT_LINES, ALERT_LINES_ID, type AlertLevel } from './indicators/alertLines'
import { replaceIndicatorExtend } from './extend'
import { fmtPrice } from '../components/ui'
import { Bell } from 'lucide-react'
import { registerCustomOverlays } from './overlays'
import { fibDefaults } from './overlays/fib'
import { POSITION_TOOLS, completePosition, positionDefaults } from './overlays/position'
import { INDICATOR_CATALOG, registerCustomIndicators } from './indicators'
import { compareCreate, useCompareRebase } from './indicators/compare'
import { live } from './live'
import { PLUGIN_HOOKS, pluginIndicator } from '../plugins'

registerCustomOverlays()

/** Drawings with a settings dialog (double-click or the gear in the selection toolbar). */
export const EDITABLE = new Set(['fibRetracement', 'longPosition', 'shortPosition'])

const toKLine = (b: Bar): KLineData => ({ timestamp: b[0], open: b[1], high: b[2], low: b[3], close: b[4], volume: b[5] })
const uid = () => `d_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`

/** Fields of an overlay we persist. Points keep timestamp+value only (dataIndex is load-dependent). */
function overlayData(o: Overlay): Record<string, unknown> {
  return {
    points: o.points.map((p) => ({ timestamp: p.timestamp, value: p.value })),
    styles: o.styles ?? undefined,
    lock: o.lock,
    visible: o.visible,
    mode: o.mode,
    extendData: o.extendData ?? undefined,
    groupId: o.groupId,
  }
}

export function ChartView() {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<Chart | null>(null)
  const symbolRef = useRef<SymbolInfo | null>(null)
  const unsubBarRef = useRef<(() => void) | null>(null)
  const createdIndicators = useRef<Map<string, { name: string; calcParams?: number[] }>>(new Map())
  const suppressRemove = useRef(false)
  const drawingId = useRef<string | null>(null)
  /** Undo stack for drawings: create → remove it; remove → restore it; move → put points back. */
  const undoStack = useRef<Array<{ type: 'create'; id: string } | { type: 'remove'; rec: DrawingRecord } | { type: 'move'; id: string; points: DrawingRecord['points'] }>>([])
  const moveStart = useRef<Map<string, DrawingRecord['points']>>(new Map())
  const measureId = useRef<string | null>(null)
  const clearMeasure = (chart: Chart) => {
    if (!measureId.current) return
    chart.removeOverlay({ id: measureId.current })
    measureId.current = null
  }

  const symbol = useStore((s) => s.symbol)
  const interval = useStore((s) => s.interval)
  const [loading, setLoading] = useState(false)
  const chartType = useStore((s) => s.chartType)
  const theme = useStore((s) => s.theme)
  const settings = useStore((s) => s.settings)
  const timezone = useStore((s) => s.timezone)
  const indicators = useStore((s) => s.indicators)
  const tool = useStore((s) => s.tool)
  const magnet = useStore((s) => s.magnet)

  // ------------------------------------------------------------- lifecycle
  useEffect(() => {
    const el = ref.current
    if (!el) return
    registerCustomIndicators()  // here, not at import: legend titles need the UI language, known only after start-up
    const st = useStore.getState()
    const chart = init(el, {
      locale: 'en-US',
      timezone: st.timezone,
      styles: buildStyles(st.theme, st.settings, st.chartType, !!st.symbol && alertMarkets().includes(st.symbol.market)),
      layout: { pane: { minHeight: 60 }, barSpaceLimit: { min: 1.5, max: 60 } },
    })
    if (!chart) return
    chartRef.current = chart
    chartHolder.chart = chart
    live.chart = chart
    createdIndicators.current.clear()
    ;(window as unknown as { __lvChart?: Chart }).__lvChart = chart
    chartHolder.suppressRemove = () => { suppressRemove.current = true }
    chartHolder.restoreRemove = () => { suppressRemove.current = false }
    chartHolder.undo = () => undo(chart)

    // bell next to the crosshair price label (mouse hover, or a tap on a phone): alert at that price
    chart.subscribeAction('onCrosshairFeatureClick', (data) => {
      const d = data as { crosshair?: { y?: number; paneId?: string }; feature?: { id?: string } } | undefined
      if (d?.feature?.id !== ALERT_FEATURE_ID || d.crosshair?.y == null) return
      const price = priceAt(chart, d.crosshair.paneId ?? 'candle_pane', d.crosshair.y)
      if (price != null) openAlertAt(price)
    })

    chart.setDataLoader({
      async getBars({ type, timestamp, symbol: sym, period, callback }) {
        const id = (sym as { id?: string }).id ?? symbolRef.current?.id
        const iv = intervalOf(period) as Interval
        if (!id || type === 'backward') { callback([], { forward: false, backward: false }); return }
        if (type === 'init') setLoading(true)
        // KLineChart does not drop answers for a symbol/period it already left: ignore them ourselves
        const stale = () => symbolRef.current?.id !== id || intervalOf(chartRef.current?.getPeriod() ?? period) !== iv
        try {
          const res = await withRetry(() => api.bars(id, iv, type === 'forward' && timestamp ? timestamp : null, type === 'init' ? 600 : 500), stale)
          if (stale()) return
          if (type === 'init') setLoading(false)
          callback(res.bars.map(toKLine), { forward: res.more, backward: false })
          if (type === 'init') {
            const last = res.bars.at(-1)
            if (last) useStore.getState().setLastBar({ ts: last[0], open: last[1], close: last[4] })
          }
        } catch (e) {
          if (stale()) return
          if (type === 'init') setLoading(false)
          const temporary = e instanceof ApiError && [429, 503].includes(e.status)
          useStore.setState({ error: temporary ? t('The server is busy. Retrying…') : (e as Error).message })
          callback([], { forward: false, backward: false })
          // a busy server is not the end of the history: reload this chart once things calm down
          if (temporary && type === 'init') window.setTimeout(() => { if (!stale()) chartRef.current?.resetData() }, 6000)
        }
      },
      subscribeBar({ symbol: sym, period, callback }) {
        const id = (sym as { id?: string }).id ?? symbolRef.current?.id
        if (!id) return
        unsubBarRef.current?.()
        unsubBarRef.current = ws.subscribeBars(id, intervalOf(period), (bar) => {
          callback(toKLine(bar))
          useStore.getState().setLastBar({ ts: bar[0], open: bar[1], close: bar[4] })
        })
      },
      unsubscribeBar() {
        unsubBarRef.current?.()
        unsubBarRef.current = null
      },
    })

    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(el)
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
      const s = useStore.getState()
      if (e.key === 'Escape') {
        clearMeasure(chart)
        if (drawingId.current) {
          suppressRemove.current = true
          chart.removeOverlay({ id: drawingId.current })
          suppressRemove.current = false
          drawingId.current = null
        }
        s.setTool(null)
        s.setSelectedOverlay(null)
        s.setSheet(null)
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' && !e.shiftKey) {
        e.preventDefault()
        undo(chart)
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && s.selectedOverlayId) {
        chart.removeOverlay({ id: s.selectedOverlayId })
        s.setSelectedOverlay(null)
      } else if (e.altKey && !e.metaKey && !e.ctrlKey) {
        const map: Record<string, string> = { t: 'segment', h: 'horizontalStraightLine', v: 'verticalStraightLine', f: 'fibRetracement', r: 'rayLine', p: 'priceLine' }
        const name = map[e.key.toLowerCase()]
        if (name) { e.preventDefault(); s.setTool(name) }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      ro.disconnect()
      unsubBarRef.current?.()
      dispose(el)
      chartRef.current = null
      chartHolder.chart = null
      live.chart = null
    }
  }, [])

  // ----------------------------------------------------------- symbol/period
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !symbol) return
    symbolRef.current = symbol
    suppressRemove.current = true
    chart.removeOverlay()
    suppressRemove.current = false
    drawingId.current = null
    undoStack.current = []
    chart.setSymbol({ ticker: symbol.display || symbol.symbol, pricePrecision: symbol.price_precision, volumePrecision: symbol.volume_precision, id: symbol.id, name: symbol.name })
    chart.setPeriod(PERIOD[interval])
    let cancelled = false
    withRetry(() => api.drawings(symbol.id), () => cancelled).then((list) => {
      if (cancelled || symbolRef.current?.id !== symbol.id) return
      for (const d of list) restoreOverlay(chart, symbol.id, d)
    }).catch(() => { if (!cancelled) useStore.setState({ error: t('Your drawings could not be loaded. Reload the page to try again.') }) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol?.id])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !symbol) return
    const cur = chart.getPeriod()
    const next = PERIOD[interval]
    if (!cur || cur.span !== next.span || cur.type !== next.type) chart.setPeriod(next)
  }, [interval, symbol])

  // ------------------------------------------------------------------ styles
  const alertButton = !!symbol && alertMarkets().includes(symbol.market)
  useEffect(() => {
    chartRef.current?.setStyles(buildStyles(theme, settings, chartType, alertButton))
  }, [theme, settings, chartType, alertButton])

  // right click on the price chart (mouse only; phones use the crosshair bell): "Add alert at <price>"
  const [menu, setMenu] = useState<{ x: number; y: number; price: number } | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const onContextMenu = (e: React.MouseEvent<HTMLDivElement>) => {
    const chart = chartRef.current
    if (!chart || !alertButton || !window.matchMedia('(pointer: fine)').matches) return
    const box = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - box.left
    const y = e.clientY - box.top
    const pane = chart.getSize('candle_pane')
    if (!pane || y < pane.top || y > pane.top + pane.height) return   // indicator panes: the browser menu
    const price = priceAt(chart, 'candle_pane', y - pane.top)
    if (price == null) return
    e.preventDefault()
    setMenu({ x: Math.max(4, Math.min(x, box.width - 230)), y: Math.max(4, Math.min(y, box.height - 52)), price })
  }
  useEffect(() => {
    if (!menu) return
    const close = (e: Event) => { if (!(e.target instanceof Node && menuRef.current?.contains(e.target))) setMenu(null) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenu(null) }
    document.addEventListener('mousedown', close)
    document.addEventListener('wheel', close, { passive: true })
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('wheel', close); document.removeEventListener('keydown', onKey) }
  }, [menu])
  useEffect(() => setMenu(null), [symbol, interval])

  // armed price alerts of this symbol: dashed lines with a bell label (alerts on drawings show as the drawing)
  const alerts = useAlerts((s) => s.alerts)
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const levels: AlertLevel[] = symbol
      ? alerts.filter((a) => a.active && a.symbol === symbol.id && a.level != null && !a.line && !a.drawing_id)
        .map((a) => ({ price: a.level as number, cond: a.cond }))
      : []
    const has = chart.getIndicators({ id: ALERT_LINES_ID }).length > 0
    if (!levels.length) {
      if (has) chart.removeIndicator({ id: ALERT_LINES_ID })
      return
    }
    if (has) replaceIndicatorExtend(chart, ALERT_LINES_ID, ALERT_LINES, { levels })
    else chart.createIndicator({ id: ALERT_LINES_ID, name: ALERT_LINES, paneId: 'candle_pane', extendData: { levels } }, true)
  }, [alerts, symbol])

  useEffect(() => {
    chartRef.current?.setTimezone(timezone)
  }, [timezone])

  // -------------------------------------------------------------- indicators
  function syncIndicators(chart: Chart, list: typeof indicators) {
    const created = createdIndicators.current
    const wanted = new Map(list.map((i) => [i.key, i]))
    for (const [key] of created) {
      if (!wanted.has(key)) {
        chart.removeIndicator({ id: key })
        created.delete(key)
      }
    }
    for (const ind of list) {
      const prev = created.get(ind.key)
      if (!prev) {
        const create: IndicatorCreate = { id: ind.key, name: ind.name }
        if (ind.calcParams?.length) create.calcParams = ind.calcParams
        const meta = INDICATOR_CATALOG.find((m) => m.name === ind.name)
        if (meta?.precision !== undefined) create.precision = meta.precision
        if (ind.main) create.paneId = 'candle_pane'
        const plugin = pluginIndicator(ind.name)
        if (plugin?.initialExtend) create.extendData = plugin.initialExtend(ind)
        if (ind.name === 'CMP') Object.assign(create, compareCreate(ind, list))
        const paneId = chart.createIndicator(create, ind.main)
        created.set(ind.key, { name: ind.name, calcParams: ind.calcParams })
        if (paneId && !ind.main) chart.setPaneOptions({ id: paneId, minHeight: 60, height: 120 })
      } else if (JSON.stringify(prev.calcParams) !== JSON.stringify(ind.calcParams)) {
        chart.overrideIndicator({ id: ind.key, name: ind.name, calcParams: ind.calcParams })
        created.set(ind.key, { name: ind.name, calcParams: ind.calcParams })
      }
    }
  }

  useEffect(() => {
    const chart = chartRef.current
    if (chart) syncIndicators(chart, indicators)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indicators])

  // ----------------------------------------------------------------- plugins
  // PLUGIN_HOOKS is fixed at build time, so the hook order never changes between renders.
  for (const usePluginChart of PLUGIN_HOOKS) usePluginChart(chartRef, indicators, symbol)
  useCompareRebase(chartRef, indicators)

  // ---------------------------------------------------------------- drawing
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !symbol) return
    if (drawingId.current) {
      suppressRemove.current = true
      chart.removeOverlay({ id: drawingId.current })
      suppressRemove.current = false
      drawingId.current = null
    }
    if (!tool) return
    const id = uid()
    drawingId.current = id
    if (tool === 'measure') {
      clearMeasure(chart)
      chart.createOverlay({
        id, name: 'measure', mode: 'normal',
        onDrawEnd: () => {
          if (drawingId.current === id) drawingId.current = null
          measureId.current = id
          useStore.getState().setTool(null)
          // as in common charting apps: the next click on the chart dismisses the measurement (attach after this click)
          window.setTimeout(() => ref.current?.addEventListener('pointerdown', () => clearMeasure(chart), { once: true, capture: true }), 0)
        },
      })
      return
    }
    const extendData = tool === 'fibRetracement' ? fibDefaults() : POSITION_TOOLS.has(tool) ? positionDefaults() : { saved: false }
    const ts = toolStyle(useStore.getState().settings, tool)
    const styles = ts ? { line: { color: ts.color, size: ts.size, style: 'solid' as const } } : undefined
    chart.createOverlay(withPersistence(symbol.id, { id, name: tool, mode: magnet, extendData, styles }, () => {
      if (drawingId.current === id) drawingId.current = null
      useStore.getState().setTool(null)
    }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tool])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    for (const o of chart.getOverlays()) chart.overrideOverlay({ id: o.id, mode: magnet })
  }, [magnet])

  // colour/width picked while the tool is armed apply to the pending drawing too
  const toolStyles = settings.toolStyles
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !tool || !drawingId.current) return
    const ts = toolStyle(useStore.getState().settings, tool)
    if (ts) chart.overrideOverlay({ id: drawingId.current, styles: { line: { color: ts.color, size: ts.size, style: 'solid' } } })
  }, [toolStyles, tool])

  // persistence helpers close over the chart via refs
  function withPersistence(symbolId: string, base: OverlayCreate, onDone?: () => void): OverlayCreate {
    const save = (o: Overlay) => {
      if (o.points.length < Math.max(1, o.totalStep - 1)) return
      void api.putDrawing(o.id, symbolId, o.name, overlayData(o)).catch(saveFailed)
    }
    const snapshot = (o: Overlay): DrawingRecord => ({ id: o.id, name: o.name, ...(overlayData(o) as Omit<DrawingRecord, 'id' | 'name'>) })
    return {
      ...base,
      onDrawEnd: (e) => {
        let o = e.overlay
        const chart = chartRef.current
        if (chart && POSITION_TOOLS.has(o.name)) {
          completePosition(chart, o.id, o.name === 'longPosition' ? 1 : -1)
          o = chart.getOverlays({ id: o.id })[0] ?? o
        }
        save(o); pushUndo({ type: 'create', id: o.id }); onDone?.()
      },
      onPressedMoveStart: (e) => { moveStart.current.set(e.overlay.id, e.overlay.points.map((p) => ({ timestamp: p.timestamp, value: p.value }))) },
      onPressedMoveEnd: (e) => {
        save(e.overlay)
        const before = moveStart.current.get(e.overlay.id)
        if (before) { pushUndo({ type: 'move', id: e.overlay.id, points: before }); moveStart.current.delete(e.overlay.id) }
      },
      onRemoved: (e) => {
        if (suppressRemove.current) return
        cancelPendingSave(e.overlay.id)  // a queued style save must not bring the drawing back
        void api.deleteDrawing(e.overlay.id).catch(() => { /* ignore */ })
        if (e.overlay.points.length >= Math.max(1, e.overlay.totalStep - 1)) pushUndo({ type: 'remove', rec: snapshot(e.overlay) })
        if (useStore.getState().selectedOverlayId === e.overlay.id) useStore.getState().setSelectedOverlay(null)
      },
      onSelected: (e) => useStore.getState().setSelectedOverlay(e.overlay.id),
      onDoubleClick: (e) => {
        if (EDITABLE.has(e.overlay.name)) useStore.getState().setEditOverlay(e.overlay.id)
        return false
      },
      onDeselected: (e) => {
        if (useStore.getState().selectedOverlayId === e.overlay.id) useStore.getState().setSelectedOverlay(null)
      },
    }
  }

  function pushUndo(action: (typeof undoStack.current)[number]) {
    undoStack.current.push(action)
    if (undoStack.current.length > 50) undoStack.current.shift()
  }

  function undo(chart: Chart) {
    const action = undoStack.current.pop()
    const sym = symbolRef.current
    if (!action || !sym) return
    if (action.type === 'create') {
      suppressRemove.current = true
      chart.removeOverlay({ id: action.id })
      suppressRemove.current = false
      void api.deleteDrawing(action.id).catch(() => { /* ignore */ })
    } else if (action.type === 'remove') {
      restoreOverlay(chart, sym.id, action.rec)
      void api.putDrawing(action.rec.id, sym.id, action.rec.name, { ...action.rec, id: undefined, name: undefined, updated_at: undefined }).catch(saveFailed)
    } else {
      chart.overrideOverlay({ id: action.id, points: action.points })
      const o = chart.getOverlays({ id: action.id })[0]
      if (o) void api.putDrawing(o.id, sym.id, o.name, overlayData(o)).catch(saveFailed)
    }
    useStore.getState().setSelectedOverlay(null)
  }

  function restoreOverlay(chart: Chart, symbolId: string, d: DrawingRecord) {
    // The built-in fibonacciLine was replaced by the configurable fibRetracement (same points convention).
    const migrate = d.name === 'fibonacciLine'
    if (migrate) d = { ...d, name: 'fibRetracement', extendData: fibDefaults() }
    if (migrate) void api.putDrawing(d.id, symbolId, d.name, { ...d, id: undefined, name: undefined, updated_at: undefined }).catch(() => { /* ignore */ })
    chart.createOverlay(withPersistence(symbolId, {
      id: d.id,
      name: d.name,
      points: d.points,
      styles: (d.styles as OverlayCreate['styles']) ?? undefined,
      lock: d.lock ?? false,
      visible: d.visible ?? true,
      mode: d.mode ?? 'normal',
      extendData: d.extendData,
      groupId: d.groupId,
    }))
  }

  return (
    <div className="relative h-full w-full" onContextMenu={onContextMenu}>
      <div ref={ref} className="h-full w-full" />
      {menu && (
        <div ref={menuRef} className="absolute z-40 min-w-[220px] rounded-lg border border-line bg-panel p-1 shadow-xl" style={{ left: menu.x, top: menu.y }}>
          <button className="tb-btn w-full !h-9 !justify-start text-[13px]" onClick={() => { openAlertAt(menu.price); setMenu(null) }}>
            <Bell />{t('Add alert at {price}', { price: fmtPrice(menu.price, symbol?.price_precision) })}
          </button>
        </div>
      )}
      {loading && (
        // previous symbol's candles stay in the canvas until the new ones arrive: cover them
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center" style={{ background: 'color-mix(in srgb, var(--bg) 82%, transparent)' }}>
          <span className="flex items-center gap-2 rounded-md border border-line bg-panel px-3 py-1.5 text-[12px] text-muted">
            <span className="pulse inline-block h-1.5 w-1.5 rounded-full" style={{ background: 'var(--warn)' }} />
            {t('Loading {what}', { what: `${symbol ? (symbol.display || symbol.symbol) : ''} · ${t(interval)}` })}
          </span>
        </div>
      )}
    </div>
  )
}

/** Temporary refusals (429 / 503) are retried twice with a short pause instead of ending the history. */
async function withRetry<T>(fn: () => Promise<T>, stale: () => boolean): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await fn()
    } catch (e) {
      if (attempt >= 2 || stale() || !(e instanceof ApiError) || ![429, 503].includes(e.status)) throw e
      await new Promise((r) => window.setTimeout(r, 1500 * (attempt + 1)))
    }
  }
}

/** A drawing was not saved: tell the user when it is a limit (otherwise it is retried on the next change). */
function saveFailed(e: Error) {
  if (e instanceof ApiError && [409, 413, 429, 507].includes(e.status)) {
    const reason = getLang() === 'en' ? e.detail : e.message
    useStore.setState({ error: t('Drawing not saved: {reason}', { reason }) })
  }
}

/** Persist an overlay's current state (used by toolbars and dialogs). */
export function saveOverlay(symbolId: string, o: Overlay) {
  return api.putDrawing(o.id, symbolId, o.name, overlayData(o)).catch(saveFailed)
}

/** The live chart instance, for toolbars that act on overlays imperatively. */
export const chartHolder: { chart: Chart | null; suppressRemove: () => void; restoreRemove: () => void; undo: () => void } = {
  chart: null,
  suppressRemove: () => {},
  restoreRemove: () => {},
  undo: () => {},
}


/** Price at a y pixel of a pane (pane coordinates). */
function priceAt(chart: Chart, paneId: string, y: number): number | null {
  const pts = chart.convertFromPixel([{ y }], { paneId }) as Array<{ value?: number }>
  const v = pts[0]?.value
  return v != null && Number.isFinite(v) && v > 0 ? v : null
}
