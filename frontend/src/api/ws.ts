import type { Bar, Interval, Quote } from '../lib/types'

type BarHandler = (bar: Bar, closed: boolean) => void
type QuoteHandler = (quotes: Record<string, Quote>) => void
type StatusHandler = (status: WsStatus) => void
export type WsStatus = 'connecting' | 'open' | 'closed'

/** Single multiplexed WebSocket to the backend hub with automatic reconnect + resubscribe. */
class WsClient {
  private ws: WebSocket | null = null
  private barHandlers = new Map<string, Set<BarHandler>>()
  private quoteHandlers = new Set<QuoteHandler>()
  private errorHandlers = new Set<(msg: string) => void>()
  private statusHandlers = new Set<StatusHandler>()
  private quoteIds = new Set<string>()
  private retry = 1000
  private timer: number | null = null
  status: WsStatus = 'closed'

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.setStatus('connecting')
    const ws = new WebSocket(`${proto}://${location.host}/ws`)
    this.ws = ws
    ws.onopen = () => {
      this.retry = 1000
      this.setStatus('open')
      for (const key of this.barHandlers.keys()) {
        const [id, interval] = splitKey(key)
        this.send({ op: 'sub', ch: 'bars', id, interval })
      }
      if (this.quoteIds.size) this.send({ op: 'set', ch: 'quotes', ids: [...this.quoteIds] })
    }
    ws.onmessage = (e) => {
      let msg: { ch: string; id?: string; interval?: string; bar?: Bar; closed?: boolean; q?: Record<string, Quote>; msg?: string }
      try { msg = JSON.parse(e.data) } catch { return }
      if (msg.ch === 'bars' && msg.id && msg.interval && msg.bar) {
        const hs = this.barHandlers.get(`${msg.id}|${msg.interval}`)
        hs?.forEach((h) => h(msg.bar as Bar, !!msg.closed))
      } else if (msg.ch === 'quotes' && msg.q) {
        this.quoteHandlers.forEach((h) => h(msg.q as Record<string, Quote>))
      } else if (msg.ch === 'error' && msg.msg) {
        this.errorHandlers.forEach((h) => h(msg.msg as string))
      }
    }
    ws.onclose = () => {
      this.setStatus('closed')
      this.ws = null
      if (this.timer) window.clearTimeout(this.timer)
      this.timer = window.setTimeout(() => this.connect(), this.retry)
      this.retry = Math.min(this.retry * 2, 15000)
    }
    ws.onerror = () => ws.close()
  }

  private send(msg: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg))
  }

  private setStatus(s: WsStatus) {
    this.status = s
    this.statusHandlers.forEach((h) => h(s))
  }

  onStatus(h: StatusHandler) {
    this.statusHandlers.add(h)
    h(this.status)
    return () => { this.statusHandlers.delete(h) }
  }

  subscribeBars(id: string, interval: Interval, h: BarHandler) {
    const key = `${id}|${interval}`
    let hs = this.barHandlers.get(key)
    if (!hs) {
      hs = new Set()
      this.barHandlers.set(key, hs)
      this.send({ op: 'sub', ch: 'bars', id, interval })
    }
    hs.add(h)
    return () => {
      hs!.delete(h)
      if (hs!.size === 0) {
        this.barHandlers.delete(key)
        this.send({ op: 'unsub', ch: 'bars', id, interval })
      }
    }
  }

  setQuoteIds(ids: string[]) {
    const next = new Set(ids)
    if (sameSet(next, this.quoteIds)) return
    this.quoteIds = next
    this.send({ op: 'set', ch: 'quotes', ids })
  }

  onQuotes(h: QuoteHandler) {
    this.quoteHandlers.add(h)
    return () => { this.quoteHandlers.delete(h) }
  }

  /** Server-side refusals (e.g. a subscription cap). */
  onError(h: (msg: string) => void) {
    this.errorHandlers.add(h)
    return () => { this.errorHandlers.delete(h) }
  }
}

function splitKey(key: string): [string, string] {
  const i = key.lastIndexOf('|')
  return [key.slice(0, i), key.slice(i + 1)]
}
function sameSet(a: Set<string>, b: Set<string>) {
  if (a.size !== b.size) return false
  for (const x of a) if (!b.has(x)) return false
  return true
}

export const ws = new WsClient()
