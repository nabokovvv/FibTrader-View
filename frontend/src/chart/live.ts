import type { Chart } from 'klinecharts'

/** The live chart instance (and its container) for modules that ChartView itself imports (no import cycle through
 *  ChartView). */
export const live: { chart: Chart | null; el: HTMLElement | null } = { chart: null, el: null }
