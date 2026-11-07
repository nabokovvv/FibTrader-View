import { Check, Loader2, Plus, Search, X } from 'lucide-react'
import { t, wlName } from '../i18n'
import { availableMarkets } from '../auth/session'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { MARKET_LABEL, tickerOf, type SymbolInfo } from '../lib/types'
import { useStore } from '../state/store'
import { MarketBadge, Modal, symName } from './ui'

const ALL_FILTERS = ['all', 'bybit', 'us', 'moex', 'yahoo', 'hl', 'bq', 'bybit-spot'] as const
type Filter = (typeof ALL_FILTERS)[number]
/** Only the markets this server serves (the public instance has Bybit only). */
const marketFilters = (): Filter[] => {
  const on = availableMarkets()
  return on.length ? ALL_FILTERS.filter((m) => m === 'all' || on.includes(m)) : [...ALL_FILTERS]
}
/** What this server offers: crypto only, crypto + Hyperliquid perps (public instance), or every market. */
type Scope = 'crypto' | 'hl' | 'all'
const marketScope = (): Scope => {
  const on = availableMarkets()
  if (!on.length) return 'all'
  if (on.every((m) => m.startsWith('bybit'))) return 'crypto'
  return on.every((m) => m.startsWith('bybit') || m === 'hl' || m === 'bq') ? 'hl' : 'all'
}
const PLACEHOLDER: Record<Scope, string> = {
  crypto: 'Ticker or coin: BTC, ETH, SOL, PEPE…',
  hl: 'Ticker or name: BTC, TSLA, gold, SPX, oil…',
  all: 'Ticker or name: BTC, SBER, Apple, gold, SPX…',
}
const SHORTCUTS: Record<Scope, string[]> = {
  crypto: ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'HYPE'],
  hl: ['BTC', 'ETH', 'TSLA', 'NVDA', 'GOLD', 'SPX', 'OIL'],
  all: ['GOLD', 'SPX', 'BRENT', 'DAX', 'IMOEX', 'EURUSD'],
}
const INTRO: Record<Scope, string> = {
  crypto: 'Search Bybit perpetuals and spot.',
  hl: 'Search Bybit perps and spot, plus stocks, indices, commodities and FX as Hyperliquid perpetuals.',
  all: 'Search Bybit perps and spot, US stocks, MOEX, global indices, commodities and FX.',
}

