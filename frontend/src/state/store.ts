import { create } from 'zustand'
import { serverLimits } from '../auth/session'
import { t } from '../i18n'
import { api, ApiError } from '../api/client'
import type { WsStatus } from '../api/ws'
import type { ChartType, IndicatorState, Interval, Quote, SymbolInfo, Watchlist } from '../lib/types'
import { setFibDefaults, type FibConfig } from '../chart/overlays/fib'
import { setPositionDefaults, type PositionConfig } from '../chart/overlays/position'

export type Magnet = 'normal' | 'weak_magnet' | 'strong_magnet'
export type Sheet = null | 'tools' | 'watchlist' | 'indicators' | 'search' | 'settings' | 'alerts' | 'share' | 'donate' | 'feedback'
export type Theme = 'dark' | 'light'

export interface Settings {
  upColor: string
  downColor: string
  showVolume: boolean
  showGrid: boolean
  showLastPrice: boolean
  /** User's default Fib retracement template (null = the standard levels). */
  fibDefaults?: FibConfig | null
  /** Account size / risk used by new Long & Short position drawings. */
  positionDefaults?: PositionConfig | null
  /** Colour/width new drawings of a tool start with (brush). */
  toolStyles?: Record<string, ToolStyle>
}

export interface ToolStyle { color: string; size: number }
export const DEFAULT_TOOL_STYLES: Record<string, ToolStyle> = { brush: { color: '#ffab2e', size: 2 } }
export const toolStyle = (s: Settings, tool: string): ToolStyle | undefined => s.toolStyles?.[tool] ?? DEFAULT_TOOL_STYLES[tool]

export const DEFAULT_SETTINGS: Settings = {
  upColor: '#089981',   // standard teal / red
  downColor: '#f23645',
  showVolume: true,
  showGrid: true,
  showLastPrice: true,
}

interface LayoutState {
  symbolId: string
  interval: Interval
  chartType: ChartType
  theme: Theme
  timezone: string
  indicators: IndicatorState[]
  showWatchlist: boolean
  activeWatchlistId: number | null
}

interface AppState {
  ready: boolean
  error: string | null
  /** A neutral message (copied, sent, an alert fired). */
  notice: string | null
  symbol: SymbolInfo | null
  interval: Interval
  chartType: ChartType
  theme: Theme
  timezone: string
  indicators: IndicatorState[]
  settings: Settings
  watchlists: Watchlist[]
  activeWatchlistId: number | null
  quotes: Record<string, Quote>
  tool: string | null
  magnet: Magnet
  selectedOverlayId: string | null
  /** Drawing whose settings dialog is open (Fib retracement). */
  editOverlayId: string | null
  showWatchlist: boolean
  sheet: Sheet
  wsStatus: WsStatus
  lastBar: { close: number; open: number; ts: number } | null

  bootstrap: () => Promise<void>
  setSymbol: (s: SymbolInfo) => void
  setSymbolById: (id: string) => Promise<void>
  setInterval: (iv: Interval) => void
  setChartType: (t: ChartType) => void
  setTheme: (t: Theme) => void
  setTimezone: (tz: string) => void
  addIndicator: (name: string, main: boolean, calcParams?: number[], options?: Record<string, unknown>) => void
  removeIndicator: (key: string) => void
  updateIndicator: (key: string, patch: Partial<IndicatorState>) => void
  setSettings: (patch: Partial<Settings>) => void
  setTool: (tool: string | null) => void
  setMagnet: (m: Magnet) => void
  setSelectedOverlay: (id: string | null) => void
  setEditOverlay: (id: string | null) => void
  toggleWatchlist: () => void
  setSheet: (s: Sheet) => void
  setWsStatus: (s: WsStatus) => void
  setQuotes: (q: Record<string, Quote>) => void
  setLastBar: (b: AppState['lastBar']) => void
  loadWatchlists: () => Promise<void>
  setActiveWatchlist: (id: number) => void
  addToWatchlist: (symbolId: string, watchlistId?: number) => Promise<void>
  removeFromWatchlist: (symbolId: string, watchlistId?: number) => Promise<void>
  createWatchlist: (name: string) => Promise<void>
  renameWatchlist: (id: number, name: string) => Promise<void>
  deleteWatchlist: (id: number) => Promise<void>
  layoutNames: string[]
  refreshLayouts: () => Promise<void>
  saveLayoutAs: (name: string) => Promise<void>
  loadLayout: (name: string) => Promise<void>
  deleteLayout: (name: string) => Promise<void>
}

