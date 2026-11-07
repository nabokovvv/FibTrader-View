import type { KLineData, OverlayFigure, OverlayTemplate } from 'klinecharts'
import { useStore } from '../../state/store'
import { volumeProfile } from '../indicators/calc'
import { hexAlpha } from '../theme'

/**
 * Fixed range volume profile: two clicks pick a stretch of time; the volume at price of the bars between them is
 * drawn from the left edge of the stretch (rising bars in the up colour, falling in the down colour), with the
 * point of control (POC) and the 70 % value area (VAH / VAL) across the stretch. Only the times of the two points
 * matter. Recomputed from the chart's bars on every frame (live bars count; a bar replay shows what it has so far).
 */
const ROWS = 24

/** Index of the first bar opening at or after `t`. */
function lowerBound(list: KLineData[], t: number): number {
  let lo = 0
  let hi = list.length
  while (lo < hi) { const m = (lo + hi) >> 1; if (list[m].timestamp < t) lo = m + 1; else hi = m }
  return lo
}

const textStyle = (color: string) => ({
  color, size: 10, backgroundColor: 'transparent', borderSize: 0, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0,
})

export const fixedRangeVolumeProfile: OverlayTemplate = {
  name: 'fixedRangeVolumeProfile',
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: false,
  createPointFigures: ({ chart, coordinates, overlay, yAxis }) => {
    if (coordinates.length < 2 || !yAxis) return []
    const [p1, p2] = overlay.points
    if (typeof p1?.timestamp !== 'number' || typeof p2?.timestamp !== 'number') return []
    const list = chart.getDataList()
    const a = Math.min(p1.timestamp, p2.timestamp)
    const b = Math.max(p1.timestamp, p2.timestamp)
    const bars = list.slice(lowerBound(list, a), lowerBound(list, b + 1))
    const x1 = Math.min(coordinates[0].x, coordinates[1].x)
    const x2 = Math.max(coordinates[0].x, coordinates[1].x)
    const { upColor, downColor } = useStore.getState().settings
    const dark = useStore.getState().theme === 'dark'
    const edge = dark ? '#8b929c' : '#6b7079'
    const p = volumeProfile(bars, ROWS)
    if (!p) {
      // nothing to measure yet (a stretch without bars): show the stretch so it can be moved
      return [{ type: 'polygon', attrs: { coordinates: [{ x: x1, y: coordinates[0].y }, { x: x2, y: coordinates[0].y }, { x: x2, y: coordinates[1].y }, { x: x1, y: coordinates[1].y }] },
        styles: { style: 'stroke_fill', color: hexAlpha(edge, 0.08), borderColor: hexAlpha(edge, 0.6), borderSize: 1, borderStyle: 'dashed' } }]
    }
    const y = (v: number) => yAxis.convertToPixel(v)
    const yTop = y(p.hi)
    const yBot = y(p.lo)
    const w = x2 - x1
    const width = Math.max(w * 0.3, Math.min(w, 60))
    const figures: OverlayFigure[] = [
      // the stretch itself: the part that takes clicks (select, move)
      { type: 'polygon', attrs: { coordinates: [{ x: x1, y: yTop }, { x: x2, y: yTop }, { x: x2, y: yBot }, { x: x1, y: yBot }] },
        styles: { style: 'stroke_fill', color: hexAlpha(edge, 0.06), borderColor: hexAlpha(edge, 0.5), borderSize: 1, borderStyle: 'dashed' } },
    ]
    for (let k = 0; k < p.rows; k++) {
      if (!p.total[k]) continue
      const r1 = y(p.lo + (k + 1) * p.step)
      const r2 = y(p.lo + k * p.step)
      const h = Math.max(1, Math.abs(r2 - r1) - 1)
      const top = Math.min(r1, r2)
      const inVa = k >= p.vaLo && k <= p.vaHi
      const alpha = k === p.poc ? 0.7 : inVa ? 0.4 : 0.18
      const wUp = (p.up[k] / p.max) * width
      const wDn = (p.dn[k] / p.max) * width
      if (wUp > 0) figures.push({ type: 'rect', ignoreEvent: true, attrs: { x: x1, y: top, width: wUp, height: h }, styles: { style: 'fill', color: hexAlpha(upColor, alpha) } })
      if (wDn > 0) figures.push({ type: 'rect', ignoreEvent: true, attrs: { x: x1 + wUp, y: top, width: wDn, height: h }, styles: { style: 'fill', color: hexAlpha(downColor, alpha) } })
    }
    const prec = chart.getSymbol()?.pricePrecision ?? 2
    const line = (v: number, label: string, dash: boolean) => {
      const yy = Math.round(y(v)) + 0.5
      figures.push({ type: 'line', ignoreEvent: true, attrs: { coordinates: [{ x: x1, y: yy }, { x: x2, y: yy }] },
        styles: { style: dash ? 'dashed' : 'solid', dashedValue: [4, 3], size: 1, color: dash ? hexAlpha(edge, 0.9) : edge } })
      figures.push({ type: 'text', ignoreEvent: true, attrs: { x: x2 + 4, y: yy, text: `${label} ${v.toFixed(prec)}`, align: 'left', baseline: 'middle' }, styles: textStyle(edge) })
    }
    line(p.lo + (p.poc + 0.5) * p.step, 'POC', false)
    line(p.lo + (p.vaHi + 1) * p.step, 'VAH', true)
    line(p.lo + p.vaLo * p.step, 'VAL', true)
    return figures
  },
}
