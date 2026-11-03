import { accessToken, sessionRejected } from '../auth/session'
import { getLang, t } from '../i18n'
import type { BarsResponse, DrawingRecord, Interval, SymbolInfo, Watchlist } from '../lib/types'

/** An API failure: `status` is the HTTP code, `message` is already in the UI language. */
export class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string, message: string) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

const FRIENDLY: Record<number, string> = {
  401: 'Sign in again.',
  409: 'Limit reached.',
  413: 'Too large.',
  429: 'Too many requests. Wait a few seconds.',
  502: 'The data source is not answering. Try again later.',
  503: 'The server is busy. Try again in a moment.',
  507: 'Storage is full. Try again later.',
}

export async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const token = await accessToken()
  const r = await fetch(url, {
    ...init,
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}), ...(init?.headers ?? {}) },
  })
  if (r.status === 401 && token) sessionRejected()
  if (!r.ok) {
    let detail = r.statusText
    try { detail = (await r.json()).detail ?? detail } catch { /* ignore */ }
    const friendly = FRIENDLY[r.status] ?? (r.status === 404 && /unknown symbol/.test(detail) ? 'Unknown symbol.' : null)
    // English keeps the server's precise wording; other languages get the translated summary
    const message = friendly && getLang() !== 'en' ? t(friendly) : `${r.status}: ${detail}`
    throw new ApiError(r.status, detail, message)
  }
  return r.json() as Promise<T>
}

export const q = (params: Record<string, string | number | undefined | null>) =>
  Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join('&')

