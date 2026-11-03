import type { Chart } from 'klinecharts'

/**
 * KLineChart's override*() deep-merges `extendData` into the old value — arrays index by index — so a shorter
 * list (fewer fib levels, no current impulse) keeps stale items. Reset to null first (merge overwrites
 * non-objects), then set the new value: this replaces it outright.
 */
export function replaceOverlayExtend(chart: Chart, id: string, extendData: unknown) {
  chart.overrideOverlay({ id, extendData: null })
  chart.overrideOverlay({ id, extendData })
}

export function replaceIndicatorExtend(chart: Chart, id: string, name: string, extendData: unknown) {
  chart.overrideIndicator({ id, name, extendData: null })
  chart.overrideIndicator({ id, name, extendData })
}
