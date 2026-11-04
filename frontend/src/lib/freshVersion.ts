import { flushPendingSaves } from '../state/store'

/* ------------------------------------------------------------------------------------------------------------
 * A page that stays open (a home-screen app on iOS is kept in memory and never reloads by itself, a tab left for
 * days) keeps running the build it started with. When it comes back to the front, compare its bundle with the one
 * the server's index.html names now; after a deploy, save pending changes and reload.
 * ---------------------------------------------------------------------------------------------------------- */
const ENTRY = /\/assets\/index-[\w-]+\.js/
const RETRY_KEY = 'fv-reloaded-for'

function runningEntry(): string | null {
  for (const s of document.querySelectorAll<HTMLScriptElement>('script[type="module"][src]')) {
    const m = new URL(s.src, location.href).pathname.match(ENTRY)
    if (m) return m[0]
  }
  return null   // dev server: nothing to compare
}

let lastCheck = 0
async function check() {
  const mine = runningEntry()
  if (!mine || Date.now() - lastCheck < 60_000) return
  lastCheck = Date.now()
  let latest: string | null = null
  try {
    const r = await fetch('/', { cache: 'no-store' })
    if (!r.ok) return
    latest = (await r.text()).match(ENTRY)?.[0] ?? null
  } catch { return }   // offline: try next time
  if (!latest || latest === mine) return
  // one reload per new build: if it does not take (a cache in between), do not loop
  try { if (sessionStorage.getItem(RETRY_KEY) === latest) return; sessionStorage.setItem(RETRY_KEY, latest) } catch { /* private mode */ }
  await flushPendingSaves()
  location.reload()
}

export function watchForNewVersion() {
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') void check() })
  window.addEventListener('pageshow', (e) => { if (e.persisted) void check() })   // restored from the back/forward cache
}
