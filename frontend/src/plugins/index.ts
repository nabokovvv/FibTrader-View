import type { Chart, IndicatorTemplate } from 'klinecharts'
import type { ComponentType, RefObject } from 'react'
import type { IndicatorMeta } from '../chart/indicators'
import type { IndicatorState, SymbolInfo } from '../lib/types'

/**
 * Optional chart plugins. Every folder `src/plugins/<name>/index.ts` with a default-exported `ChartPlugin`
 * is bundled automatically; the public build ships without the private plugin folders.
 */
export interface PluginIndicator {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  template: IndicatorTemplate<any, any, any>
  /** Entry in the Indicators dialog (shown before the built-ins). */
  meta: IndicatorMeta
  /** extendData the indicator starts with when it is added to the chart. */
  initialExtend?: (ind: IndicatorState) => unknown
  /** Extra settings shown under the indicator in the Indicators dialog. */
  Settings?: ComponentType<{ ind: IndicatorState }>
}

export interface ChartPlugin {
  id: string
  indicators?: PluginIndicator[]
  /** React hook called on every ChartView render (e.g. to load the plugin's data into its indicators). */
  useChart?: (chart: RefObject<Chart | null>, indicators: IndicatorState[], symbol: SymbolInfo | null) => void
}

const modules = import.meta.glob<{ default: ChartPlugin }>('./*/index.ts', { eager: true })

export const PLUGINS: ChartPlugin[] = Object.keys(modules).sort().map((k) => modules[k].default)
export const PLUGIN_INDICATORS: PluginIndicator[] = PLUGINS.flatMap((p) => p.indicators ?? [])
export const pluginIndicator = (name: string) => PLUGIN_INDICATORS.find((i) => i.meta.name === name)

/** Fixed at build time, so calling them in order from ChartView keeps the hook order stable. */
export const PLUGIN_HOOKS = PLUGINS.flatMap((p) => (p.useChart ? [p.useChart] : []))
