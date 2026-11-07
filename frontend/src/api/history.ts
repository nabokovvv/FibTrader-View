import { create } from 'zustand'
import { ApiError } from './client'

/* ------------------------------------------------------------------------------------------------------------
 * Older history on the public site (backend/app/history.py). The newest bars of every chart are open to everyone;
 * older ones need an account, and an account that takes far more than anyone scrolls by hand is first paced, then
 * closed for a day. One protocol for every loader (chart, bar replay, shared chart, crypto panels):
 *  - 'slow'  (429 + Retry-After): wait and ask again — one request waiting per loader, a quiet hint meanwhile;
 *  - 'login_required' (403) / 'stopped' (429 + until), or a page cut at the edge (`locked`): no more older bars for
 *    now — never "the end of history": while the lock holds, the chart's loader answers "more later" without asking
 *    the server (historyClosed), the chart shows why (HistoryNotice), and once a stop is over the next scroll to the
 *    edge loads again; the newest bars keep working throughout;
 *  - other 429 / 503 (busy server): a few retries, honouring Retry-After.
 * ---------------------------------------------------------------------------------------------------------- */

export type HistoryLock = { kind: 'login' } | { kind: 'stopped'; until: number }

interface HistoryAccess {
  /** older history is closed for the chart on screen (and why) */
  lock: HistoryLock | null
  /** the notice about it was closed (it comes back when the chart reaches the edge again) */
  hidden: boolean
  /** requests waiting out a 'slow' answer right now */
  waiting: number
}
export const useHistoryAccess = create<HistoryAccess>(() => ({ lock: null, hidden: false, waiting: 0 }))

export class HistoryLocked extends Error {
  lock: HistoryLock
  constructor(lock: HistoryLock) {
    super(lock.kind === 'login' ? 'sign in to see older history' : 'history limit reached')
    this.lock = lock
  }
}

/** Thrown when the loader's chart changed while it waited: drop the answer. */
export class Stale extends Error {}

/** The lock an error or a cut page stands for (null: something else). */
export function lockOf(x: unknown): HistoryLock | null {
  if (x instanceof ApiError) {
    if (x.code === 'login_required') return { kind: 'login' }
    if (x.code === 'stopped') return { kind: 'stopped', until: x.until ?? Date.now() + 3_600_000 }
    return null
  }
  const r = x as { locked?: string; until?: number } | null
  if (r?.locked === 'login') return { kind: 'login' }
  if (r?.locked === 'stopped') return { kind: 'stopped', until: r.until ?? Date.now() + 3_600_000 }
  return null
}

export function setHistoryLock(lock: HistoryLock | null) {
  const s = useHistoryAccess.getState()
  if (JSON.stringify(s.lock) !== JSON.stringify(lock) || s.hidden) useHistoryAccess.setState({ lock, hidden: false })
}

/** Older history is closed right now: a loader at the edge answers "more later" without asking the server (and the
 *  notice shows again). A stop ends by itself; a guest's lock lasts until signing in. */
export function historyClosed(): boolean {
  const { lock, hidden } = useHistoryAccess.getState()
  if (!lock) return false
  if (lock.kind === 'stopped' && lock.until <= Date.now()) { useHistoryAccess.setState({ lock: null, hidden: false }); return false }
  if (hidden) useHistoryAccess.setState({ hidden: false })
  return true
}

const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms))

/** Wait `ms` unless the loader goes stale meanwhile (checked every half second). */
async function wait(ms: number, stale: () => boolean) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (stale()) throw new Stale()
    await sleep(Math.min(500, end - Date.now()))
  }
  if (stale()) throw new Stale()
}

/**
 * Run a request for bars that may be older history. Waits out 'slow' answers as long as the loader stays current,
 * retries a busy server up to three times, turns the sign-in / daily-limit refusals into HistoryLocked (and shows
 * the lock); a page the server cut at the edge shows the lock too. Throws Stale if the chart moved on.
 */
export async function historyFetch<T>(fn: () => Promise<T>, stale: () => boolean, showCut = true): Promise<T> {
  for (let busy = 0; ;) {
    try {
      const r = await fn()
      if (stale()) throw new Stale()        // (an answer for a chart already left must not show its lock)
      const lock = showCut ? lockOf(r) : null
      if (lock) setHistoryLock(lock)
      return r
    } catch (e) {
      if (e instanceof Stale) throw e
      if (stale()) throw new Stale()
      const lock = lockOf(e)
      if (lock) { setHistoryLock(lock); throw new HistoryLocked(lock) }
      if (!(e instanceof ApiError)) throw e
      if (e.code === 'slow') {
        useHistoryAccess.setState((s) => ({ waiting: s.waiting + 1 }))
        try { await wait((e.retryAfter ?? 5) * 1000 + Math.random() * 400, stale) } finally {
          useHistoryAccess.setState((s) => ({ waiting: Math.max(0, s.waiting - 1) }))
        }
        continue
      }
      if (![429, 503].includes(e.status) || busy >= 3) throw e
      busy++
      await wait(e.retryAfter ? e.retryAfter * 1000 : 1500 * busy + Math.random() * 500, stale)
    }
  }
}

/** "14:05" today, "7 Nov 14:05" on another day. */
export function untilText(ms: number): string {
  const d = new Date(ms)
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toDateString() === new Date().toDateString() ? time : `${d.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${time}`
}
