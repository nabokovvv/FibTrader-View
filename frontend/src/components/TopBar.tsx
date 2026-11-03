import { Bell, ChartArea, ChartCandlestick, ChartLine, ChartNoAxesColumn, ChevronDown, Ellipsis, GitCompareArrows, LayoutGrid, Moon, PanelRight, Search, Settings, Share2, SlidersHorizontal, Sun } from 'lucide-react'
import { alertMarkets, useAlerts } from '../state/alerts'
import { serverFeatures } from '../auth/session'
import { t } from '../i18n'
import { Logo } from './Logo'
import { AccountButton } from '../auth/AccountButton'
import { Fragment, useEffect, useRef, useState } from 'react'
import { INTERVAL_LABEL } from '../chart/periods'
import { SHOWN_INTERVALS, tickerOf, type ChartType, type Interval } from '../lib/types'
import { useStore } from '../state/store'
import { fmtPrice, MarketBadge, symName } from './ui'

const CHART_TYPES: Array<{ type: ChartType; label: string; Icon: typeof ChartCandlestick }> = [
  { type: 'candle_solid', label: 'Candles', Icon: ChartCandlestick },
  { type: 'candle_stroke', label: 'Hollow candles', Icon: ChartCandlestick },
  { type: 'ohlc', label: 'Bars', Icon: ChartNoAxesColumn },
  { type: 'area', label: 'Area', Icon: ChartArea },
]

export function TopBar({ compact }: { compact: boolean }) {
  const symbol = useStore((s) => s.symbol)
  const interval = useStore((s) => s.interval)
  const setInterval = useStore((s) => s.setInterval)
  const chartType = useStore((s) => s.chartType)
  const setChartType = useStore((s) => s.setChartType)
  const theme = useStore((s) => s.theme)
  const setTheme = useStore((s) => s.setTheme)
  const setSheet = useStore((s) => s.setSheet)
  const showWatchlist = useStore((s) => s.showWatchlist)
  const toggleWatchlist = useStore((s) => s.toggleWatchlist)
  const wsStatus = useStore((s) => s.wsStatus)
  const lastBar = useStore((s) => s.lastBar)
  const quote = useStore((s) => (s.symbol ? s.quotes[s.symbol.id] : undefined))
  const layoutNames = useStore((s) => s.layoutNames)
  const saveLayoutAs = useStore((s) => s.saveLayoutAs)
  const loadLayout = useStore((s) => s.loadLayout)
  const deleteLayout = useStore((s) => s.deleteLayout)

  const armed = useAlerts((s) => s.alerts.filter((a) => a.active).length)
  const price = quote?.p ?? lastBar?.close
  const dirUp = lastBar ? lastBar.close >= lastBar.open : true
  const ChartIcon = CHART_TYPES.find((c) => c.type === chartType)?.Icon ?? ChartLine

  return (
    // height includes the status-bar inset (when the app is drawn under it), so the 44 px row never shrinks
    <header className="flex shrink-0 items-center gap-1 border-b border-line bg-panel px-2" style={{ height: 'calc(44px + var(--safe-top))', paddingTop: 'var(--safe-top)' }}>
      <a className="mr-1 hidden h-8 w-8 items-center justify-center rounded-md sm:flex" href="/" aria-label="FibTrader View" title="FibTrader View">
        <Logo className="h-[22px] w-[22px] text-text" />
      </a>

      <button className="tb-btn !h-8 max-w-[46vw] gap-2 !px-2 sm:max-w-none" onClick={() => setSheet('search')} title={t('Search symbol (⌘K)')}>
        {!compact && <Search className="!h-4 !w-4 shrink-0 text-muted" />}
        <span className="min-w-[3rem] truncate text-[14px] font-semibold text-text">{symbol ? tickerOf(symbol) : '—'}</span>
        {symbol && !compact && <MarketBadge market={symbol.market} currency={symbol.currency} />}
        {symbol && !compact && <span className="hidden max-w-[220px] truncate text-muted lg:inline">{symName(symbol.name)}</span>}
      </button>

      {price !== undefined && (
        <span className={`ml-1 text-[14px] font-semibold ${dirUp ? 'num-up' : 'num-down'}`}>{fmtPrice(price, symbol?.price_precision)}</span>
      )}
      {quote?.ch !== undefined && quote?.ch !== null && !compact && (
        <span className={`text-[12px] ${quote.ch >= 0 ? 'num-up' : 'num-down'}`}>{quote.ch >= 0 ? '+' : ''}{quote.ch.toFixed(2)}%</span>
      )}

      {!compact && <div className="mx-1 h-5 w-px bg-line" />}

      {compact ? (
        <IntervalMenu interval={interval} onPick={setInterval} />
      ) : (
        <div className="flex items-center">
          {SHOWN_INTERVALS.map((iv) => (
            <button key={iv} className="tb-btn !min-w-8 !px-1.5 text-[12px]" data-active={iv === interval} onClick={() => setInterval(iv)}>{t(INTERVAL_LABEL[iv])}</button>
          ))}
        </div>
      )}

      {!compact && <div className="mx-1 h-5 w-px bg-line" />}

      {/* phone: chart type, share, theme and settings live in the "⋯" menu; indicators are in the bottom bar */}
      {!compact && (
        <Menu
          button={<span className="tb-btn" title={t('Chart type')}><ChartIcon /><ChevronDown className="!h-3 !w-3" /></span>}
          items={CHART_TYPES.map((c) => ({ key: c.type, label: t(c.label), active: c.type === chartType, onClick: () => setChartType(c.type), Icon: c.Icon }))}
        />
      )}

      {!compact && (
        <button className="tb-btn" onClick={() => setSheet('indicators')} title={t('Indicators')}>
          <SlidersHorizontal /><span className="hidden text-[12px] xl:inline">{t('Indicators')}</span>
        </button>
      )}
      {!compact && (
        <button className="tb-btn" onClick={() => setSheet('compare')} title={t('Compare with another symbol')}>
          <GitCompareArrows />
        </button>
      )}
      {alertMarkets().length > 0 && (
        <button className="tb-btn relative" onClick={() => { useAlerts.getState().setDraft(null); setSheet('alerts') }} title={t('Alerts')}>
          <Bell />{armed > 0 && <span className="absolute right-0.5 top-0.5 min-w-3.5 rounded-full px-0.5 text-center text-[9px] font-semibold leading-3.5" style={{ background: 'var(--brand)', color: '#fff' }}>{armed}</span>}
        </button>
      )}
      {!compact && serverFeatures().shares && (
        <button className="tb-btn" onClick={() => setSheet('share')} title={t('Share chart')}><Share2 /></button>
      )}

      {!compact && (
        <Menu
          button={<span className="tb-btn" title={t('Layouts')}><LayoutGrid /><ChevronDown className="!h-3 !w-3" /></span>}
          items={[
            ...layoutNames.map((n) => ({ key: n, label: n, onClick: () => void loadLayout(n) })),
            { key: '__save', label: t('Save layout as…'), onClick: () => { const n = window.prompt(t('Layout name')); if (n && n !== 'last') void saveLayoutAs(n) } },
            ...(layoutNames.length ? [{ key: '__del', label: t('Delete a layout…'), onClick: () => { const n = window.prompt(t('Delete which layout?\n{names}', { names: layoutNames.join(', ') })); if (n && layoutNames.includes(n)) void deleteLayout(n) } }] : []),
          ]}
        />
      )}

      <div className="ml-auto flex items-center gap-0.5">
        <span className="mr-1 flex items-center gap-1 text-[11px] text-faint" title={t('Live data: {status}', { status: t(wsStatus === 'open' ? 'live' : wsStatus) })}>
          <span className={`inline-block h-2 w-2 rounded-full ${wsStatus === 'open' ? 'pulse' : ''}`} style={{ background: wsStatus === 'open' ? 'var(--up)' : wsStatus === 'connecting' ? 'var(--warn)' : 'var(--down)' }} />
          {!compact && t(wsStatus === 'open' ? 'live' : wsStatus)}
        </span>
        {!compact && (
          <button className="tb-btn" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} title={t('Toggle theme')}>{theme === 'dark' ? <Sun /> : <Moon />}</button>
        )}
        {!compact && (
          <button className="tb-btn" data-active={showWatchlist} onClick={toggleWatchlist} title={t('Watchlist')}><PanelRight /></button>
        )}
        {!compact && <button className="tb-btn" onClick={() => setSheet('settings')} title={t('Settings')}><Settings /></button>}
        {compact && (
          <Menu
            align="right"
            button={<span className="tb-btn" title={t('More')} aria-label={t('More')}><Ellipsis /></span>}
            items={[
              ...CHART_TYPES.map((c) => ({ key: c.type, label: t(c.label), active: c.type === chartType, onClick: () => setChartType(c.type), Icon: c.Icon })),
              ...(serverFeatures().shares ? [{ key: '__share', label: t('Share chart'), onClick: () => setSheet('share'), Icon: Share2, sep: true }] : []),
              { key: '__theme', label: t(theme === 'dark' ? 'Light theme' : 'Dark theme'), onClick: () => setTheme(theme === 'dark' ? 'light' : 'dark'), Icon: theme === 'dark' ? Sun : Moon, sep: !serverFeatures().shares },
              { key: '__settings', label: t('Settings'), onClick: () => setSheet('settings'), Icon: Settings },
            ]}
          />
        )}
        <AccountButton />
      </div>
    </header>
  )
}

