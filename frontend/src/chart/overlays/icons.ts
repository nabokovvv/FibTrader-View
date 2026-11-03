/**
 * Drawing-tool icons in a 24×24 box, in the familiar charting style: lines end in small hollow "handle" rings
 * (the line stops at the ring's edge), zones get a soft fill.
 *   d    – stroked path
 *   f    – solid fill (arrow heads, dots)
 *   soft – translucent fill (position zones, shape bodies)
 */
export interface Icon { d: string; f?: string; soft?: string }

type P = [number, number]
const R = 2.1
const n = (v: number) => Math.round(v * 100) / 100

/** Hollow ring around a point. */
export const ring = ([x, y]: P, r = R) => `M${n(x - r)} ${n(y)} a${r} ${r} 0 1 0 ${n(2 * r)} 0 a${r} ${r} 0 1 0 ${n(-2 * r)} 0`

/** Polyline through pts; vertices flagged in `h` get a ring and the segments stop at its edge. */
export function poly(pts: P[], h: boolean[] = [], r = R): string {
  let d = ''
  for (let i = 0; i < pts.length - 1; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[i + 1]
    const len = Math.hypot(x2 - x1, y2 - y1) || 1
    const ux = (x2 - x1) / len, uy = (y2 - y1) / len
    const a = h[i] ? r : 0, b = h[i + 1] ? r : 0
    d += `M${n(x1 + ux * a)} ${n(y1 + uy * a)} L${n(x2 - ux * b)} ${n(y2 - uy * b)} `
  }
  const seen = new Set<string>()
  pts.forEach((p, i) => {
    const k = p.join(',')
    if (h[i] && !seen.has(k)) { seen.add(k); d += ring(p, r) + ' ' }
  })
  return d.trim()
}
const all = (k: number) => Array(k).fill(true) as boolean[]

