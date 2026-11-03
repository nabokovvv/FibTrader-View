import { ChevronDown, Plus, X } from 'lucide-react'
import { serverLimits } from '../auth/session'
import { t, wlName } from '../i18n'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { ws } from '../api/ws'
import { tickerOf, type SymbolInfo } from '../lib/types'
import { useStore } from '../state/store'
import { Menu } from './TopBar'
import { fmtPct, fmtPrice, MarketBadge, symName } from './ui'

const infoCache = new Map<string, SymbolInfo>()
const EMPTY: string[] = []

export function Watchlist({ onAdd }: { onAdd: () => void }) {
  const watchlists = useStore((s) => s.watchlists)
  const activeId = useStore((s) => s.activeWatchlistId)
  const setActive = useStore((s) => s.setActiveWatchlist)
  const quotes = useStore((s) => s.quotes)
  const symbol = useStore((s) => s.symbol)
  const setSymbolById = useStore((s) => s.setSymbolById)
  const removeFromWatchlist = useStore((s) => s.removeFromWatchlist)
  const createWatchlist = useStore((s) => s.createWatchlist)
  const renameWatchlist = useStore((s) => s.renameWatchlist)
  const deleteWatchlist = useStore((s) => s.deleteWatchlist)
  const [infos, setInfos] = useState<Record<string, SymbolInfo>>({})
  const [editMode, setEditMode] = useState(false)

  const active = useMemo(() => watchlists.find((w) => w.id === activeId) ?? watchlists[0], [watchlists, activeId])
  const items = active?.items ?? EMPTY
  const maxItems = serverLimits()?.watchlist_items
  const full = maxItems !== undefined && items.length >= maxItems

  // resolve names/precision for rows (cached)
  useEffect(() => {
    let alive = true
    const missing = items.filter((id) => !infoCache.has(id))
    if (!missing.length) { setInfos(Object.fromEntries(items.map((id) => [id, infoCache.get(id)!]))); return }
    api.symbolsBatch(missing).then((found) => { for (const [id, s] of Object.entries(found)) infoCache.set(id, s) })
      .catch(() => null)
      .then(() => {
        if (alive) setInfos(Object.fromEntries(items.filter((id) => infoCache.has(id)).map((id) => [id, infoCache.get(id)!])))
      })
    return () => { alive = false }
  }, [items])

  // live quotes for the visible list + the chart symbol
  useEffect(() => {
    // the chart symbol goes first: if the server caps the list, the price in the top bar still updates
    const ids = new Set(symbol ? [symbol.id, ...items] : items)
    ws.setQuoteIds([...ids])
  }, [items, symbol])

  const rows = items.map((id) => {
    const info = infos[id]
    const q = quotes[id]
    const sym = info ? tickerOf(info) : id.slice(id.indexOf(':') + 1)
    const up = (q?.ch ?? 0) >= 0
    return (
      <li key={id}>
        <div
          role="button" tabIndex={0}
          className="group flex w-full cursor-pointer items-center gap-2 px-3 py-[7px] text-left hover:bg-raised"
          style={{ background: symbol?.id === id ? 'var(--raised)' : undefined }}
          onClick={() => void setSymbolById(id)}
          onKeyDown={(e) => { if (e.key === 'Enter') void setSymbolById(id) }}
        >
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate font-medium">{sym}</span>
              <MarketBadge market={id.split(':')[0]} currency={info?.currency} />
            </div>
            <div className="truncate text-[11px] text-muted">{symName(info?.name)}</div>
          </div>
          <div className="text-right">
            <div className="font-medium">{fmtPrice(q?.p, info?.price_precision ?? 2)}</div>
            <div className={`text-[11px] ${q ? (up ? 'num-up' : 'num-down') : 'text-faint'}`}>{q ? fmtPct(q.ch) : '…'}</div>
          </div>
          {editMode && (
            <button className="tb-btn !h-7 !min-w-7 text-muted" onClick={(e) => { e.stopPropagation(); void removeFromWatchlist(id) }} aria-label={t('Remove {name}', { name: sym })}><X /></button>
          )}
        </div>
      </li>
    )
  })

  return (
    <aside className="flex h-full flex-col">
      <div className="flex h-10 shrink-0 items-center gap-1 border-b border-line px-2">
        <Menu
          button={<span className="tb-btn !h-8 gap-1 !px-2 text-[13px] font-medium text-text">{active ? wlName(active.name) : t('Watchlist')}<ChevronDown className="!h-3 !w-3" /></span>}
          items={[
            ...watchlists.map((w) => ({ key: String(w.id), label: `${wlName(w.name)} (${w.items.length})`, active: w.id === active?.id, onClick: () => setActive(w.id) })),
            { key: 'new', label: t('+ New list'), onClick: () => { const n = window.prompt(t('List name')); if (n) void createWatchlist(n) } },
            ...(active ? [
              { key: 'rename', label: t('Rename list'), onClick: () => { const n = window.prompt(t('New name'), active.name); if (n) void renameWatchlist(active.id, n) } },
              { key: 'delete', label: t('Delete list'), onClick: () => { if (window.confirm(t('Delete “{name}”?', { name: wlName(active.name) }))) void deleteWatchlist(active.id) } },
            ] : []),
          ]}
        />
        <div className="ml-auto flex items-center">
          <button className="tb-btn text-[12px]" data-active={editMode} onClick={() => setEditMode((e) => !e)}>{t(editMode ? 'Done' : 'Edit')}</button>
          {maxItems !== undefined && active && (
            <span className="mr-1 text-[11px] tabular-nums" style={{ color: full ? 'var(--warn)' : 'var(--faint)' }}
              title={full ? t('This list is full ({n} symbols). Remove one or make a new list.', { n: maxItems }) : undefined}>
              {items.length}/{maxItems}
            </span>
          )}
          <button className="tb-btn disabled:opacity-40" onClick={onAdd} disabled={full}
            title={full ? t('This list is full ({n} symbols). Remove one or make a new list.', { n: maxItems ?? 0 }) : t('Add symbol')}><Plus /></button>
        </div>
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto py-1">
        {rows}
        {!items.length && <li className="px-4 py-8 text-center text-[12px] text-muted">{t('This list is empty. Add symbols with +.')}</li>}
      </ul>
    </aside>
  )
}
