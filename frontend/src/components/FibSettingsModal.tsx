import { Plus, Trash2 } from 'lucide-react'
import { t } from '../i18n'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Overlay } from 'klinecharts'
import { chartHolder, saveOverlay } from '../chart/ChartView'
import { replaceOverlayExtend } from '../chart/extend'
import { STANDARD_FIB, cloneFib, normalizeFib, type FibConfig, type FibLevel } from '../chart/overlays/fib'
import { useStore } from '../state/store'
import { Modal, Toggle } from './ui'

const STYLES: Array<{ v: FibLevel['style']; label: string; dash: string }> = [
  { v: 'solid', label: 'Solid', dash: '' },
  { v: 'dashed', label: 'Dashed', dash: '5 3' },
  { v: 'dotted', label: 'Dotted', dash: '1.5 2.5' },
]

/** Full settings for a Fib retracement drawing. Edits apply live; Cancel restores. */
export function FibSettingsModal({ overlayId, onClose }: { overlayId: string; onClose: () => void }) {
  const symbol = useStore((s) => s.symbol)
  const setSettings = useStore((s) => s.setSettings)
  const chart = chartHolder.chart
  const overlay = chart?.getOverlays({ id: overlayId })[0] as Overlay | undefined
  const original = useRef<FibConfig>(normalizeFib(overlay?.extendData))
  const [cfg, setCfg] = useState<FibConfig>(() => cloneFib(original.current))
  const [tab, setTab] = useState<'levels' | 'style'>('levels')
  const [note, setNote] = useState<string | null>(null)
  const [newValue, setNewValue] = useState('')
  const wide = typeof window !== 'undefined' && window.innerWidth >= 640

  useEffect(() => { if (chart) replaceOverlayExtend(chart, overlayId, cfg) }, [cfg, chart, overlayId])

  const sorted = useMemo(() => cfg.levels.map((l, i) => ({ l, i })).sort((a, b) => a.l.value - b.l.value), [cfg.levels])
  const patch = (p: Partial<FibConfig>) => setCfg((c) => ({ ...c, ...p }))
  const patchLevel = (i: number, p: Partial<FibLevel>) => setCfg((c) => ({ ...c, levels: c.levels.map((l, k) => (k === i ? { ...l, ...p } : l)) }))
  const removeLevel = (i: number) => setCfg((c) => ({ ...c, levels: c.levels.filter((_, k) => k !== i) }))
  const addLevel = () => {
    const v = Number(newValue.replace(',', '.'))
    if (!Number.isFinite(v)) return
    setCfg((c) => ({ ...c, levels: [...c.levels, { value: v, enabled: true, color: '#ffab2e', style: 'solid', width: 1 }] }))
    setNewValue('')
  }

  const finish = (keep: boolean) => {
    if (!chart || !overlay || !symbol) { onClose(); return }
    if (!keep) replaceOverlayExtend(chart, overlayId, original.current)
    const o = chart.getOverlays({ id: overlayId })[0]
    if (keep && o) void saveOverlay(symbol.id, o)
    onClose()
  }

  if (!overlay) return null

  return (
    <Modal
      title={t('Fib retracement')} onClose={() => finish(false)} width={600}
      footer={
        <div className="flex flex-wrap items-center gap-2">
          <button className="tb-btn !h-8 text-[12px]" onClick={() => { setSettings({ fibDefaults: cloneFib(cfg) }); setNote(t('Saved as the default for new Fib retracements')) }}>{t('Save as default')}</button>
          <button className="tb-btn !h-8 text-[12px]" onClick={() => { setCfg(cloneFib(STANDARD_FIB)); setNote(t('Standard levels restored')) }}>{t('Reset to standard levels')}</button>
          {note && <span className="text-[11px] text-faint">{note}</span>}
          <div className="ml-auto flex gap-2">
            <button className="tb-btn !h-8 !px-3" onClick={() => finish(false)}>{t('Cancel')}</button>
            <button className="h-8 rounded-md px-4 text-[13px] font-medium" style={{ background: 'var(--accent)', color: 'var(--bg)' }} onClick={() => finish(true)}>{t('Ok')}</button>
          </div>
        </div>
      }
    >
      <div className="sticky top-0 z-10 flex gap-1.5 border-b border-line bg-panel px-4 py-2.5">
        <button className="chip" data-active={tab === 'levels'} onClick={() => setTab('levels')}>{t('Levels')}</button>
        <button className="chip" data-active={tab === 'style'} onClick={() => setTab('style')}>{t('Style')}</button>
      </div>

      {tab === 'levels' ? (
        <div className="px-3 py-2">
          <div className="grid grid-cols-1 gap-x-4 sm:grid-flow-col sm:grid-cols-2" style={{ gridTemplateRows: wide ? `repeat(${Math.ceil(sorted.length / 2)}, auto)` : undefined }}>
            {sorted.map(({ l, i }) => (
              <LevelRow key={i} level={l} onChange={(p) => patchLevel(i, p)} onRemove={() => removeLevel(i)} />
            ))}
          </div>
          <div className="mt-2 flex items-center gap-2 px-1 pb-1">
            <div className="field h-8 w-28 px-2">
              <input value={newValue} onChange={(e) => setNewValue(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') addLevel() }}
                inputMode="decimal" placeholder={t('e.g. 0.65')} className="h-full w-full text-[13px] placeholder:text-faint" />
            </div>
            <button className="tb-btn !h-8 text-[12px]" onClick={addLevel} disabled={!newValue.trim()}><Plus />{t('Add level')}</button>
            <span className="ml-auto text-[11px] text-faint">{t('{n} of {total} shown', { n: cfg.levels.filter((l) => l.enabled).length, total: cfg.levels.length })}</span>
          </div>
        </div>
      ) : (
        <div className="grid gap-x-8 px-4 py-2 sm:grid-cols-2">
          <div>
            <div className="pb-1 pt-2 text-[12px] text-faint">{t('Lines')}</div>
            <Toggle label={t('Trend line')} checked={cfg.trendLine} onChange={(v) => patch({ trendLine: v })} />
            <Toggle label={t('Extend lines left')} checked={cfg.extendLeft} onChange={(v) => patch({ extendLeft: v })} />
            <Toggle label={t('Extend lines right')} checked={cfg.extendRight} onChange={(v) => patch({ extendRight: v })} />
            <Toggle label={t('Reverse')} checked={cfg.reverse} onChange={(v) => patch({ reverse: v })} />
            <Toggle label={t('Background')} checked={cfg.background} onChange={(v) => patch({ background: v })} />
            {cfg.background && (
              <label className="flex items-center gap-3 py-2 text-[12px] text-muted">
                {t('Opacity')}
                <input type="range" min={0.03} max={0.4} step={0.01} value={cfg.bgOpacity} onChange={(e) => patch({ bgOpacity: Number(e.target.value) })} className="flex-1 accent-[var(--accent)]" />
                <span className="w-8 text-right">{Math.round(cfg.bgOpacity * 100)}%</span>
              </label>
            )}
          </div>
          <div>
            <div className="pb-1 pt-2 text-[12px] text-faint">{t('Labels')}</div>
            <Toggle label={t('Level values')} checked={cfg.showValues} onChange={(v) => patch({ showValues: v })} />
            <Toggle label={t('Prices')} checked={cfg.showPrices} onChange={(v) => patch({ showPrices: v })} />
            <Toggle label={t('Levels as percent')} checked={cfg.asPercent} onChange={(v) => patch({ asPercent: v })} />
            <div className="flex items-center justify-between py-2">
              <span>{t('Position')}</span>
              <div className="flex gap-1">
                <button className="chip !h-7" data-active={cfg.labelsLeft} onClick={() => patch({ labelsLeft: true })}>{t('Left')}</button>
                <button className="chip !h-7" data-active={!cfg.labelsLeft} onClick={() => patch({ labelsLeft: false })}>{t('Right')}</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </Modal>
  )
}

function LevelRow({ level, onChange, onRemove }: { level: FibLevel; onChange: (p: Partial<FibLevel>) => void; onRemove: () => void }) {
  const [text, setText] = useState(String(level.value))
  useEffect(() => { setText(String(level.value)) }, [level.value])
  const commit = () => {
    const v = Number(text.replace(',', '.'))
    if (Number.isFinite(v) && v !== level.value) onChange({ value: v })
    else setText(String(level.value))
  }
  const style = STYLES.find((s) => s.v === level.style) ?? STYLES[0]
  const nextStyle = STYLES[(STYLES.indexOf(style) + 1) % STYLES.length]
  return (
    <div className="group flex h-10 items-center gap-2 rounded-md px-1 hover:bg-raised" style={{ opacity: level.enabled ? 1 : 0.55 }}>
      <input type="checkbox" checked={level.enabled} onChange={(e) => onChange({ enabled: e.target.checked })}
        className="h-4 w-4 shrink-0 cursor-pointer" style={{ accentColor: level.color }} aria-label={t('Show level {v}', { v: level.value })} />
      <div className="field h-7 w-[72px] shrink-0 px-2">
        <input value={text} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
          inputMode="decimal" className="h-full w-full text-[13px]" aria-label={t('Level value')} />
      </div>
      <label className="relative h-6 w-6 shrink-0 cursor-pointer rounded border border-line" style={{ background: level.color }} title={t('Colour')}>
        <input type="color" value={level.color} onChange={(e) => onChange({ color: e.target.value })} className="absolute inset-0 cursor-pointer opacity-0" aria-label={t('Level colour')} />
      </label>
      <button className="tb-btn !h-7 !min-w-0 !px-1.5" onClick={() => onChange({ style: nextStyle.v })} title={t('{style} — click for {next}', { style: t(style.label), next: t(nextStyle.label).toLowerCase() })}>
        <svg width="26" height="8" viewBox="0 0 26 8"><line x1="1" y1="4" x2="25" y2="4" stroke={level.color} strokeWidth={level.width} strokeDasharray={style.dash} strokeLinecap="round" /></svg>
      </button>
      <button className="tb-btn !h-7 !min-w-7 !px-0 text-[12px]" onClick={() => onChange({ width: level.width >= 4 ? 1 : level.width + 1 })} title={t('Line width — click to change')}>{level.width}px</button>
      <span className="flex-1" />
      <button className="tb-btn !h-7 !min-w-7 !px-0 opacity-60 hover:opacity-100" onClick={onRemove} aria-label={t('Delete level {v}', { v: level.value })}><Trash2 /></button>
    </div>
  )
}