export const api = {
  health: () => req<{ ok: boolean; markets: string[] }>('/api/health'),
  searchSymbols: (query: string, limit = 40) =>
    req<{ results: SymbolInfo[] }>(`/api/symbols/search?${q({ q: query, limit })}`).then((r) => r.results),
  symbol: (id: string) => req<SymbolInfo>(`/api/symbol?${q({ id })}`),
  /** Metadata for many symbols, 100 per request (watchlists). Keyed by the id that was asked for. */
  symbolsBatch: async (ids: string[]) => {
    const out: Record<string, SymbolInfo> = {}
    for (let i = 0; i < ids.length; i += 100) {
      const r = await req<{ symbols: Array<SymbolInfo & { requested: string }> }>(`/api/symbols?${q({ ids: ids.slice(i, i + 100).join(',') })}`)
      for (const s of r.symbols) out[s.requested] = s
    }
    return out
  },
  bars: (id: string, interval: Interval, to?: number | null, limit = 500) =>
    req<BarsResponse>(`/api/bars?${q({ id, interval, to, limit })}`),

  /** All drawings of a symbol; the server pages them (bounded memory per request). */
  drawings: async (symbol: string) => {
    const all: DrawingRecord[] = []
    let offset: number | null = 0
    for (let guard = 0; offset !== null && guard < 200; guard++) {
      const r: { drawings: DrawingRecord[]; next_offset?: number | null } = await req(`/api/drawings?${q({ symbol, offset })}`)
      all.push(...r.drawings)
      offset = r.next_offset ?? null
    }
    return all
  },
  putDrawing: (id: string, symbol: string, name: string, data: Record<string, unknown>) =>
    req(`/api/drawings/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ symbol, name, data }) }),
  /** Rewrite existing drawings of one symbol in batches of 100 (lock all / hide all). */
  putDrawings: async (symbol: string, items: Array<{ id: string; name: string; data: Record<string, unknown> }>) => {
    // batches of at most 100 drawings and ~400 KB (the server takes 512 KB per request)
    let batch: typeof items = [], size = 0
    const flush = async () => {
      if (batch.length) await req('/api/drawings', { method: 'PUT', body: JSON.stringify({ symbol, items: batch }) })
      batch = []; size = 0
    }
    for (const it of items) {
      const n = JSON.stringify(it).length
      if (batch.length >= 100 || (batch.length && size + n > 400_000)) await flush()
      batch.push(it); size += n
    }
    await flush()
  },
  deleteDrawing: (id: string) => req(`/api/drawings/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  deleteDrawings: (symbol: string) => req(`/api/drawings?${q({ symbol })}`, { method: 'DELETE' }),

  watchlists: () => req<{ watchlists: Watchlist[] }>('/api/watchlists').then((r) => r.watchlists),
  createWatchlist: (name: string, items: string[] = []) =>
    req<Watchlist>('/api/watchlists', { method: 'POST', body: JSON.stringify({ name, items }) }),
  updateWatchlist: (id: number, patch: Partial<Pick<Watchlist, 'name' | 'items' | 'position'>>) =>
    req(`/api/watchlists/${id}`, { method: 'PUT', body: JSON.stringify(patch) }),
  deleteWatchlist: (id: number) => req(`/api/watchlists/${id}`, { method: 'DELETE' }),

  layout: (name: string) => req<{ name: string; state: Record<string, unknown> }>(`/api/layouts/${encodeURIComponent(name)}`),
  layouts: () => req<{ layouts: Array<{ name: string; updated_at: number }> }>('/api/layouts').then((r) => r.layouts),
  putLayout: (name: string, state: Record<string, unknown>) =>
    req(`/api/layouts/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify({ state }) }),
  deleteLayout: (name: string) => req(`/api/layouts/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  settings: () => req<Record<string, unknown>>('/api/settings'),
  putSettings: (values: Record<string, unknown>) => req('/api/settings', { method: 'PUT', body: JSON.stringify(values) }),

  alerts: () => req<{ alerts: Alert[]; markets: string[]; telegram: boolean }>('/api/alerts'),
  createAlert: (a: AlertIn) => req<{ alert: Alert }>('/api/alerts', { method: 'POST', body: JSON.stringify(a) }).then((r) => r.alert),
  updateAlert: (id: number, patch: Partial<Pick<Alert, 'active' | 'level' | 'cond' | 'note'>>) =>
    req<{ alert: Alert }>(`/api/alerts/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }).then((r) => r.alert),
  deleteAlert: (id: number) => req(`/api/alerts/${id}`, { method: 'DELETE' }),
  telegram: () => req<{ available: boolean; linked: boolean; bot: string | null }>('/api/telegram'),
  telegramLink: (lang: string) => req<{ url: string }>('/api/telegram/link', { method: 'POST', body: JSON.stringify({ lang }) }),
  telegramUnlink: () => req('/api/telegram', { method: 'DELETE' }),

  derivs: (kind: 'funding' | 'oi' | 'liq', id: string, interval: Interval, from: number, to: number) =>
    req<DerivsResponse>(`/api/derivs/${kind}?${q({ id, interval, from, to })}`),

  sendFeedback: (kind: 'idea' | 'bug', text: string, context?: Record<string, string>) =>
    req<{ ok: boolean; public: boolean }>('/api/feedback', { method: 'POST', body: JSON.stringify({ kind, text, context }) }),

  createShare: (state: Record<string, unknown>) =>
    req<{ id: string; expires_in_days: number }>('/api/shares', { method: 'POST', body: JSON.stringify({ state }) }),
  share: (id: string) => req<{ state: Record<string, unknown>; created_at: number; expires_at: number }>(`/api/shares/${encodeURIComponent(id)}`),
}

export type AlertCond = 'cross' | 'up' | 'down'
export interface Alert {
  id: number; symbol: string; cond: AlertCond; level: number | null; line: [number, number, number, number] | null
  drawing_id: string | null; note: string; active: boolean; created_at: number; fired_at: number | null; fired_price: number | null
}
export interface AlertIn { symbol: string; cond: AlertCond; level?: number; line?: number[]; drawing_id?: string; note?: string }
export interface DerivsResponse { points: number[][]; covered_from: number; exhausted: boolean; recorded?: boolean; unavailable?: string }
