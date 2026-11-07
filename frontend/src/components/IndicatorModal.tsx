import { Search, X } from 'lucide-react'
import { t } from '../i18n'
import { useMemo, useState } from 'react'
import { INDICATOR_CATALOG, offered, unavailable, type IndicatorMeta } from '../chart/indicators/catalog'
import { padParams } from '../chart/indicators/levels'
import { useStore } from '../state/store'
import { Modal } from './ui'
import { pluginIndicator } from '../plugins'
import type { IndicatorState } from '../lib/types'

const GROUPS: IndicatorMeta['group'][] = ['Trend', 'Momentum', 'Volatility', 'Volume', 'Levels', 'Crypto', 'Sentiment']

export function IndicatorModal({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState('')
  const indicators = useStore((s) => s.indicators)
  const addIndicator = useStore((s) => s.addIndicator)
  const removeIndicator = useStore((s) => s.removeIndicator)
  const updateIndicator = useStore((s) => s.updateIndicator)

  const symbol = useStore((s) => s.symbol)
  const interval = useStore((s) => s.interval)
  const filtered = useMemo(() => {
    const qq = q.trim().toLowerCase()
    return INDICATOR_CATALOG.filter((m) => offered(m) && (!qq || m.name.toLowerCase().includes(qq) || m.label.toLowerCase().includes(qq) || t(m.label).toLowerCase().includes(qq)))
  }, [q])

  return (
    <Modal title={t('Indicators')} onClose={onClose} width={720}>
      <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_280px]">
        <div>
          <div className="sticky top-0 z-10 border-b border-line bg-panel px-4 py-3">
            <div className="field h-10 px-3">
              <Search className="h-4 w-4 text-muted" />
              <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('Search indicators')} className="h-full flex-1 placeholder:text-faint" />
            </div>
          </div>
          {GROUPS.map((g) => {
            const list = filtered.filter((m) => m.group === g)
            if (!list.length) return null
            return (
              <div key={g} className="px-2 py-2">
                <div className="px-2 pb-1 text-[11px] text-faint">{t(g)}</div>
                {list.map((m) => {
                  // the catalogue says where each indicator works; elsewhere it stays listed, disabled, with the reason
                  const why = unavailable(m, symbol, interval)
                  return (
                    <button key={m.name} className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-raised disabled:cursor-default disabled:hover:bg-transparent" disabled={!!why}
                      onClick={() => addIndicator(m.name, m.main, m.params, m.options ? { ...m.options } : undefined)}>
                      <span className={`w-24 shrink-0 font-medium ${why ? 'text-faint' : ''}`}>{m.name}</span>
                      <span className="min-w-0 flex-1">
                        <span className={`block truncate ${why ? 'text-faint' : 'text-muted'}`}>{t(m.label)}</span>
                        {why && <span className="block text-[11px] leading-4 text-muted">{why}</span>}
                      </span>
                      {!why && <span className="text-[11px] text-faint">{t(m.main ? 'on chart' : 'pane')}</span>}
                    </button>
                  )
                })}
              </div>
            )
          })}
        </div>
        <div className="border-t border-line sm:border-l sm:border-t-0">
          <div className="px-4 py-3 text-[12px] text-faint">{t('On this chart')}</div>
          {!indicators.length && <div className="px-4 pb-4 text-[12px] text-muted">{t('No indicators yet. Pick one on the left.')}</div>}
          <ul>
            {indicators.map((ind) => (
              <li key={ind.key} className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="flex-1 font-medium">{ind.name}</span>
                  <button className="tb-btn !h-7 !min-w-7" onClick={() => removeIndicator(ind.key)} aria-label={t('Remove {name}', { name: ind.name })}><X /></button>
                </div>
                <PluginSettings ind={ind} />
                {(() => {
                  const meta = INDICATOR_CATALOG.find((m) => m.name === ind.name)
                  const params = padParams(ind.calcParams, meta?.params)   // band parameters added later show their defaults
                  if (!params?.length) return null
                  return (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {params.map((p, i) => (
                        <label key={i} className="flex flex-col gap-0.5">
                          {meta?.paramNames?.[i] && <span className="text-[10px] leading-3 text-faint">{t(meta.paramNames[i])}</span>}
                          <input
                            type="number" inputMode="decimal" defaultValue={p}
                            className="h-7 w-16 rounded border border-line bg-bg px-2 text-[12px] outline-none focus:border-muted"
                            onBlur={(e) => {
                              const v = Math.max(-1000, Math.min(1000, Number(e.target.value)))
                              if (!Number.isFinite(v) || v === p) return
                              const next = [...params]
                              next[i] = v
                              updateIndicator(ind.key, { calcParams: next })
                            }}
                          />
                        </label>
                      ))}
                    </div>
                  )
                })()}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Modal>
  )
}

/** Extra settings a plugin indicator brings (nothing for the built-ins). */
function PluginSettings({ ind }: { ind: IndicatorState }) {
  const Settings = pluginIndicator(ind.name)?.Settings
  return Settings ? <Settings ind={ind} /> : null
}
