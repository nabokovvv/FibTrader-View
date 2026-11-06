import { Eye, EyeOff, Lock, LockOpen, Magnet, Trash2, Undo2 } from 'lucide-react'
import { cancelPendingSave } from '../chart/pendingSave'
import { t as tr } from '../i18n'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import { chartHolder } from '../chart/ChartView'
import { MEASURE_ICON, TOOL_GROUPS, TOOLS, type Icon, type ToolEntry } from '../chart/overlays'
import { useStore } from '../state/store'

const ToolIcon = ({ icon, className }: { icon: Icon; className?: string }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
    {icon.soft && <path d={icon.soft} fill="currentColor" fillOpacity={0.22} stroke="none" />}
    <path d={icon.d} />
    {icon.f && <path d={icon.f} fill="currentColor" stroke="none" />}
  </svg>
)

/**
 * Vertical drawing rail. Each group remembers the last tool used; clicking the group icon arms that
 * tool, the chevron/long-press opens the flyout with the whole group.
 */
export function DrawingToolbar({ horizontal = false }: { horizontal?: boolean }) {
  const tool = useStore((s) => s.tool)
  const setTool = useStore((s) => s.setTool)
  const magnet = useStore((s) => s.magnet)
  const setMagnet = useStore((s) => s.setMagnet)
  const symbol = useStore((s) => s.symbol)
  const [lastByGroup, setLastByGroup] = useState<Record<string, ToolEntry>>(() =>
    Object.fromEntries(TOOL_GROUPS.map((g) => [g, TOOLS.find((t) => t.group === g)!])))
  const [open, setOpen] = useState<string | null>(null)
  const [allLocked, setAllLocked] = useState(false)
  const [allHidden, setAllHidden] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const anchors = useRef(new Map<string, HTMLDivElement>())
  const [menuPos, setMenuPos] = useState({ left: 0, top: 0, maxHeight: 300, width: 220 })
  const hoverTimer = useRef<number | null>(null)
  const clearHover = () => { if (hoverTimer.current) { window.clearTimeout(hoverTimer.current); hoverTimer.current = null } }
  const canHover = useMemo(() => window.matchMedia('(hover: hover) and (pointer: fine)').matches, [])
  useEffect(() => () => clearHover(), [])

  useLayoutEffect(() => {
    if (!open) return
    const anchor = anchors.current.get(open)?.getBoundingClientRect()
    const menu = menuRef.current
    if (!anchor || !menu) return
    // Resolve env() through computed padding; custom properties themselves may still contain env().
    const bounds = menu.getBoundingClientRect()
    const shell = document.querySelector('.app-shell')
    const root = shell?.getBoundingClientRect()
    const css = shell ? getComputedStyle(shell) : null
    const left = (root?.left ?? 0) + (parseFloat(css?.paddingLeft ?? '') || 0) + 6
    const right = window.innerWidth - (parseFloat(css?.paddingRight ?? '') || 0) - 6
    const bottom = window.innerHeight - (parseFloat(css?.paddingBottom ?? '') || 0) - 6
    const top = (document.querySelector('header')?.getBoundingClientRect().bottom ?? 0) + 6
    const width = horizontal ? right - left : Math.min(240, right - left)
    const maxHeight = Math.max(44, (horizontal ? (ref.current?.getBoundingClientRect().top ?? bottom) - 6 : bottom) - top)
    setMenuPos({ width, maxHeight,
      left: horizontal ? left : Math.max(left, Math.min(anchor.right + 5, right - width)),
      top: Math.max(top, Math.min(horizontal ? (ref.current?.getBoundingClientRect().top ?? bottom) - bounds.height - 6 : anchor.top, bottom - Math.min(bounds.height, maxHeight))),
    })
  }, [open, horizontal, menuPos.width, menuPos.maxHeight])

  useEffect(() => {
    const close = () => { clearHover(); setOpen(null) }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('resize', close)
    document.addEventListener('keydown', key)
    return () => { window.removeEventListener('resize', close); document.removeEventListener('keydown', key) }
  }, [])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent | TouchEvent) => { if (!ref.current?.contains(e.target as Node) && !menuRef.current?.contains(e.target as Node)) setOpen(null) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('touchstart', onDoc)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('touchstart', onDoc) }
  }, [open])

  const arm = (t: ToolEntry) => {
    setLastByGroup((m) => ({ ...m, [t.group]: t }))
    setTool(tool === t.name ? null : t.name)
    setOpen(null)
  }

  const setAll = (patch: { lock?: boolean; visible?: boolean }) => {
    const chart = chartHolder.chart
    if (!chart || !symbol) return
    const items: Array<{ id: string; name: string; data: Record<string, unknown> }> = []
    for (const o of chart.getOverlays()) {
      if (o.name === 'measure') continue
      chart.overrideOverlay({ id: o.id, ...patch })
      items.push({ id: o.id, name: o.name, data: {
        points: o.points.map((p) => ({ timestamp: p.timestamp, value: p.value })),
        styles: o.styles ?? undefined, lock: patch.lock ?? o.lock, visible: patch.visible ?? o.visible,
        mode: o.mode, extendData: o.extendData ?? undefined, groupId: o.groupId,
      } })
    }
    // one request per 100 drawings instead of one per drawing
    if (items.length) api.putDrawings(symbol.id, items).catch((e: Error) => useStore.setState({ error: tr('Drawing not saved: {reason}', { reason: e.message }) }))
  }

  const removeAll = () => {
    const chart = chartHolder.chart
    if (!chart || !symbol) return
    if (!chart.getOverlays().length) return
    cancelPendingSave()  // a queued style save must not bring a drawing back
    if (!window.confirm(tr('Remove all drawings on {symbol}?', { symbol: symbol.display || symbol.symbol }))) return
    chartHolder.suppressRemove()
    chart.removeOverlay()
    chartHolder.restoreRemove()
    void api.deleteDrawings(symbol.id).catch(() => { /* ignore */ })
    useStore.getState().setSelectedOverlay(null)
  }

  const groupBtn = (g: ToolEntry['group']) => {
    const t = lastByGroup[g]
    const active = TOOLS.some((x) => x.group === g && x.name === tool)
    const toggle = () => setOpen(open === g ? null : g)
    return (
      <div
        key={g} className="relative shrink-0" ref={(el) => { if (el) anchors.current.set(g, el); else anchors.current.delete(g) }}
        onMouseEnter={canHover ? () => { clearHover(); hoverTimer.current = window.setTimeout(() => setOpen(g), 380) } : undefined}
        onMouseLeave={canHover ? () => { clearHover(); hoverTimer.current = window.setTimeout(() => setOpen((o) => (o === g ? null : o)), 220) } : undefined}
      >
        <button
          className="rail-btn" data-active={active} title={canHover ? `${tr(t.label)}${t.hotkey ? ` (${t.hotkey})` : ''}` : tr(g)}
          aria-haspopup="menu" aria-expanded={open === g}
          onClick={() => { clearHover(); if (canHover) arm(t); else toggle() }}
          onContextMenu={(e) => { e.preventDefault(); toggle() }}
        >
          <ToolIcon icon={t.icon} />
        </button>
        {/* the right third of the button opens the group (easier to hit than the corner triangle) */}
        {canHover && (
          <button
            className={`absolute inset-y-0 right-0 flex w-3 items-end justify-end rounded-r-lg pb-0.5 pr-0.5 text-faint hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-text`}
            aria-label={tr('More {group} tools', { group: tr(g).toLowerCase() })} onClick={() => { clearHover(); toggle() }}
          >
            <svg viewBox="0 0 8 8" className="h-2 w-2" fill="currentColor"><path d="M8 8H2L8 2z" /></svg>
          </button>
        )}
        {!canHover && (
          <span className="pointer-events-none absolute bottom-0.5 right-0.5 text-faint">
            <svg viewBox="0 0 8 8" className="h-2 w-2" fill="currentColor"><path d="M8 8H2L8 2z" /></svg>
          </span>
        )}
        {open === g && createPortal(
          <div
            ref={menuRef} role="menu"
            className={`fixed z-40 overflow-y-auto overscroll-contain rounded-lg border border-line bg-panel p-1 shadow-xl ${horizontal ? 'grid grid-cols-2 gap-0.5' : ''}`}
            style={menuPos}
            onMouseEnter={canHover ? clearHover : undefined}
            onMouseLeave={canHover ? () => { clearHover(); hoverTimer.current = window.setTimeout(() => setOpen(null), 220) } : undefined}
          >
            {/* invisible bridge so the pointer can travel from the button to the menu without closing it */}
            {!horizontal && <span className="absolute -left-2 top-0 h-full w-2" />}
            <div className={`px-2 py-1 text-[11px] text-faint ${horizontal ? 'col-span-2' : ''}`}>{tr(g)}</div>
            {TOOLS.filter((x) => x.group === g).map((x) => (
 <button key={x.name} role="menuitem" className={`tb-btn w-full justify-start gap-3 text-[13px] ${horizontal ? '!h-11' : '!h-9'}`} data-active={tool === x.name} onClick={() => { clearHover(); arm(x) }}>
                <ToolIcon icon={x.icon} className="h-[18px] w-[18px]" />
                <span className="flex-1 text-left">{tr(x.label)}</span>
                {x.hotkey && !horizontal && <span className="kbd">{x.hotkey}</span>}
              </button>
            ))}
          </div>, document.body
        )}
      </div>
    )
  }

  const cycleMagnet = () => setMagnet(magnet === 'normal' ? 'weak_magnet' : magnet === 'weak_magnet' ? 'strong_magnet' : 'normal')

  return (
    <div ref={ref} onScroll={() => { clearHover(); setOpen(null) }} className={`drawing-rail flex [&>*]:shrink-0 ${horizontal ? 'flex-row items-center gap-1 overflow-x-auto px-2' : 'h-full min-h-0 flex-col items-center gap-1 overflow-y-auto overscroll-contain py-2'}`}>
      {TOOL_GROUPS.map(groupBtn)}
      <button className="rail-btn" data-active={tool === 'measure'} onClick={() => setTool(tool === 'measure' ? null : 'measure')} title={tr('Ruler — click start, click end')}>
        <ToolIcon icon={MEASURE_ICON} />
      </button>
      <div className={horizontal ? 'mx-1 h-6 w-px bg-line' : 'my-1 h-px w-6 bg-line'} />
      <button className="rail-btn" data-active={magnet !== 'normal'} onClick={cycleMagnet}
        title={tr(magnet === 'normal' ? 'Magnet off' : magnet === 'weak_magnet' ? 'Weak magnet (snaps near OHLC)' : 'Strong magnet (always snaps to OHLC)')}>
        <Magnet strokeWidth={magnet === 'strong_magnet' ? 2.8 : 2} />
      </button>
      <button className="rail-btn" data-active={allLocked} onClick={() => { setAll({ lock: !allLocked }); setAllLocked(!allLocked) }} title={tr(allLocked ? 'Unlock all drawings' : 'Lock all drawings')}>
        {allLocked ? <Lock /> : <LockOpen />}
      </button>
      <button className="rail-btn" data-active={allHidden} onClick={() => { setAll({ visible: allHidden }); setAllHidden(!allHidden) }} title={tr(allHidden ? 'Show drawings' : 'Hide drawings')}>
        {allHidden ? <EyeOff /> : <Eye />}
      </button>
      <button className="rail-btn" onClick={() => chartHolder.undo()} title={tr('Undo (⌘Z)')}><Undo2 /></button>
      <button className="rail-btn" onClick={removeAll} title={tr('Remove all drawings on this symbol')}><Trash2 /></button>
    </div>
  )
}
