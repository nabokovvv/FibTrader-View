import type { Chart, IndicatorDrawParams } from 'klinecharts'
import { useStore } from '../../state/store'
import { hexAlpha } from '../theme'

/* ------------------------------------------------------------------------------------------------------------
 * Reference levels of oscillators, like TradingView: overbought / oversold bands (editable as the last indicator
 * parameters) with a faint fill between them, the line filled beyond a band (single-line oscillators), a single
 * level (ADX) or a zero line. Drawn under the indicator lines (`draw` returns false, so the lines follow), and the
 * levels are kept inside the pane's scale (minValue / maxValue).
 * ---------------------------------------------------------------------------------------------------------- */
export const RSI_COLOR = '#7e57c2'

interface Spec {
  bands?: [number, number]   // indexes of the upper and lower band in calcParams
  level?: number             // index of a single level in calcParams
  zero?: boolean
  mid?: number               // dotted middle line
  fillKey?: string           // result key of the single line filled beyond the bands
  tint?: string              // colour of the fill between the bands
}

const SPECS: Record<string, Spec> = {
  RSI: { bands: [1, 2], mid: 50, fillKey: 'rsi', tint: RSI_COLOR },
  WR: { bands: [1, 2], mid: -50, fillKey: 'wr' },
  CCI: { bands: [1, 2], mid: 0, fillKey: 'cci' },
  STOCH: { bands: [3, 4] },
  KDJ: { bands: [3, 4] },
  DMI: { level: 2 },
  MACD: { zero: true },
  AO: { zero: true },
  MTM: { zero: true },
  ROC: { zero: true },
}

const num = (v: unknown, fallback: number) => (typeof v === 'number' && Number.isFinite(v) ? v : fallback)

/** Missing trailing parameters (layouts saved before the bands existed) come from the catalogue defaults. */
export function padParams(params: number[] | undefined, defaults: number[] | undefined): number[] | undefined {
  if (!defaults?.length) return params
  const p = params ? [...params] : []
  for (let i = p.length; i < defaults.length; i++) p.push(defaults[i])
  return p
}

function bandValues(spec: Spec, p: number[], defaults: number[]): [number, number] {
  const [ui, li] = spec.bands!
  let up = num(p[ui], defaults[ui])
  let lo = num(p[li], defaults[li])
  if (up < lo) [up, lo] = [lo, up]
  return [up, lo]
}

/** createIndicator / overrideIndicator fields for an indicator with reference levels ({} otherwise). */
export function levelProps(name: string, params: number[] | undefined, defaults: number[] = []): Record<string, unknown> {
  const spec = SPECS[name]
  if (!spec) return {}
  const p = params ?? []
  let minValue: number | null = null
  let maxValue: number | null = null
  if (spec.bands) [maxValue, minValue] = bandValues(spec, p, defaults)
  else if (spec.level !== undefined) minValue = maxValue = num(p[spec.level], defaults[spec.level] ?? 25)
  else if (spec.zero) minValue = maxValue = 0
  return { minValue, maxValue, draw: drawLevels(spec, defaults) }
}

function drawLevels(spec: Spec, defaults: number[]) {
  return ({ ctx, chart, indicator, bounding, xAxis, yAxis }: IndicatorDrawParams<unknown, unknown, unknown>): boolean => {
    const st = useStore.getState()
    const dark = st.theme === 'dark'
    const p = (indicator.calcParams as number[]) ?? []
    const width = bounding.width
    const y = (v: number) => Math.round(yAxis.convertToPixel(v)) + 0.5
    const hline = (v: number, dash: number[]) => {
      const yy = y(v)
      ctx.setLineDash(dash)
      ctx.beginPath()
      ctx.moveTo(0, yy)
      ctx.lineTo(width, yy)
      ctx.stroke()
    }
    ctx.lineWidth = 1
    ctx.strokeStyle = dark ? 'rgba(139,146,156,.6)' : 'rgba(107,112,121,.6)'
    if (spec.zero) { hline(0, [4, 4]); return false }
    if (spec.level !== undefined) { hline(num(p[spec.level], defaults[spec.level] ?? 25), [4, 4]); return false }
    if (!spec.bands) return false

    const [up, lo] = bandValues(spec, p, defaults)
    const yU = y(up)
    const yL = y(lo)
    ctx.fillStyle = hexAlpha(spec.tint ?? (dark ? '#8b929c' : '#6b7079'), dark ? 0.08 : 0.07)
    ctx.fillRect(0, yU, width, yL - yU)
    hline(up, [4, 4])
    hline(lo, [4, 4])
    if (spec.mid !== undefined && spec.mid > lo && spec.mid < up) hline(spec.mid, [1, 3])

    // the line beyond a band: filled between the line and the band (rise colour above, fall colour below)
    if (spec.fillKey) {
      const result = indicator.result as Array<Record<string, number | undefined>>
      const vr = chart.getVisibleRange()
      const pts: Array<[number, number]> = []
      for (let i = Math.max(0, Math.floor(vr.from) - 1); i < Math.min(result.length, Math.ceil(vr.to) + 1); i++) {
        const v = result[i]?.[spec.fillKey]
        if (typeof v === 'number' && Number.isFinite(v)) pts.push([xAxis.convertToPixel(i), yAxis.convertToPixel(v)])
      }
      if (pts.length > 1) {
        const zone = (yb: number, above: boolean, color: string) => {
          ctx.save()
          ctx.beginPath()
          if (above) ctx.rect(0, -1e5, width, yb + 1e5)
          else ctx.rect(0, yb, width, 1e5)
          ctx.clip()
          ctx.beginPath()
          ctx.moveTo(pts[0][0], yb)
          for (const [x, yy] of pts) ctx.lineTo(x, yy)
          ctx.lineTo(pts[pts.length - 1][0], yb)
          ctx.closePath()
          ctx.fillStyle = color
          ctx.fill()
          ctx.restore()
        }
        zone(yU, true, hexAlpha(st.settings.upColor, 0.3))
        zone(yL, false, hexAlpha(st.settings.downColor, 0.3))
      }
    }
    return false
  }
}

/** Re-apply the levels after a parameter change (draw reads the parameters itself; the scale needs min / max). */
export function overrideLevels(chart: Chart, id: string, name: string, params: number[] | undefined, defaults: number[] = []) {
  const props = levelProps(name, params, defaults)
  if ('minValue' in props) chart.overrideIndicator({ id, name, minValue: props.minValue as number | null, maxValue: props.maxValue as number | null })
}
