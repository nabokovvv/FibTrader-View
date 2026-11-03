import type { Chart, KLineData, OverlayFigure, OverlayTemplate, Point } from 'klinecharts'
import { t as tr } from '../../i18n'
import { useStore } from '../../state/store'

/**
 * Long / Short position tool.
 * Points: 0 = entry (left edge), 1 = target (left edge), 2 = stop (left edge), 3 = right edge (at entry price).
 * Placed with one click; ChartView fills in target/stop/width (completePosition).
 */
export interface PositionConfig {
  accountSize: number
  riskPct: number        // % of account risked at the stop
  qtyPrecision: number
}

export const DEFAULT_POSITION: PositionConfig = { accountSize: 1000, riskPct: 1, qtyPrecision: 3 }
let userDefaults: PositionConfig | null = null
export function setPositionDefaults(c: Partial<PositionConfig> | null | undefined) { userDefaults = c ? normalizePosition(c) : null }
export function positionDefaults(): PositionConfig { return { ...(userDefaults ?? DEFAULT_POSITION) } }
export function normalizePosition(raw: unknown): PositionConfig {
  const c = (raw && typeof raw === 'object' ? raw : {}) as Partial<PositionConfig>
  const n = (v: unknown, d: number) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : d)
  return {
    accountSize: n(c.accountSize, DEFAULT_POSITION.accountSize),
    riskPct: n(c.riskPct, DEFAULT_POSITION.riskPct),
    qtyPrecision: Math.min(8, Math.max(0, Math.round(Number(c.qtyPrecision ?? DEFAULT_POSITION.qtyPrecision)))),
  }
}

export const POSITION_TOOLS = new Set(['longPosition', 'shortPosition'])

