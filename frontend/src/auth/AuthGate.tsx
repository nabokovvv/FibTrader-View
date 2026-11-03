import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import { initLang, t } from '../i18n'
import { LoginScreen } from './LoginScreen'
import { initAuth, loadConfig, onUserChange, type AuthUser } from './session'

const ShareView = lazy(() => import('../share/ShareView').then((m) => ({ default: m.ShareView })))
/** /s/<id>: a shared chart, open to everyone (no sign-in). */
const shareId = /^\/s\/([A-Za-z0-9_-]{8,32})\/?$/.exec(window.location.pathname)?.[1] ?? null

type Phase = { kind: 'share' } | { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'out' } | { kind: 'in'; user: AuthUser | null }

/**
 * Renders the app only once we know who the user is. Private instance: straight through.
 * Public instance: the sign-in screen until there is a session. Any later change of user (sign-out,
 * sign-in in another tab) reloads the page so no state of the previous account survives.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' })

  useEffect(() => {
    let alive = true
    let shown: string | null | undefined  // id of the account whose app is on screen
    const off = onUserChange((u) => {
      if (!alive) return
      if (shown !== undefined) { window.location.reload(); return }
      setPhase(u ? { kind: 'in', user: u } : { kind: 'out' })
      if (u) shown = u.id
    })
    loadConfig()
      .then(async (cfg) => {
        initLang(!!cfg.i18n)
        if (shareId) { if (alive) setPhase({ kind: 'share' }); return }
        if (cfg.auth === 'none') { if (alive) setPhase({ kind: 'in', user: null }); return }
        const u = await initAuth(cfg)
        if (!alive) return
        setPhase(u ? { kind: 'in', user: u } : { kind: 'out' })
        if (u) shown = u.id
      })
      .catch((e: unknown) => { if (alive) setPhase({ kind: 'error', message: e instanceof Error ? e.message : String(e) }) })
    return () => { alive = false; off() }
  }, [])

  if (phase.kind === 'loading') return <div className="flex h-full items-center justify-center bg-bg text-muted" aria-busy="true">…</div>
  if (phase.kind === 'error') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-bg px-6 text-center text-muted">
        <p>{t('The server did not answer ({message}).', { message: phase.message })}</p>
        <button className="tb-btn !px-3" onClick={() => window.location.reload()}>{t('Try again')}</button>
      </div>
    )
  }
  if (phase.kind === 'share' && shareId) return <Suspense fallback={null}><ShareView id={shareId} /></Suspense>
  if (phase.kind === 'out') return <LoginScreen />
  return <>{children}</>
}
