import { Bell, Eye, EyeOff, Lock, LockOpen, Settings2, Trash2 } from 'lucide-react'
import { alertMarkets, useAlerts } from '../state/alerts'
import { scheduleSave } from '../chart/pendingSave'
import { t } from '../i18n'
import { useEffect, useState } from 'react'
import type { Overlay } from 'klinecharts'
import { api } from '../api/client'
import { EDITABLE, chartHolder } from '../chart/ChartView'
import { TOOLS } from '../chart/overlays'
import { hexAlpha } from '../chart/theme'
import { useStore } from '../state/store'
import { ColorPicker, WidthPicker } from './StylePickers'



/** Drawings an alert can follow (mirrors LEVEL_DRAWINGS / LINE_DRAWINGS on the server). */
const LEVEL_TOOLS = new Set(['horizontalStraightLine', 'horizontalRayLine', 'horizontalSegment', 'priceLine'])
const LINE_TOOLS = new Set(['segment', 'rayLine', 'straightLine'])

/** Floating toolbar for the selected drawing: colour, width, dash, alert, lock, hide, delete. */
export function SelectionToolbar() {
  const selectedId = useStore((s) => s.selectedOverlayId)
  const symbol = useStore((s) => s.symbol)
  const [, force] = useState(0)

  useEffect(() => { force((n) => n + 1) }, [selectedId])

  const chart = chartHolder.chart
  if (!selectedId || !chart || !symbol) return null
  const overlay = chart.getOverlays({ id: selectedId })[0] as Overlay | undefined
  if (!overlay) return null

  const st = (overlay.styles ?? {}) as Record<string, Record<string, unknown>>
  const curColor = (st.line?.color as string | undefined) ?? '#f1e9d6'
  const curSize = (st.line?.size as number | undefined) ?? 1
  const dashed = st.line?.style === 'dashed'
  const meta = TOOLS.find((t) => t.name === overlay.name)

  const persist = (o: Overlay) => {
    // colour drags fire many events: save once the user pauses
    scheduleSave(o.id, () => save(o))
  }
  const save = (o: Overlay) => {
    void api.putDrawing(o.id, symbol.id, o.name, {
      points: o.points.map((p) => ({ timestamp: p.timestamp, value: p.value })),
      styles: o.styles ?? undefined, lock: o.lock, visible: o.visible, mode: o.mode,
      extendData: o.extendData ?? undefined, groupId: o.groupId,
    }).catch(() => { /* ignore */ })
  }

  const applyStyle = (patch: { color?: string; size?: number; style?: 'solid' | 'dashed' }) => {
    const color = patch.color ?? curColor
    const size = patch.size ?? curSize
    const style = patch.style ?? (dashed ? 'dashed' : 'solid')
    const styles = {
      line: { color, size, style, dashedValue: [6, 4] },
      point: { color, activeColor: color, borderColor: hexAlpha(color, 0.35), activeBorderColor: hexAlpha(color, 0.35) },
      polygon: { color: hexAlpha(color, 0.14), borderColor: color, borderSize: size },
      rect: { color: hexAlpha(color, 0.14), borderColor: color, borderSize: size },
      circle: { color: hexAlpha(color, 0.14), borderColor: color, borderSize: size },
      arc: { color, size },
      text: { backgroundColor: color },
    }
    chart.overrideOverlay({ id: overlay.id, styles })
    const o = chart.getOverlays({ id: overlay.id })[0]
    if (o) persist(o)
    force((n) => n + 1)
  }

  const toggle = (patch: { lock?: boolean; visible?: boolean }) => {
    chart.overrideOverlay({ id: overlay.id, ...patch })
    const o = chart.getOverlays({ id: overlay.id })[0]
    if (o) persist(o)
    force((n) => n + 1)
  }

  const remove = () => {
    chart.removeOverlay({ id: overlay.id })   // onRemoved handler deletes it server-side
    useStore.getState().setSelectedOverlay(null)
  }

  return (
    <div className="pointer-events-auto absolute left-1/2 top-2 z-30 flex -translate-x-1/2 items-center gap-1 rounded-lg border border-line bg-panel px-2 py-1 shadow-xl">
      <span className="mr-1 hidden text-[12px] text-muted sm:inline">{meta ? t(meta.label) : overlay.name}</span>
      {EDITABLE.has(overlay.name) ? (
        <button className="tb-btn" onClick={() => useStore.getState().setEditOverlay(overlay.id)} title={t('Settings (double-click the drawing)')}><Settings2 /><span className="text-[12px]">{t('Settings')}</span></button>
      ) : (<>
      <ColorPicker value={curColor} onChange={(color) => applyStyle({ color })} />
      <WidthPicker value={curSize} onChange={(size) => applyStyle({ size })} />
      {overlay.name !== 'brush' && <button className="tb-btn !min-w-7 !px-1" data-active={dashed} onClick={() => applyStyle({ style: dashed ? 'solid' : 'dashed' })} aria-label={t('Dashed')}>
        <span className="block w-4 border-t border-dashed border-current" />
      </button>}
      </>)}
      <div className="mx-1 h-5 w-px bg-line" />
      {alertMarkets().includes(symbol.market) && (LEVEL_TOOLS.has(overlay.name) || LINE_TOOLS.has(overlay.name)) && overlay.points.length >= (LINE_TOOLS.has(overlay.name) ? 2 : 1) && (
        <button className="tb-btn" title={t('Alert on this line')} onClick={() => {
          const pts = overlay.points.map((p) => [p.timestamp ?? 0, p.value ?? 0] as [number, number]).sort((a, b) => a[0] - b[0])
          const line = LINE_TOOLS.has(overlay.name) && pts[0][0] !== pts[1][0] ? [pts[0][0], pts[0][1], pts[1][0], pts[1][1]] as [number, number, number, number] : undefined
          useAlerts.getState().setDraft({ drawingId: overlay.id, drawingName: overlay.name, ...(line ? { line } : { level: pts[0][1] }) })
          useStore.getState().setSheet('alerts')
        }}><Bell /></button>
      )}
      <button className="tb-btn" onClick={() => toggle({ lock: !overlay.lock })} title={t(overlay.lock ? 'Unlock' : 'Lock')}>{overlay.lock ? <Lock /> : <LockOpen />}</button>
      <button className="tb-btn" onClick={() => toggle({ visible: !overlay.visible })} title={t(overlay.visible ? 'Hide' : 'Show')}>{overlay.visible ? <Eye /> : <EyeOff />}</button>
      <button className="tb-btn" onClick={remove} title={t('Delete (Del)')}><Trash2 /></button>
    </div>
  )
}
