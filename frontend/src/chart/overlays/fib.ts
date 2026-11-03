import type { OverlayFigure, OverlayTemplate } from 'klinecharts'

/** One retracement level with its own style. */
export interface FibLevel {
  value: number
  enabled: boolean
  color: string
  style: 'solid' | 'dashed' | 'dotted'
  width: number
}

export interface FibConfig {
  levels: FibLevel[]
  extendLeft: boolean
  extendRight: boolean
  reverse: boolean
  trendLine: boolean
  background: boolean
  bgOpacity: number          // 0..1
  showValues: boolean        // "0.618"
  showPrices: boolean        // "(77,123.4)"
  asPercent: boolean         // "61.8%" instead of "0.618"
  labelsLeft: boolean        // labels at the left end (the usual default) or right
}

const L = (value: number, color: string, enabled = true): FibLevel => ({ value, enabled, color, style: 'solid', width: 1 })

/** Standard Fib retracement: the commonly used levels, colours and on/off states. */
export const STANDARD_FIB: FibConfig = {
  levels: [
    L(0, '#787b86'), L(0.236, '#f23645'), L(0.382, '#ff9800'), L(0.5, '#4caf50'), L(0.618, '#089981'),
    L(0.786, '#00bcd4'), L(1, '#787b86'), L(1.618, '#2962ff'), L(2.618, '#f23645'), L(3.618, '#9c27b0'),
    L(4.236, '#e91e63'), L(1.272, '#ff9800', false), L(1.414, '#f23645', false), L(2, '#089981', false),
    L(2.272, '#ff9800', false), L(2.414, '#4caf50', false), L(3, '#00bcd4', false), L(3.272, '#787b86', false),
    L(3.414, '#2962ff', false), L(4, '#f23645', false), L(4.272, '#9c27b0', false), L(4.414, '#e91e63', false),
    L(4.618, '#ff9800', false), L(4.764, '#089981', false),
  ],
  extendLeft: false,
  extendRight: false,
  reverse: false,
  trendLine: true,
  background: true,
  bgOpacity: 0.12,
  showValues: true,
  showPrices: true,
  asPercent: false,
  labelsLeft: true,
}

let userDefaults: FibConfig | null = null
/** Defaults used for new drawings: the user's saved template, else the standard one. */
export function setFibDefaults(cfg: FibConfig | null | undefined) { userDefaults = cfg ? normalizeFib(cfg) : null }
export function fibDefaults(): FibConfig { return cloneFib(userDefaults ?? STANDARD_FIB) }
export const cloneFib = (c: FibConfig): FibConfig => JSON.parse(JSON.stringify(c)) as FibConfig

/** Fill gaps in configs saved by older versions. */
export function normalizeFib(raw: unknown): FibConfig {
  const c = (raw && typeof raw === 'object' ? raw : {}) as Partial<FibConfig>
  const base = cloneFib(STANDARD_FIB)
  const levels = Array.isArray(c.levels) && c.levels.length
    ? c.levels.filter((l) => Number.isFinite(Number(l?.value))).map((l) => ({
      value: Number(l.value), enabled: l.enabled !== false, color: typeof l.color === 'string' ? l.color : '#787b86',
      style: (['solid', 'dashed', 'dotted'] as const).includes(l.style) ? l.style : 'solid',
      width: Math.min(4, Math.max(1, Number(l.width) || 1)),
    }))
    : base.levels
  return { ...base, ...c, levels }
}

export function fibFormat(v: number, asPercent: boolean): string {
  if (asPercent) return `${+(v * 100).toFixed(2)}%`
  return String(+v.toFixed(4))
}

const DASH: Record<FibLevel['style'], number[]> = { solid: [], dashed: [6, 4], dotted: [1.5, 3] }

function hexA(hex: string, a: number): string {
  const h = hex.replace('#', '')
  if (h.length !== 6) return hex
  const n = parseInt(h, 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
}

/**
 * Fib retracement with per-level settings. Point 1 is level 1, point 2 is level 0 (the common convention);
 * `reverse` swaps them. The config lives in extendData so it is saved with the drawing.
 */
export const fibRetracement: OverlayTemplate<FibConfig> = {
  name: 'fibRetracement',
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ chart, coordinates, bounding, overlay }) => {
    if (coordinates.length < 2) return []
    const pts = overlay.points
    const v1 = pts[0]?.value, v2 = pts[1]?.value
    if (typeof v1 !== 'number' || typeof v2 !== 'number') return []
    const cfg = normalizeFib(overlay.extendData)
    const precision = chart.getSymbol()?.pricePrecision ?? 2
    const [a, b] = cfg.reverse ? [coordinates[1], coordinates[0]] : [coordinates[0], coordinates[1]]
    const [pa, pb] = cfg.reverse ? [v2, v1] : [v1, v2]
    const left = cfg.extendLeft ? 0 : Math.min(a.x, b.x)
    const right = cfg.extendRight ? bounding.width : Math.max(a.x, b.x)

    const levels = cfg.levels.filter((l) => l.enabled).sort((x, y) => x.value - y.value)
    const rows = levels.map((l) => ({ l, y: b.y + (a.y - b.y) * l.value, price: pb + (pa - pb) * l.value }))
    const figures: OverlayFigure[] = []

    if (cfg.background) {
      for (let i = 0; i + 1 < rows.length; i++) {
        const r0 = rows[i], r1 = rows[i + 1]
        figures.push({
          type: 'polygon', ignoreEvent: true,
          attrs: { coordinates: [{ x: left, y: r0.y }, { x: right, y: r0.y }, { x: right, y: r1.y }, { x: left, y: r1.y }] },
          styles: { style: 'fill', color: hexA(r1.l.color, cfg.bgOpacity) },
        })
      }
    }
    if (cfg.trendLine) {
      figures.push({ type: 'line', attrs: { coordinates: [a, b] }, styles: { style: 'dashed', dashedValue: [4, 4], size: 1, color: hexA('#9598a1', 0.8) } })
    }
    for (const r of rows) {
      figures.push({
        type: 'line',
        attrs: { coordinates: [{ x: left, y: r.y }, { x: right, y: r.y }] },
        styles: { style: r.l.style === 'solid' ? 'solid' : 'dashed', dashedValue: DASH[r.l.style], size: r.l.width, color: r.l.color },
      })
      if (cfg.showValues || cfg.showPrices) {
        const parts: string[] = []
        if (cfg.showValues) parts.push(fibFormat(r.l.value, cfg.asPercent))
        if (cfg.showPrices) parts.push(`(${r.price.toFixed(precision)})`)
        const atLeft = cfg.labelsLeft
        const x = atLeft ? (cfg.extendLeft ? 4 : left - 6) : (cfg.extendRight ? bounding.width - 4 : right + 6)
        const align = atLeft ? (cfg.extendLeft ? 'left' : 'right') : (cfg.extendRight ? 'right' : 'left')
        figures.push({
          type: 'text', ignoreEvent: true,
          attrs: { x, y: r.y, text: parts.join(' '), align, baseline: 'middle' },
          styles: { color: r.l.color, size: 11, backgroundColor: 'transparent', borderSize: 0, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0 },
        })
      }
    }
    return figures
  },
}