function rgba(hex: string, a: number) {
  const h = hex.replace('#', '')
  if (h.length !== 6) return hex
  const n = parseInt(h, 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
}

const PERIOD_MS: Record<string, number> = { second: 1e3, minute: 6e4, hour: 36e5, day: 864e5, week: 6048e5, month: 2592e6, year: 31536e6 }

/** After the one-click placement: target 2R away, stop at ~8% of the visible price range, ~30 bars wide. */
export function completePosition(chart: Chart, id: string, side: 1 | -1) {
  const o = chart.getOverlays({ id })[0]
  const p0 = o?.points[0]
  if (!o || !p0 || typeof p0.value !== 'number' || typeof p0.timestamp !== 'number' || o.points.length >= 4) return
  const data = chart.getDataList()
  const vr = chart.getVisibleRange()
  const vis = data.slice(Math.max(0, vr.from), Math.max(vr.from + 1, vr.to))
  const hi = Math.max(...vis.map((d) => d.high)), lo = Math.min(...vis.map((d) => d.low))
  const risk = Number.isFinite(hi - lo) && hi > lo ? (hi - lo) * 0.08 : p0.value * 0.01
  const per = chart.getPeriod()
  const barMs = per ? (PERIOD_MS[per.type] ?? 6e4) * per.span : 6e4
  const right = p0.timestamp + 30 * barMs
  chart.overrideOverlay({
    id,
    points: [
      { timestamp: p0.timestamp, value: p0.value },
      { timestamp: p0.timestamp, value: p0.value + side * 2 * risk },
      { timestamp: p0.timestamp, value: p0.value - side * risk },
      { timestamp: right, value: p0.value },
    ],
  })
}

interface Outcome { status: 'open' | 'tp' | 'sl'; price: number; ts: number }

/** Replays bars after the entry bar inside the box: first touch of target or stop (both in one bar = stop). */
function outcome(data: KLineData[], side: 1 | -1, t: number, s: number, tsL: number, tsR: number): Outcome | null {
  let last: KLineData | null = null
  for (const b of data) {
    if (b.timestamp <= tsL) continue
    if (b.timestamp > tsR) break
    last = b
    const hitS = side === 1 ? b.low <= s : b.high >= s
    const hitT = side === 1 ? b.high >= t : b.low <= t
    if (hitS) return { status: 'sl', price: s, ts: b.timestamp }
    if (hitT) return { status: 'tp', price: t, ts: b.timestamp }
  }
  return last ? { status: 'open', price: last.close, ts: last.timestamp } : null
}

function makePosition(name: 'longPosition' | 'shortPosition', side: 1 | -1): OverlayTemplate<PositionConfig> {
  return {
    name,
    totalStep: 2,
    needDefaultPointFigure: true,
    needDefaultXAxisFigure: true,
    needDefaultYAxisFigure: true,
    // keep the handles tied together: target/stop handles sit on the left edge, the width handle on the entry price
    performEventPressedMove: ({ points, performPointIndex }) => {
      if (points.length < 4) return
      const [p0, p1, p2, p3] = points as Array<Partial<Point>>
      const e = p0.value ?? 0
      if (performPointIndex === 0) {
        for (const p of [p1, p2]) { p.timestamp = p0.timestamp; p.dataIndex = p0.dataIndex }
        p3.value = e
      } else if (performPointIndex === 1 || performPointIndex === 2) {
        const p = performPointIndex === 1 ? p1 : p2
        p.timestamp = p0.timestamp; p.dataIndex = p0.dataIndex
        const wantAbove = (performPointIndex === 1) === (side === 1)
        const v = p.value ?? e
        const eps = Math.abs(e) * 1e-6 || 1e-9
        p.value = wantAbove ? Math.max(v, e + eps) : Math.min(v, e - eps)
      } else if (performPointIndex === 3) {
        p3.value = e
      }
    },
    createPointFigures: ({ chart, coordinates, overlay }) => {
      if (coordinates.length < 4) return []
      const pts = overlay.points
      const [e, t, s] = [pts[0].value, pts[1].value, pts[2].value]
      const ta = pts[0].timestamp, tz = pts[3].timestamp
      const tsL = typeof ta === 'number' && typeof tz === 'number' ? Math.min(ta, tz) : ta
      const tsR = typeof ta === 'number' && typeof tz === 'number' ? Math.max(ta, tz) : tz
      if (typeof e !== 'number' || typeof t !== 'number' || typeof s !== 'number') return []
      const cfg = normalizePosition(overlay.extendData)
      const { upColor, downColor } = useStore.getState().settings
      const prec = chart.getSymbol()?.pricePrecision ?? 2
      const fmt = (v: number) => v.toFixed(prec)
      const money = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
      const xL = Math.min(coordinates[0].x, coordinates[3].x), xR = Math.max(coordinates[0].x, coordinates[3].x)
      const yE = coordinates[0].y, yT = coordinates[1].y, yS = coordinates[2].y
      const xMid = (xL + xR) / 2
      const rect = (y0: number, y1: number, color: string, x1 = xR): OverlayFigure => ({
        type: 'polygon',
        attrs: { coordinates: [{ x: xL, y: y0 }, { x: x1, y: y0 }, { x: x1, y: y1 }, { x: xL, y: y1 }] },
        styles: { style: 'fill', color },
      })
      const figures: OverlayFigure[] = [
        rect(yE, yT, rgba(upColor, 0.18)),
        rect(yE, yS, rgba(downColor, 0.18)),
      ]

      const dT = Math.abs(t - e), dS = Math.abs(e - s)
      const qty = dS > 0 ? (cfg.accountSize * cfg.riskPct / 100) / dS : 0
      const res = typeof tsL === 'number' && typeof tsR === 'number' ? outcome(chart.getDataList(), side, t, s, tsL, tsR) : null
      if (res) {
        const xr = Math.min(xR, (chart.convertToPixel({ timestamp: res.ts }, { paneId: 'candle_pane' }) as { x?: number }).x ?? xR)
        const yr = (chart.convertToPixel({ value: res.price }, { paneId: 'candle_pane' }) as { y?: number }).y ?? yE
        const win = side * (res.price - e) >= 0
        figures.push(rect(yE, yr, rgba(win ? upColor : downColor, 0.32), Math.max(xL, xr)))
        figures.push({ type: 'line', ignoreEvent: true, attrs: { coordinates: [{ x: xL, y: yE }, { x: Math.max(xL, xr), y: yr }] }, styles: { style: 'dashed', dashedValue: [3, 3], size: 1, color: rgba('#d7dbe0', 0.7) } })
      }
      figures.push({ type: 'line', attrs: { coordinates: [{ x: xL, y: yE }, { x: xR, y: yE }] }, styles: { style: 'solid', size: 1, color: '#9598a1' } })

      const box = (y: number, text: string, bg: string, baseline: 'top' | 'bottom' | 'middle'): OverlayFigure => ({
        type: 'text', ignoreEvent: true,
        attrs: { x: xMid, y, text, align: 'center', baseline },
        styles: { color: '#ffffff', size: 11, backgroundColor: bg, borderSize: 0, borderRadius: 3, paddingLeft: 6, paddingRight: 6, paddingTop: 3, paddingBottom: 3 },
      })
      const above = (y: number) => [y - 4, 'bottom'] as const
      const below = (y: number) => [y + 4, 'top'] as const
      const [ty, tb] = side === 1 ? above(Math.min(yT, yE)) : below(Math.max(yT, yE))
      const [sy, sb] = side === 1 ? below(Math.max(yS, yE)) : above(Math.min(yS, yE))
      figures.push(box(ty, `${tr('Target')}: ${fmt(t)} (${(dT / e * 100).toFixed(2)}%) ${fmt(dT)}, ${tr('Amount')}: ${money(qty * dT)}`, upColor, tb))
      figures.push(box(sy, `${tr('Stop')}: ${fmt(s)} (${(dS / e * 100).toFixed(2)}%) ${fmt(dS)}, ${tr('Amount')}: ${money(qty * dS)}`, downColor, sb))

      const pnl = res ? side * (res.price - e) * qty : 0
      const state = tr(!res ? 'Not started' : res.status === 'open' ? 'Open P&L' : res.status === 'tp' ? 'Closed P&L (target)' : 'Closed P&L (stop)')
      const pnlBg = !res ? '#5c636d' : pnl >= 0 ? upColor : downColor
      const inner = side === 1 ? 1 : -1   // put the two info lines inside the target zone near the entry
      figures.push(box(yE - inner * 14, `${state}: ${res ? money(pnl) : '—'}, ${tr('Qty')}: ${qty.toFixed(cfg.qtyPrecision)}`, pnlBg, 'middle'))
      figures.push(box(yE + inner * 14, `${tr('Risk/Reward Ratio')}: ${dS > 0 ? (dT / dS).toFixed(2) : '—'}`, '#3a4049', 'middle'))
      return figures
    },
  }
}

export const longPosition = makePosition('longPosition', 1)
export const shortPosition = makePosition('shortPosition', -1)
