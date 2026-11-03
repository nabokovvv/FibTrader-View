import type { Coordinate, OverlayFigure, OverlayTemplate } from 'klinecharts'
import { getLang, plural, t } from '../../i18n'
import { useStore } from '../../state/store'

/** Ruler (Measure): Δprice / %, bars & time span, volume. Transient — never saved. */

function rgba(hex: string, a: number) {
  const h = hex.replace('#', '')
  if (h.length !== 6) return hex
  const n = parseInt(h, 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
}

function span(ms: number): string {
  const ru = getLang() === 'ru'
  const u = ru ? { m: 'м', h: 'ч', d: 'д', M: 'мес', y: 'г' } : { m: 'm', h: 'h', d: 'd', M: 'M', y: 'y' }
  const m = Math.round(Math.abs(ms) / 60_000)
  if (m < 60) return `${m}${u.m}`
  const h = Math.floor(m / 60), mm = m % 60
  if (h < 24) return mm ? `${h}${u.h} ${mm}${u.m}` : `${h}${u.h}`
  const d = Math.floor(h / 24), hh = h % 24
  if (d < 60) return hh ? `${d}${u.d} ${hh}${u.h}` : `${d}${u.d}`
  const mo = Math.floor(d / 30.44)
  return d < 730 ? `${mo}${u.M} ${Math.round(d - mo * 30.44)}${u.d}` : `${(d / 365.25).toFixed(1)}${u.y}`
}

function big(v: number): string {
  const a = Math.abs(v)
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  if (a >= 1e3) return `${(v / 1e3).toFixed(2)}K`
  return v.toFixed(a < 10 ? 3 : 0)
}

function arrow(from: Coordinate, to: Coordinate, size = 6): Coordinate[][] {
  const ang = Math.atan2(to.y - from.y, to.x - from.x)
  const left = { x: to.x - size * Math.cos(ang - Math.PI / 6), y: to.y - size * Math.sin(ang - Math.PI / 6) }
  const right = { x: to.x - size * Math.cos(ang + Math.PI / 6), y: to.y - size * Math.sin(ang + Math.PI / 6) }
  return [[from, to], [left, to, right]]
}

export const measure: OverlayTemplate = {
  name: 'measure',
  totalStep: 3,
  needDefaultPointFigure: false,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ chart, coordinates, overlay }) => {
    if (coordinates.length < 2) return []
    const [p1, p2] = overlay.points
    const [c1, c2] = coordinates
    if (typeof p1?.value !== 'number' || typeof p2?.value !== 'number') return []
    const { upColor, downColor } = useStore.getState().settings
    const up = p2.value >= p1.value
    const color = up ? upColor : downColor
    const prec = chart.getSymbol()?.pricePrecision ?? 2
    const d = p2.value - p1.value
    const pct = p1.value !== 0 ? (d / p1.value) * 100 : 0
    const sign = d >= 0 ? '+' : '−'

    const i1 = p1.dataIndex ?? 0, i2 = p2.dataIndex ?? 0
    const bars = Math.abs(i2 - i1)
    const ms = (p2.timestamp ?? 0) - (p1.timestamp ?? 0)
    const data = chart.getDataList()
    let vol = 0
    for (let i = Math.max(0, Math.min(i1, i2)); i <= Math.min(data.length - 1, Math.max(i1, i2)); i++) vol += data[i]?.volume ?? 0

    const x0 = Math.min(c1.x, c2.x), x1 = Math.max(c1.x, c2.x)
    const y0 = Math.min(c1.y, c2.y), y1 = Math.max(c1.y, c2.y)
    const xm = (x0 + x1) / 2, ym = (y0 + y1) / 2
    const line = { style: 'solid', size: 1, color }
    const figures: OverlayFigure[] = [
      { type: 'polygon', ignoreEvent: true, attrs: { coordinates: [{ x: x0, y: y0 }, { x: x1, y: y0 }, { x: x1, y: y1 }, { x: x0, y: y1 }] }, styles: { style: 'fill', color: rgba(color, 0.16) } },
    ]
    // vertical arrow start price -> end price, horizontal arrow start time -> end time
    for (const seg of [...arrow({ x: xm, y: c1.y }, { x: xm, y: c2.y }), ...arrow({ x: c1.x, y: ym }, { x: c2.x, y: ym })]) {
      figures.push({ type: 'line', ignoreEvent: true, attrs: { coordinates: seg }, styles: line })
    }
    const lines = [
      `${sign}${Math.abs(d).toFixed(prec)} (${sign}${Math.abs(pct).toFixed(2)}%)`,
      `${plural(bars, ['bar', 'bars'], ['бар', 'бара', 'баров'])}, ${span(ms)}`,
      `${t('Vol')} ${big(vol)}`,
    ]
    const lh = 16
    const top = up ? y0 - 8 - lh * lines.length : y1 + 8
    const w = Math.max(...lines.map((t) => t.length)) * 7 + 20
    figures.push({
      type: 'rect', ignoreEvent: true,
      attrs: { x: xm - w / 2, y: top - 4, width: w, height: lh * lines.length + 8 },
      styles: { style: 'fill', color, borderRadius: 4 },
    })
    lines.forEach((text, i) => figures.push({
      type: 'text', ignoreEvent: true,
      attrs: { x: xm, y: top + i * lh + lh / 2, text, align: 'center', baseline: 'middle' },
      styles: { color: '#ffffff', size: 12, backgroundColor: 'transparent', borderSize: 0, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0, weight: i === 0 ? 600 : 400 },
    }))
    return figures
  },
}
