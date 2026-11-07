import { X } from 'lucide-react'
import { useEffect } from 'react'
import { setHistoryLock, untilText, useHistoryAccess } from '../api/history'
import { t } from '../i18n'

/**
 * Why the chart stops at its left edge when older history is closed (a guest on a shared chart: sign in; an account
 * past the daily history limit: until when), and a quiet hint while older bars come in more slowly.
 */
export function HistoryNotice() {
  const lock = useHistoryAccess((s) => s.lock)
  const hidden = useHistoryAccess((s) => s.hidden)
  const slow = useHistoryAccess((s) => s.waiting > 0)

  // the limit ends by itself: drop the lock then (the next scroll to the edge loads again)
  useEffect(() => {
    if (lock?.kind !== 'stopped') return
    const ms = lock.until - Date.now()
    if (ms <= 0) { setHistoryLock(null); return }
    const id = window.setTimeout(() => setHistoryLock(null), Math.min(ms, 2 ** 31 - 1))
    return () => window.clearTimeout(id)
  }, [lock])

  if (lock && !hidden) {
    return (
      <div className="pointer-events-auto absolute left-2 top-1/2 z-10 flex max-w-[260px] -translate-y-1/2 items-start gap-2 rounded-lg border border-line bg-panel px-3 py-2 text-[12px] leading-snug text-muted shadow-lg"
        role="status">
        <div className="flex flex-col gap-2">
          {lock.kind === 'login'
            ? <>
                <span>{t('Older history is open after signing in.')}</span>
                <a className="tb-btn !h-8 self-start !px-3 text-[12px]" style={{ background: 'var(--brand)', color: '#fff' }} href="/">{t('Sign in')}</a>
              </>
            : <span>{t('The history limit is used up. Older bars open again at {time}.', { time: untilText(lock.until) })}</span>}
        </div>
        <button className="tb-btn !h-6 !w-6 shrink-0 !p-0" onClick={() => useHistoryAccess.setState({ hidden: true })} title={t('Close')} aria-label={t('Close')}><X /></button>
      </div>
    )
  }
  if (slow) {
    return (
      <div className="pointer-events-none absolute bottom-12 left-2 z-10 flex items-center gap-2 rounded-md border border-line bg-panel px-2 py-1 text-[11px] text-muted" role="status">
        <span className="pulse inline-block h-1.5 w-1.5 rounded-full" style={{ background: 'var(--warn)' }} />
        {t('Loading older history more slowly…')}
      </div>
    )
  }
  return null
}