/** Expose candle colours to CSS so price/% text matches the candles. */
export function applyCandleVars(s: Settings) {
  const root = document.documentElement.style
  root.setProperty('--candle-up', s.upColor)
  root.setProperty('--candle-down', s.downColor)
}

const DEFAULT_SYMBOL_ID = 'bybit:BTCUSDT'
const DEFAULT_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

function layoutState(s: AppState): LayoutState {
  return {
    symbolId: s.symbol?.id ?? DEFAULT_SYMBOL_ID,
    interval: s.interval,
    chartType: s.chartType,
    theme: s.theme,
    timezone: s.timezone,
    indicators: s.indicators,
    showWatchlist: s.showWatchlist,
    activeWatchlistId: s.activeWatchlistId,
  }
}

/* ------------------------------------------------------------------------------------------------------------
 * Account sync of the autosaved layout ('last') and the settings, for several devices / tabs of one account.
 * A tab writes only the fields it changed on top of the server copy (read, merge, write), so changing the symbol on
 * the laptop no longer overwrites the indicators added on the desktop. When a tab comes back to the front it pulls
 * the account-wide fields (indicators, chart type, theme, time zone, settings); the symbol / interval / watchlist
 * stay per tab (two tabs with different symbols must not flip each other) and come from 'last' on a page load.
 * ---------------------------------------------------------------------------------------------------------- */
type LayoutKey = keyof LayoutState
const SHARED_LAYOUT = ['indicators', 'chartType', 'theme', 'timezone'] as const
const dirtyLayout = new Set<LayoutKey>()
const dirtySettings = new Set<keyof Settings>()
let persistTimer: number | null = null
let settingsTimer: number | null = null
let saveChain: Promise<unknown> = Promise.resolve()
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
const isMissing = (e: unknown) => e instanceof ApiError && e.status === 404

function schedulePersist(...keys: LayoutKey[]) {
  for (const k of keys) dirtyLayout.add(k)
  if (persistTimer) window.clearTimeout(persistTimer)
  persistTimer = window.setTimeout(() => { persistTimer = null; void saveLast() }, 600)
}

function scheduleSettings(keys: Array<keyof Settings>) {
  for (const k of keys) dirtySettings.add(k)
  if (settingsTimer) window.clearTimeout(settingsTimer)
  settingsTimer = window.setTimeout(() => { settingsTimer = null; void saveSettings() }, 300)
}

/** Queue the saves one after another (a slow request must not let an older state land last). */
function chained(job: () => Promise<void>): Promise<unknown> {
  saveChain = saveChain.then(job).catch(() => { /* offline: the dirty fields are kept for the next save */ })
  return saveChain
}

function saveLast(): Promise<unknown> {
  return chained(async () => {
    const s = useStore.getState()
    if (!s.ready || !s.symbol || !dirtyLayout.size) return
    const keys = [...dirtyLayout]
    dirtyLayout.clear()
    try {
      let server: Partial<LayoutState> = {}
      try { server = (await api.layout('last')).state as Partial<LayoutState> } catch (e) { if (!isMissing(e)) throw e }
      const mine = layoutState(s)
      const next = { ...mine, ...server } as LayoutState
      for (const k of keys) (next as unknown as Record<string, unknown>)[k] = mine[k]
      await api.putLayout('last', next as unknown as Record<string, unknown>)
      applyShared(next)
    } catch (e) {
      for (const k of keys) dirtyLayout.add(k)
      throw e
    }
  })
}

function saveSettings(): Promise<unknown> {
  return chained(async () => {
    if (!dirtySettings.size) return
    const keys = [...dirtySettings]
    dirtySettings.clear()
    try {
      const server = ((await api.settings()).ui as Partial<Settings> | undefined) ?? {}
      const mine = useStore.getState().settings
      const next = { ...DEFAULT_SETTINGS, ...mine, ...server } as Settings
      for (const k of keys) (next as unknown as Record<string, unknown>)[k] = mine[k]
      await api.putSettings({ ui: next })
      applySettings(next)
    } catch (e) {
      for (const k of keys) dirtySettings.add(k)
      throw e
    }
  })
}

