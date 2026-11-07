import { registerOverlay } from 'klinecharts'
import { EXTRA_OVERLAYS } from './extra'
import { fibRetracement } from './fib'
import { longPosition, shortPosition } from './position'
import { measure } from './measure'
import { fixedRangeVolumeProfile } from './volumeProfile'
import { ICONS, type Icon } from './icons'
export type { Icon }

export interface ToolMeta {
  name: string          // klinecharts overlay name
  label: string
  group: 'Lines' | 'Channels' | 'Fibonacci' | 'Positions' | 'Shapes' | 'Patterns' | 'Notes'
  hotkey?: string
}
export type ToolEntry = ToolMeta & { icon: Icon }

const TOOL_LIST: ToolMeta[] = [
  { name: 'segment', label: 'Trend line', group: 'Lines', hotkey: 'Alt+T' },
  { name: 'rayLine', label: 'Ray', group: 'Lines', hotkey: 'Alt+R' },
  { name: 'straightLine', label: 'Extended line', group: 'Lines' },
  { name: 'horizontalStraightLine', label: 'Horizontal line', group: 'Lines', hotkey: 'Alt+H' },
  { name: 'horizontalRayLine', label: 'Horizontal ray', group: 'Lines' },
  { name: 'horizontalSegment', label: 'Horizontal segment', group: 'Lines' },
  { name: 'verticalStraightLine', label: 'Vertical line', group: 'Lines', hotkey: 'Alt+V' },
  { name: 'verticalRayLine', label: 'Vertical ray', group: 'Lines' },
  { name: 'verticalSegment', label: 'Vertical segment', group: 'Lines' },
  { name: 'priceLine', label: 'Price line', group: 'Lines', hotkey: 'Alt+P' },
  { name: 'arrow', label: 'Arrow', group: 'Lines' },
  { name: 'parallelStraightLine', label: 'Parallel channel', group: 'Channels' },
  { name: 'priceChannelLine', label: 'Price channel', group: 'Channels' },
  { name: 'fibRetracement', label: 'Fib retracement', group: 'Fibonacci', hotkey: 'Alt+F' },
  { name: 'fibonacciSegment', label: 'Fib segment', group: 'Fibonacci' },
  { name: 'fibonacciExtension', label: 'Fib extension', group: 'Fibonacci' },
  { name: 'fibonacciCircle', label: 'Fib circles', group: 'Fibonacci' },
  { name: 'fibonacciSpiral', label: 'Fib spiral', group: 'Fibonacci' },
  { name: 'fibonacciSpeedResistanceFan', label: 'Fib speed fan', group: 'Fibonacci' },
  { name: 'gannBox', label: 'Gann box', group: 'Fibonacci' },
  { name: 'longPosition', label: 'Long position', group: 'Positions' },
  { name: 'shortPosition', label: 'Short position', group: 'Positions' },
  { name: 'fixedRangeVolumeProfile', label: 'Fixed range volume profile', group: 'Positions' },
  { name: 'rect', label: 'Rectangle', group: 'Shapes' },
  { name: 'circle', label: 'Ellipse', group: 'Shapes' },
  { name: 'triangle', label: 'Triangle', group: 'Shapes' },
  { name: 'parallelogram', label: 'Parallelogram', group: 'Shapes' },
  { name: 'threeWaves', label: 'ABC wave', group: 'Patterns' },
  { name: 'fiveWaves', label: '5-wave impulse', group: 'Patterns' },
  { name: 'eightWaves', label: '8-wave cycle', group: 'Patterns' },
  { name: 'anyWaves', label: 'Free wave', group: 'Patterns' },
  { name: 'abcd', label: 'ABCD', group: 'Patterns' },
  { name: 'xabcd', label: 'XABCD', group: 'Patterns' },
  { name: 'simpleAnnotation', label: 'Annotation', group: 'Notes' },
  { name: 'simpleTag', label: 'Price tag', group: 'Notes' },
  { name: 'brush', label: 'Brush', group: 'Notes' },
]

export const TOOLS: ToolEntry[] = TOOL_LIST.map((t) => ({ ...t, icon: ICONS[t.name] ?? { d: '' } }))

/** Ruler (Measure): standalone rail button, transient drawing. */
export const MEASURE_ICON = ICONS.measure

export const TOOL_GROUPS: ToolMeta['group'][] = ['Lines', 'Channels', 'Fibonacci', 'Positions', 'Shapes', 'Patterns', 'Notes']

let registered = false
export function registerCustomOverlays() {
  if (registered) return
  registered = true
  for (const t of EXTRA_OVERLAYS) registerOverlay(t)
  registerOverlay(fibRetracement as Parameters<typeof registerOverlay>[0])
  registerOverlay(longPosition as Parameters<typeof registerOverlay>[0])
  registerOverlay(measure)
  registerOverlay(shortPosition as Parameters<typeof registerOverlay>[0])
  registerOverlay(fixedRangeVolumeProfile)
}