/** Symbol search dialog. In "add" mode results get a + button that adds to the active watchlist. */
export function SymbolSearch({ onClose, mode = 'open' }: { onClose: () => void; mode?: 'open' | 'add' }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<SymbolInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<Filter>('all')
  const filters = useMemo(marketFilters, [])
  const scope = useMemo(marketScope, [])
  const crypto = scope === 'crypto'
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const setSymbol = useStore((s) => s.setSymbol)
  const addToWatchlist = useStore((s) => s.addToWatchlist)
  const activeWl = useStore((s) => s.watchlists.find((w) => w.id === s.activeWatchlistId))

  useEffect(() => { inputRef.current?.focus() }, [])

  useEffect(() => {
    const query = q.trim()
    if (!query) { setResults([]); return }
    let alive = true
    setLoading(true)
    const t = window.setTimeout(() => {
      api.searchSymbols(query, 60).then((r) => { if (alive) { setResults(r); setCursor(0) } })
        .catch(() => { if (alive) setResults([]) })
        .finally(() => { if (alive) setLoading(false) })
    }, 160)
    return () => { alive = false; window.clearTimeout(t) }
  }, [q])

  const shown = useMemo(() => (filter === 'all' ? results : results.filter((r) => r.market === filter)), [results, filter])

  const pick = (s: SymbolInfo) => {
    if (mode === 'add') { void addToWatchlist(s.id); return }
    setSymbol(s)
    onClose()
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, shown.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)) }
    else if (e.key === 'Enter' && shown[cursor]) pick(shown[cursor])
  }

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: results.length }
    for (const r of results) c[r.market] = (c[r.market] ?? 0) + 1
    return c
  }, [results])

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-row="${cursor}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  const query = q.trim()

  return (
    <Modal title={mode === 'add' ? t('Add to “{name}”', { name: activeWl ? wlName(activeWl.name) : t('Watchlist') }) : t('Symbol search')} onClose={onClose} width={640}>
      <div className="sticky top-0 z-10 border-b border-line bg-panel px-4 pb-3 pt-3">
        <div className="field h-11 px-3">
          <Search className="h-4 w-4 shrink-0 text-muted" />
          <input
            ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
            placeholder={t(PLACEHOLDER[scope])}
            className="h-full flex-1 text-[15px] placeholder:text-faint"
            autoCapitalize="off" autoCorrect="off" spellCheck={false}
          />
          {loading && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-faint" />}
          {q && !loading && (
            <button className="tb-btn !h-7 !min-w-7 !px-0" onClick={() => { setQ(''); inputRef.current?.focus() }} aria-label={t('Clear search')}><X /></button>
          )}
        </div>
        <div className="-mx-4 mt-3 flex gap-1.5 overflow-x-auto px-4">
          {filters.length > 2 && filters.map((m) => (
            <button key={m} className="chip" data-active={filter === m} onClick={() => { setFilter(m); setCursor(0) }}>
              {m === 'all' ? t('All') : t(MARKET_LABEL[m] ?? m)}
              {query && counts[m] ? <span className="count">{counts[m]}</span> : null}
            </button>
          ))}
        </div>
      </div>
      <ul ref={listRef} className="p-2">
        {shown.map((s, i) => {
          const inList = mode === 'add' && activeWl?.items.includes(s.id)
          const active = i === cursor
          return (
            <li key={s.id}>
              <button
                data-row={i}
                className="flex h-12 w-full items-center gap-3 rounded-lg px-2.5 text-left"
                style={{ background: active ? 'var(--raised)' : undefined }}
                onMouseMove={() => { if (!active) setCursor(i) }} onClick={() => pick(s)}
              >
                <Avatar s={s} />
                <div className="flex min-w-0 flex-1 flex-col sm:flex-row sm:items-baseline sm:gap-3">
                  <span className="shrink-0 truncate text-[14px] font-semibold tracking-[0.01em] sm:w-32"><Highlight text={tickerOf(s)} query={query} /></span>
                  <span className="min-w-0 truncate text-[12.5px] text-muted sm:text-[13px]">{symName(s.name)}</span>
                </div>
                <span className="hidden shrink-0 text-[11px] text-faint sm:inline">{s.exchange}</span>
                <MarketBadge market={s.market} currency={s.currency} highlight />
                {mode === 'add' && (
                  <span className="tb-btn !h-7 !min-w-7 !px-0" style={{ color: inList ? 'var(--up)' : undefined }}>
                    {inList ? <Check /> : <Plus />}
                  </span>
                )}
              </button>
            </li>
          )
        })}
        {!shown.length && query && !loading && (
          <li className="px-4 py-10 text-center text-muted">
            {filter !== 'all' ? t('Nothing matches “{q}” in {market}.', { q: query, market: t(MARKET_LABEL[filter] ?? filter) }) : t('Nothing matches “{q}”.', { q: query })}
            <div className="mt-1 text-[12px] text-faint">{t(crypto ? 'Try the coin ticker, like BTC or 1000PEPE.' : scope === 'hl' ? 'Try a ticker like TSLA, GOLD or SP500.' : filter !== 'all' ? 'Try a full ticker like GC=F or ASML.AS, or switch to All.' : 'Try a full ticker like GC=F or ASML.AS.')}</div>
          </li>
        )}
        {!query && (
          <li className="px-3 py-6 text-[12.5px] leading-6 text-muted">
            {t(INTRO[scope])}
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className="text-faint">{t('Shortcuts:')}</span>
              {SHORTCUTS[scope].map((a) => (
                <button key={a} className="chip !h-6 !px-2 !text-[11px]" onClick={() => { setQ(a); inputRef.current?.focus() }}>{a}</button>
              ))}
            </div>
            {availableMarkets().includes('hl') && (
              <p className="mt-3 text-[12px] leading-5 text-faint">
                {t('HL marks Hyperliquid perpetuals. They trade around the clock, so prices can differ from the exchange price of the stock, index or commodity.')}
              </p>
            )}
            {availableMarkets().includes('bq') && (
              <p className="mt-2 text-[12px] leading-5 text-faint">
                {t('CFD marks prices of a CFD broker from biquote.io: they follow the market during its trading hours. Their intraday history is short for now and grows every day.')}
              </p>
            )}
          </li>
        )}
      </ul>
    </Modal>
  )
}

/** Two-letter monogram: base asset for crypto pairs, ticker head otherwise. */
function Avatar({ s }: { s: SymbolInfo }) {
  const t = tickerOf(s)
  const base = t.replace(/^\^/, '').replace(/^\d{3,}(?=[A-Z])/, '').replace(/(USDT|USDC|USD|PERP)$/, '') || t   // 1000PEPEUSDT -> PEPE
  const text = base.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase()
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line text-[11px] font-semibold text-muted" style={{ background: 'var(--bg)' }}>
      {text}
    </span>
  )
}

function Highlight({ text, query }: { text: string; query: string }) {
  const i = query ? text.toUpperCase().indexOf(query.toUpperCase()) : -1
  if (i < 0) return <>{text}</>
  return <>{text.slice(0, i)}<span style={{ color: 'var(--accent)' }}>{text.slice(i, i + query.length)}</span>{text.slice(i + query.length)}</>
}