export const ICONS: Record<string, Icon> = {
  // Lines
  segment: { d: poly([[5, 19], [19, 5]], [true, true]) },
  rayLine: { d: poly([[4, 20], [11, 13], [21, 3]], [true, true, false]) },
  straightLine: { d: poly([[2, 22], [8, 16], [16, 8], [22, 2]], [false, true, true, false]) },
  horizontalStraightLine: { d: poly([[2, 12], [12, 12], [22, 12]], [false, true, false]) },
  horizontalRayLine: { d: poly([[4, 12], [22, 12]], [true, false]), f: 'M22 12 l-4 -2.6 v5.2 z' },
  horizontalSegment: { d: poly([[4, 12], [20, 12]], [true, true]) },
  verticalStraightLine: { d: poly([[12, 2], [12, 12], [12, 22]], [false, true, false]) },
  verticalRayLine: { d: poly([[12, 20], [12, 2]], [true, false]), f: 'M12 2 l-2.6 4 h5.2 z' },
  verticalSegment: { d: poly([[12, 4], [12, 20]], [true, true]) },
  priceLine: { d: `${poly([[3, 12], [13, 12]], [true, false])} M13 8.5 h6.5 a1.5 1.5 0 0 1 1.5 1.5 v4 a1.5 1.5 0 0 1 -1.5 1.5 h-6.5 l-2.5 -3.5 z M15 12 h3` },
  arrow: { d: poly([[4, 20], [15.5, 8.5]], [true, false]), f: 'M20 4 L11.6 7.2 L16.8 12.4 Z' },
  // Channels
  parallelStraightLine: {
    d: `${poly([[3, 14], [15, 4]], [true, true])} ${poly([[9, 20], [21, 10]], [false, true])}`,
    soft: 'M3 14 L15 4 L21 10 L9 20 Z',
  },
  priceChannelLine: {
    d: `${poly([[3, 13], [15, 3]], [true, true])} ${poly([[9, 21], [21, 11]], [false, true])} M6 17 L18 7`,
  },
  // Fibonacci
  fibRetracement: {
    d: `${poly([[3, 4], [17.9, 4]])} ${ring([20, 4])} M3 9.33 H21 M3 14.67 H21 ${poly([[6.1, 20], [21, 20]])} ${ring([4, 20])} M5.5 18.5 L18.5 5.5`,
  },
  fibonacciSegment: { d: `M7 4 H21 M7 9.33 H21 M7 14.67 H21 M7 20 H21 ${poly([[4, 20], [4, 4]], [true, true])}` },
  fibonacciExtension: { d: `${poly([[3, 20], [8, 9], [13, 15]], [true, true, true])} M14 4 H22 M16 8 H22 M17 12 H22` },
  fibonacciCircle: { d: `${ring([12, 12], 3)} ${ring([12, 12], 6.5)} ${ring([12, 12], 9.5)}`, f: 'M12 12 m-1.3 0 a1.3 1.3 0 1 0 2.6 0 a1.3 1.3 0 1 0 -2.6 0' },
  fibonacciSpiral: { d: 'M13 12 a1.5 1.5 0 0 1 -1.5 1.5 a3 3 0 0 1 -3 -3 a4.5 4.5 0 0 1 4.5 -4.5 a6 6 0 0 1 6 6 a8 8 0 0 1 -8 8 a10 10 0 0 1 -9 -6' },
  fibonacciSpeedResistanceFan: { d: `${ring([4, 20])} M5.5 18.5 L21 3 M6 19 L21 8 M6 19.6 L21 14 M5 18 L10 3 M5.6 18.3 L16 3` },
  gannBox: { d: `${poly([[4, 20], [4, 4], [20, 4], [20, 20], [4, 20]], [true, false, true, false, true])} M4 12 H20 M12 4 V20 M5.5 18.5 L18.5 5.5` },
  // Positions
  longPosition: {
    d: 'M4 3.5 H20 V20.5 H4 Z M4 14 H20',
    soft: 'M4 3.5 H20 V14 H4 Z',
    f: 'M12 5.8 l3.6 4.2 h-2.3 v2.4 h-2.6 v-2.4 h-2.3 z',
  },
  shortPosition: {
    d: 'M4 3.5 H20 V20.5 H4 Z M4 10 H20',
    soft: 'M4 10 H20 V20.5 H4 Z',
    f: 'M12 18.2 l3.6 -4.2 h-2.3 v-2.4 h-2.6 v2.4 h-2.3 z',
  },
  // Shapes
  rect: { d: poly([[4, 5], [20, 5], [20, 19], [4, 19], [4, 5]], [true, false, true, false, true]), soft: 'M4 5 H20 V19 H4 Z' },
  circle: { d: `M12 12 m-9 0 a9 6.5 0 1 0 18 0 a9 6.5 0 1 0 -18 0`, soft: 'M12 12 m-9 0 a9 6.5 0 1 0 18 0 a9 6.5 0 1 0 -18 0', f: 'M12 12 m-1.4 0 a1.4 1.4 0 1 0 2.8 0 a1.4 1.4 0 1 0 -2.8 0' },
  triangle: { d: poly([[12, 3.5], [21, 20], [3, 20], [12, 3.5]], [true, true, true, true]), soft: 'M12 3.5 L21 20 L3 20 Z' },
  parallelogram: { d: poly([[8, 5], [21, 5], [16, 19], [3, 19], [8, 5]], all(5)), soft: 'M8 5 H21 L16 19 H3 Z' },
  // Patterns (rings on every pivot)
  threeWaves: { d: poly([[3, 18], [9, 6], [14, 14], [21, 4]], all(4), 1.7) },
  fiveWaves: { d: poly([[2, 20], [6, 11], [9.5, 16], [14, 6], [17, 11], [22, 3]], all(6), 1.6) },
  eightWaves: { d: poly([[2, 17], [5, 9], [8, 13], [11, 5], [14, 10], [16.5, 4], [19, 15], [22, 11]], all(8), 1.4) },
  anyWaves: { d: 'M2 15 C5 6 8 6 10 13 S15 19 17 9 S21 6 22 8' },
  abcd: { d: `${poly([[3, 19], [8, 6], [15, 14], [21, 4]], all(4), 1.7)}` },
  xabcd: {
    d: `${poly([[2, 19], [6, 6], [11, 14], [16, 5], [22, 17]], all(5), 1.6)}`,
    soft: 'M2 19 L6 6 L11 14 Z M11 14 L16 5 L22 17 Z',
  },
  // Notes
  simpleAnnotation: {
    d: 'M5 3.5 H19 a2 2 0 0 1 2 2 V13 a2 2 0 0 1 -2 2 H13 L9 19.5 V15 H5 a2 2 0 0 1 -2 -2 V5.5 a2 2 0 0 1 2 -2 Z M7 7.5 H17 M7 11 H13',
  },
  simpleTag: {
    d: 'M3 12 L8 6.5 H19.5 a1.5 1.5 0 0 1 1.5 1.5 V16 a1.5 1.5 0 0 1 -1.5 1.5 H8 Z M12 12 H18',
    f: 'M8.2 12 m-1.3 0 a1.3 1.3 0 1 0 2.6 0 a1.3 1.3 0 1 0 -2.6 0',
  },
  brush: {
    // lucide "brush" (ISC)
    d: 'M9.06 11.9 L17.12 3.84 a2.85 2.85 0 1 1 4.03 4.03 L13.09 15.95 M7.07 14.94 c-1.66 0 -3 1.35 -3 3.02 c0 1.33 -2.5 1.52 -2 2.02 c1.08 1.1 2.49 2.02 4 2.02 c2.2 0 4 -1.8 4 -4.04 a3.01 3.01 0 0 0 -3 -3.02 Z',
  },
  // Ruler (rail button)
  measure: {
    d: 'M2.8 16.2 L16.2 2.8 a1.4 1.4 0 0 1 2 0 L21.2 5.8 a1.4 1.4 0 0 1 0 2 L7.8 21.2 a1.4 1.4 0 0 1 -2 0 L2.8 18.2 a1.4 1.4 0 0 1 0 -2 Z M6.5 12.5 l2.2 2.2 M9 10 l1.5 1.5 M11.5 7.5 l2.2 2.2 M14 5 l1.5 1.5',
  },
}
