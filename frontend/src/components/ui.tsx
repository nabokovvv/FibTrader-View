import { X } from 'lucide-react'
import { getLang } from '../i18n'
import { t } from '../i18n'
import { useEffect, type ReactNode } from 'react'

/** Centered dialog on desktop, full-height sheet on phones. Closes on Esc / backdrop. */
export function Modal({ title, onClose, children, width = 560, footer }: {
  title: ReactNode; onClose: () => void; children: ReactNode; width?: number; footer?: ReactNode
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/55 sm:items-center" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div
        role="dialog" aria-modal="true"
        className="slide-up flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl sm:max-h-[80vh] sm:rounded-xl"
        style={{ maxWidth: width, paddingBottom: 'var(--safe-bottom)' }}
      >
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
          <div className="text-[14px] font-medium">{title}</div>
          <button className="tb-btn" onClick={onClose} aria-label={t('Close')}><X /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        {footer && <div className="shrink-0 border-t border-line px-4 py-3">{footer}</div>}
      </div>
    </div>
  )
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex cursor-pointer items-center justify-between py-2">
      <span>{label}</span>
      <button
        role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className="relative h-5 w-9 rounded-full transition-colors"
        style={{ background: checked ? 'var(--accent)' : 'var(--raised)' }}
      >
        <span className="absolute top-0.5 h-4 w-4 rounded-full transition-all" style={{ left: checked ? 18 : 2, background: checked ? 'var(--bg)' : 'var(--muted)' }} />
      </button>
    </label>
  )
}

/**
 * Market tag next to a ticker. `highlight` (symbol search) makes the main instrument stand out: Bybit USDT perpetuals.
 * Everything else, USDC perpetuals included, keeps the quiet style.
 */
export function MarketBadge({ market, currency, highlight }: { market: string; currency?: string; highlight?: boolean }) {
  const label: Record<string, string> = { bybit: 'PERP', 'bybit-spot': 'SPOT', us: 'US', moex: 'MOEX', yahoo: 'GLB', hl: 'HL', bq: 'CFD' }
  const quiet = 'shrink-0 rounded border border-line px-1.5 py-px text-[10px] font-medium tracking-wide text-muted'
  if (market === 'hl') {
    return <span className={quiet} title={t('Perpetual on Hyperliquid: trades around the clock, so its price can differ from the exchange price of the underlying.')}>HL</span>
  }
  if (market === 'bq') {
    return <span className={quiet} title={t('CFD price of a broker, data by biquote.io: it follows the market during its trading hours.')}>CFD</span>
  }
  if (market === 'bybit' && currency === 'USDC') {
    return <span className={quiet} title={t('USDC-margined perpetual')}>USDC</span>
  }
  if (highlight && market === 'bybit') {
    return (
      <span className="shrink-0 rounded px-1.5 py-px text-[10px] font-semibold tracking-wide" title={t('USDT perpetual')}
        style={{ color: 'var(--brand)', background: 'color-mix(in srgb, var(--brand) 14%, transparent)', boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--brand) 55%, transparent)' }}>
        PERP
      </span>
    )
  }
  return <span className={quiet}>{label[market] ?? market.toUpperCase()}</span>
}

/** Instrument name in the UI language ("BTC / USDT Perpetual" → "BTC / USDT бессрочный"). */
export function symName(name: string | undefined): string {
  if (!name || getLang() !== 'ru') return name ?? ''
  return name.replace(/ Perpetual$/, ' бессрочный').replace(/ Perpetual \(delisted\)$/, ' бессрочный (снят с торгов)').replace(/ Spot$/, ' спот')
}

export function fmtPrice(p: number | undefined | null, precision = 2): string {
  if (p === undefined || p === null || Number.isNaN(p)) return '—'
  return p.toLocaleString('en-US', { minimumFractionDigits: precision, maximumFractionDigits: precision })
}

export function fmtPct(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) return ''
  return `${p > 0 ? '+' : ''}${p.toFixed(2)}%`
}
