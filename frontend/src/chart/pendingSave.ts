/** One debounced style save at a time (selection toolbar colour drags); deleting that drawing cancels it. */
let timer: number | null = null
let pendingId: string | null = null

export function scheduleSave(id: string, save: () => void, ms = 350) {
  if (timer) window.clearTimeout(timer)
  pendingId = id
  timer = window.setTimeout(() => { timer = null; pendingId = null; save() }, ms)
}

export function cancelPendingSave(id?: string) {
  if (timer && (!id || id === pendingId)) { window.clearTimeout(timer); timer = null; pendingId = null }
}