/** Indicators of saved layouts that no longer exist (the compare overlay was removed on 2026-11-06). */
const REMOVED_INDICATORS = new Set(['CMP'])
const liveIndicators = (list: IndicatorState[] | undefined) => list?.filter((i) => !REMOVED_INDICATORS.has(i.name))

/** Take the account-wide layout fields from the server copy, except those this tab changed and has not saved yet. */
function applyShared(raw: Partial<LayoutState>) {
  const l = { ...raw, indicators: liveIndicators(raw.indicators) }
  const s = useStore.getState()
  const patch: Partial<AppState> = {}
  for (const k of SHARED_LAYOUT) {
    if (dirtyLayout.has(k) || l[k] === undefined || same(l[k], s[k])) continue
    ;(patch as Record<string, unknown>)[k] = l[k]
  }
  if (!Object.keys(patch).length) return
  if (patch.theme) document.documentElement.dataset.theme = patch.theme
  useStore.setState(patch)
}

function applySettings(server: Partial<Settings>) {
  const cur = useStore.getState().settings
  const next = { ...cur }
  let changed = false
  for (const [k, v] of Object.entries(server) as Array<[keyof Settings, unknown]>) {
    if (dirtySettings.has(k) || same(v, cur[k])) continue
    ;(next as unknown as Record<string, unknown>)[k] = v
    changed = true
  }
  if (!changed) return
  useStore.setState({ settings: next })
  applyCandleVars(next)
  setFibDefaults(next.fibDefaults)
  setPositionDefaults(next.positionDefaults)
}

let lastPull = 0
/** A tab came back to the front: pick up what other devices / tabs of the account changed meanwhile. */
async function pullShared() {
  const now = Date.now()
  if (now - lastPull < 3000 || !useStore.getState().ready) return
  lastPull = now
  await saveChain
  try {
    const [lay, set] = await Promise.all([api.layout('last').catch((e) => { if (isMissing(e)) return null; throw e }), api.settings()])
    if (lay) applyShared(lay.state as Partial<LayoutState>)
    const ui = set.ui as Partial<Settings> | undefined
    if (ui) applySettings(ui)
  } catch { /* offline: try on the next focus */ }
}

let pullHooked = false
function hookPull() {
  if (pullHooked) return
  pullHooked = true
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') void pullShared() })
  window.addEventListener('focus', () => { void pullShared() })
}

const uid = () => Math.random().toString(36).slice(2, 10)

/** Show a watchlist change at once; if the server refuses it, put the list back and say why. */
async function optimistic(set: (p: Partial<AppState>) => void, get: () => AppState, wid: number | null,
  patch: Partial<Watchlist>, save: () => Promise<unknown>) {
  const before = get().watchlists
  set({ watchlists: before.map((w) => (w.id === wid ? { ...w, ...patch } : w)) })
  try { await save() } catch (e) {
    set({ watchlists: get().watchlists.map((w) => (w.id === wid ? before.find((b) => b.id === wid) ?? w : w)), error: (e as Error).message })
  }
}

/** Write out debounced layout/settings saves now (before a reload, e.g. switching the language). */
export async function flushPendingSaves(): Promise<void> {
  if (persistTimer) { window.clearTimeout(persistTimer); persistTimer = null; void saveLast() }
  if (settingsTimer) { window.clearTimeout(settingsTimer); settingsTimer = null; void saveSettings() }
  await Promise.race([saveChain, new Promise((r) => window.setTimeout(r, 3000))])
}

