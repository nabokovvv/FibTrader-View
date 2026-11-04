import type { Session, SupabaseClient } from '@supabase/supabase-js'

/**
 * Sign-in state. The private instance runs without auth (one local user); the public one uses Supabase Auth
 * (email code / magic link and Google). The backend tells which via /api/config.
 */
export interface AppConfig {
  auth: 'none' | 'supabase'
  supabase_url: string | null
  supabase_key: string | null
  markets: string[]
  i18n?: boolean
  limits?: ServerLimits
  features?: Features
}

/** Optional server features (older servers send none). */
export interface Features { alerts: string[]; telegram: boolean; feedback: boolean; shares: boolean; derivs: boolean }
let features: Features = { alerts: [], telegram: false, feedback: false, shares: false, derivs: false }
export const serverFeatures = () => features

export interface AuthUser { id: string; email: string | null; name: string | null; avatar: string | null }

let client: SupabaseClient | null = null
let token: string | null = null
let user: AuthUser | null = null
let onSignedOut: (() => void) | null = null
let initialising: Promise<AuthUser | null> | null = null
const listeners = new Set<(u: AuthUser | null) => void>()

/** The signed-in user (null on the private instance or when signed out). */
export const currentUser = () => user

export async function loadConfig(): Promise<AppConfig> {
  const r = await fetch('/api/config')
  if (!r.ok) throw new Error(`config: ${r.status}`)
  const cfg = (await r.json()) as AppConfig
  markets = cfg.markets ?? []
  limits = cfg.limits ?? null
  if (cfg.features) features = cfg.features
  return cfg
}

/** Access token for API calls (null on the private instance or when signed out); refreshed when close to expiry. */
export async function accessToken(): Promise<string | null> {
  if (!client) return null
  const { data } = await client.auth.getSession()
  token = data.session?.access_token ?? null
  return token
}

export const authEnabled = () => client !== null

export interface ServerLimits { watchlists: number; watchlist_items: number; drawings: number; drawings_per_symbol: number; layouts: number; alerts?: number; feedback_per_day?: number }
let limits: ServerLimits | null = null
/** Per-account limits the server enforces (null = unknown, e.g. an older server). */
export const serverLimits = () => limits

let markets: string[] = []
/** Markets the server serves (from /api/config). */
export const availableMarkets = () => markets

function userOf(s: Session | null): AuthUser | null {
  if (!s) return null
  const m = s.user.user_metadata ?? {}
  return {
    id: s.user.id,
    email: s.user.email ?? null,
    name: (m.full_name as string | undefined) ?? (m.name as string | undefined) ?? null,
    avatar: (m.avatar_url as string | undefined) ?? (m.picture as string | undefined) ?? null,
  }
}

/** Subscribe to sign-in / sign-out (also from other tabs); returns the unsubscribe function. */
export function onUserChange(fn: (u: AuthUser | null) => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/** Creates the Supabase client once (safe under React StrictMode double effects). */
export function initAuth(cfg: AppConfig): Promise<AuthUser | null> {
  if (cfg.auth !== 'supabase' || !cfg.supabase_url || !cfg.supabase_key) return Promise.resolve(null)
  initialising ??= (async () => {
    const { createClient } = await import('@supabase/supabase-js')
    const c = createClient(cfg.supabase_url!, cfg.supabase_key!, {
      auth: { flowType: 'pkce', detectSessionInUrl: true, persistSession: true, autoRefreshToken: true },
    })
    client = c
    const { data } = await c.auth.getSession()
    token = data.session?.access_token ?? null
    user = userOf(data.session)
    c.auth.onAuthStateChange((_event, session) => {
      token = session?.access_token ?? null
      const next = userOf(session)
      if (next?.id === user?.id) { user = next; return }  // token refresh, same person
      user = next
      listeners.forEach((fn) => fn(next))
    })
    onSignedOut = () => { void c.auth.signOut({ scope: 'local' }) }
    return user
  })()
  return initialising
}

const need = () => {
  if (!client) throw new Error('sign-in is not configured')
  return client
}

const redirectTo = () => `${window.location.origin}/`

export async function sendEmailCode(email: string): Promise<void> {
  const { error } = await need().auth.signInWithOtp({ email, options: { emailRedirectTo: redirectTo(), shouldCreateUser: true } })
  if (error) throw error
}

export async function verifyEmailCode(email: string, code: string): Promise<void> {
  const { error } = await need().auth.verifyOtp({ email, token: code, type: 'email' })
  if (error) throw error
}

export async function signInWithGoogle(): Promise<void> {
  const { error } = await need().auth.signInWithOAuth({ provider: 'google', options: { redirectTo: redirectTo() } })
  if (error) throw error
}

export async function signOut(): Promise<void> {
  await need().auth.signOut()
}

let refreshing: Promise<string | null> | null = null
/** The API refused `rejected`: ask Supabase for a new access token (one request shared by all callers). Returns the
 *  new token, or null when the session is really gone (revoked, refresh token expired). A network failure is not a
 *  rejection: then the old token comes back and the caller does not sign the user out. */
export async function refreshAfterReject(rejected: string): Promise<string | null> {
  if (!client) return null
  if (token && token !== rejected) return token          // someone already refreshed meanwhile
  refreshing ??= (async () => {
    try {
      const { data, error } = await client!.auth.refreshSession()
      if (data.session) return (token = data.session.access_token)
      // only an auth refusal means the session is gone; network errors, 429 and 5xx keep it
      const status = (error as { status?: number } | null)?.status ?? 0
      return [400, 401, 403, 404].includes(status) ? null : rejected
    } catch {
      return rejected
    } finally {
      window.setTimeout(() => { refreshing = null }, 0)
    }
  })()
  return refreshing
}

/** Supabase refused to refresh the session (revoked, refresh token expired): drop it locally. */
export function sessionRejected() {
  onSignedOut?.()
}