function IntervalMenu({ interval, onPick }: { interval: Interval; onPick: (iv: Interval) => void }) {
  return (
    <Menu
      button={<span className="tb-btn !px-2 text-[13px] font-semibold text-text">{t(INTERVAL_LABEL[interval])}<ChevronDown className="!h-3 !w-3" /></span>}
      items={SHOWN_INTERVALS.map((iv) => ({ key: iv, label: t(iv), active: iv === interval, onClick: () => onPick(iv) }))}
      grid
    />
  )
}

export function Menu({ button, items, grid, align = 'left' }: {
  button: React.ReactNode
  items: Array<{ key: string; label: string; active?: boolean; onClick: () => void; Icon?: typeof ChartCandlestick; sep?: boolean }>
  grid?: boolean
  align?: 'left' | 'right'   // right: the menu opens leftwards (buttons at the right edge of the screen)
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])
  return (
    <div ref={ref} className="relative">
      <div onClick={() => setOpen((o) => !o)}>{button}</div>
      {open && (
        <div className={`absolute ${align === 'right' ? 'right-0' : 'left-0'} top-full z-40 mt-1 rounded-lg border border-line bg-panel p-1 shadow-xl ${grid ? 'grid w-[196px] grid-cols-4 gap-0.5' : 'min-w-[180px]'}`}>
          {items.map((it) => (
            <Fragment key={it.key}>
              {it.sep && <div className="mx-1 my-1 h-px bg-line" />}
              <button
                className={`tb-btn w-full ${grid ? 'justify-center' : 'justify-start'} !h-9 text-[13px]`}
                data-active={it.active}
                onClick={() => { it.onClick(); setOpen(false) }}
              >
                {it.Icon && <it.Icon />}{it.label}
              </button>
            </Fragment>
          ))}
        </div>
      )}
    </div>
  )
}
