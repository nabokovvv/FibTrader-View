import type { Chart } from 'klinecharts'

/** The live chart instance for modules that ChartView itself imports (no import cycle through ChartView). */
export const live: { chart: Chart | null } = { chart: null }