export const useStore = create<AppState>((set, get) => ({
  ready: false,
  error: null,
  notice: null,
  symbol: null,
  interval: '1h',
  chartType: 'candle_solid',
  theme: 'dark',
  timezone: DEFAULT_TZ,
  indicators: [{ key: 'vol', name: 'VOL', main: false }],
  settings: DEFAULT_SETTINGS,
  watchlists: [],
  activeWatchlistId: null,
  quotes: {},
  tool: null,
  magnet: 'weak_magnet',
  selectedOverlayId: null,
  editOverlayId: null,
  showWatchlist: true,
  sheet: null,
  wsStatus: 'closed',
  lastBar: null,
  layoutNames: [],

  bootstrap: async () => {
    let layout: Partial<LayoutState> = {}
    try { layout = (await api.layout('last')).state as Partial<LayoutState> } catch { /* first run */ }
    let settings = DEFAULT_SETTINGS
    try { settings = { ...DEFAULT_SETTINGS, ...((await api.settings()).ui as Partial<Settings> | undefined) } } catch { /* ignore */ }
    // a link with ?symbol=… (alerts in Telegram) opens that symbol once, then the address is cleaned up
    const linked = new URLSearchParams(window.location.search).get('symbol')
    if (linked) window.history.replaceState(null, '', window.location.pathname)
    const symbolId = (linked && linked.length <= 64 ? linked : null) ?? layout.symbolId ?? DEFAULT_SYMBOL_ID
    let symbol: SymbolInfo | null = null
    try { symbol = await api.symbol(symbolId) } catch { /* fall through */ }
    if (!symbol) {
      try { symbol = await api.symbol(DEFAULT_SYMBOL_ID) } catch (e) { set({ error: t('Backend unreachable: {message}', { message: (e as Error).message }) }) }
    }
    const theme = layout.theme ?? 'dark'
    document.documentElement.dataset.theme = theme
    applyCandleVars(settings)
    setFibDefaults(settings.fibDefaults)
    setPositionDefaults(settings.positionDefaults)
    set({
      symbol,
      interval: layout.interval ?? '1h',
      chartType: layout.chartType ?? 'candle_solid',
      theme,
      timezone: layout.timezone ?? DEFAULT_TZ,
      indicators: liveIndicators(layout.indicators) ?? [{ key: 'vol', name: 'VOL', main: false }],
      showWatchlist: layout.showWatchlist ?? window.innerWidth >= 1100,
      activeWatchlistId: layout.activeWatchlistId ?? null,
      settings,
      ready: true,
    })
    void get().loadWatchlists()
    void get().refreshLayouts()
    hookPull()
  },

  setSymbol: (s) => { set({ symbol: s, selectedOverlayId: null, tool: null, lastBar: null, sheet: null }); schedulePersist('symbolId') },
  setSymbolById: async (id) => {
    try { get().setSymbol(await api.symbol(id)) } catch (e) { set({ error: (e as Error).message }) }
  },
  setInterval: (iv) => { set({ interval: iv, lastBar: null }); schedulePersist('interval') },
  setChartType: (t) => { set({ chartType: t }); schedulePersist('chartType') },
  setTheme: (t) => { document.documentElement.dataset.theme = t; set({ theme: t }); schedulePersist('theme') },
  setTimezone: (tz) => { set({ timezone: tz }); schedulePersist('timezone') },

  addIndicator: (name, main, calcParams, options) => {
    set({ indicators: [...get().indicators, { key: uid(), name, main, calcParams, ...(options ? { options } : {}) }] })
    schedulePersist('indicators')
  },
  removeIndicator: (key) => { set({ indicators: get().indicators.filter((i) => i.key !== key) }); schedulePersist('indicators') },
  updateIndicator: (key, patch) => {
    set({ indicators: get().indicators.map((i) => (i.key === key ? { ...i, ...patch } : i)) })
    schedulePersist('indicators')
  },
  setSettings: (patch) => {
    const settings = { ...get().settings, ...patch }
    set({ settings })
    applyCandleVars(settings)
    setFibDefaults(settings.fibDefaults)
    setPositionDefaults(settings.positionDefaults)
    scheduleSettings(Object.keys(patch) as Array<keyof Settings>)
  },

  setTool: (tool) => set({ tool }),
  setMagnet: (m) => set({ magnet: m }),
  setSelectedOverlay: (id) => set({ selectedOverlayId: id }),
  setEditOverlay: (id) => set({ editOverlayId: id }),
  toggleWatchlist: () => { set({ showWatchlist: !get().showWatchlist }); schedulePersist('showWatchlist') },
  setSheet: (s) => set({ sheet: s }),
  setWsStatus: (s) => set({ wsStatus: s }),
  setQuotes: (q) => set({ quotes: { ...get().quotes, ...q } }),
  setLastBar: (b) => set({ lastBar: b }),

  loadWatchlists: async () => {
    try {
      const watchlists = await api.watchlists()
      const active = get().activeWatchlistId
      set({ watchlists, activeWatchlistId: watchlists.some((w) => w.id === active) ? active : watchlists[0]?.id ?? null })
    } catch (e) { set({ error: (e as Error).message }) }
  },
  setActiveWatchlist: (id) => { set({ activeWatchlistId: id }); schedulePersist('activeWatchlistId') },
  addToWatchlist: async (symbolId, watchlistId) => {
    const wid = watchlistId ?? get().activeWatchlistId
    const wl = get().watchlists.find((w) => w.id === wid)
    if (!wl || wl.items.includes(symbolId)) return
    const max = serverLimits()?.watchlist_items
    if (max !== undefined && wl.items.length >= max) { set({ error: t('This list is full ({n} symbols). Remove one or make a new list.', { n: max }) }); return }
    const items = [...wl.items, symbolId]
    await optimistic(set, get, wid, { items }, () => api.updateWatchlist(wl.id, { items }))
  },
  removeFromWatchlist: async (symbolId, watchlistId) => {
    const wid = watchlistId ?? get().activeWatchlistId
    const wl = get().watchlists.find((w) => w.id === wid)
    if (!wl) return
    const items = wl.items.filter((i) => i !== symbolId)
    await optimistic(set, get, wid, { items }, () => api.updateWatchlist(wl.id, { items }))
  },
  createWatchlist: async (name) => {
    const max = serverLimits()?.watchlists
    if (max !== undefined && get().watchlists.length >= max) { set({ error: t('You can have up to {n} lists.', { n: max }) }); return }
    try {
      const wl = await api.createWatchlist(name)
      set({ watchlists: [...get().watchlists, wl], activeWatchlistId: wl.id })
    } catch (e) { set({ error: (e as Error).message }) }
  },
  renameWatchlist: async (id, name) => {
    await optimistic(set, get, id, { name }, () => api.updateWatchlist(id, { name }))
  },
  deleteWatchlist: async (id) => {
    try { await api.deleteWatchlist(id) } catch (e) { set({ error: (e as Error).message }); return }
    const watchlists = get().watchlists.filter((w) => w.id !== id)
    set({ watchlists, activeWatchlistId: watchlists[0]?.id ?? null })
  },

  refreshLayouts: async () => {
    try { set({ layoutNames: (await api.layouts()).map((l) => l.name).filter((n) => n !== 'last') }) } catch { /* ignore */ }
  },
  saveLayoutAs: async (name) => {
    const max = serverLimits()?.layouts
    if (max !== undefined && !get().layoutNames.includes(name) && get().layoutNames.length + 1 >= max) {
      set({ error: t('You can save up to {n} layouts.', { n: max - 1 }) }); return
    }
    try { await api.putLayout(name, layoutState(get()) as unknown as Record<string, unknown>) } catch (e) { set({ error: (e as Error).message }); return }
    await get().refreshLayouts()
  },
  loadLayout: async (name) => {
    const { state } = await api.layout(name)
    const l = state as Partial<LayoutState>
    let symbol = get().symbol
    if (l.symbolId && l.symbolId !== symbol?.id) {
      try { symbol = await api.symbol(l.symbolId) } catch { /* keep current */ }
    }
    if (l.theme) document.documentElement.dataset.theme = l.theme
    set({
      symbol,
      interval: l.interval ?? get().interval,
      chartType: l.chartType ?? get().chartType,
      theme: l.theme ?? get().theme,
      timezone: l.timezone ?? get().timezone,
      indicators: liveIndicators(l.indicators) ?? get().indicators,
      selectedOverlayId: null, tool: null, sheet: null,
    })
    schedulePersist('symbolId', 'interval', 'chartType', 'theme', 'timezone', 'indicators')
  },
  deleteLayout: async (name) => {
    await api.deleteLayout(name)
    await get().refreshLayouts()
  },
}))
