import { useEffect, useMemo, useRef, useState } from 'react'
import { t as tr } from '../i18n'
import type { Overlay, Point } from 'klinecharts'
import { chartHolder, saveOverlay } from '../chart/ChartView'
import { replaceOverlayExtend } from '../chart/extend'
import { normalizePosition, type PositionConfig } from '../chart/overlays/position'
import { useStore } from '../state/store'
import { Modal } from './ui'

type Pts = Array<Partial<Point>>

/** Long/Short position settings: account & risk (→ qty) and exact entry / target / stop prices. */
export function PositionSettingsModal({ overlayId, onClose }: { overlayId: string; onClose: () => void }) {
  const symbol = useStore((s) => s.symbol)
  const setSettings = useStore((s) => s.setSettings)
  const chart = chartHolder.chart
  const overlay = chart?.getOverlays({ id: overlayId })[0] as Overlay | undefined
  const orig = useRef({
    cfg: normalizePosition(overlay?.extendData),
    points: (overlay?.points ?? []).map((p) => ({ timestamp: p.timestamp, value: p.value })) as Pts,
  })
  const [raw, setRaw] = useState({ acct: String(orig.current.cfg.accountSize), risk: String(orig.current.cfg.riskPct), qp: String(orig.current.cfg.qtyPrecision) })
  const cfg: PositionConfig = useMemo(() => normalizePosition({ accountSize: Number(raw.acct.replace(',', '.')), riskPct: Number(raw.risk.replace(',', '.')), qtyPrecision: Number(raw.qp) }), [raw])
  const prec = symbol?.price_precision ?? 2
  const p = orig.current.points
  const [prices, setPrices] = useState({
    entry: String(p[0]?.value?.toFixed(prec) ?? ''), target: String(p[1]?.value?.toFixed(prec) ?? ''), stop: String(p[2]?.value?.toFixed(prec) ?? ''),
  })
  const [note, setNote] = useState<string | null>(null)
  const long = overlay?.name === 'longPosition'

  const num = (v: string) => Number(v.replace(',', '.'))
  const e = num(prices.entry), t = num(prices.target), s = num(prices.stop)
  const valid = [e, t, s].every((x) => Number.isFinite(x) && x > 0) && (long ? t > e && s < e : t < e && s > e)

  useEffect(() => {
    if (!chart) return
    const base = orig.current.points
    const pts = valid && base.length >= 4
      ? [{ ...base[0], value: e }, { ...base[0], value: t }, { ...base[0], value: s }, { ...base[3], value: e }]
      : base
    replaceOverlayExtend(chart, overlayId, cfg)
    chart.overrideOverlay({ id: overlayId, points: pts })
  }, [cfg, e, t, s, valid, chart, overlayId])

  const finish = (keep: boolean) => {
    if (!chart || !symbol) { onClose(); return }
    if (!keep) { replaceOverlayExtend(chart, overlayId, orig.current.cfg); chart.overrideOverlay({ id: overlayId, points: orig.current.points }) }
    const o = chart.getOverlays({ id: overlayId })[0]
    if (keep && o) void saveOverlay(symbol.id, o)
    onClose()
  }
  if (!overlay) return null

  const risk = cfg.accountSize * cfg.riskPct / 100
  const qty = valid ? risk / Math.abs(e - s) : 0
  const field = (label: string, value: string, onChange: (v: string) => void, suffix?: string) => (
    <label className="grid grid-cols-[110px_1fr] items-center gap-3 py-1.5">
      <span className="text-muted">{label}</span>
      <div className="field h-9 px-3">
        <input value={value} onChange={(ev) => onChange(ev.target.value)} inputMode="decimal" className="h-full flex-1 text-[13px]" />
        {suffix && <span className="text-[12px] text-faint">{suffix}</span>}
      </div>
    </label>
  )

  return (
    <Modal
      title={tr(long ? 'Long position' : 'Short position')} onClose={() => finish(false)} width={440}
      footer={
        <div className="flex flex-wrap items-center gap-2">
          <button className="tb-btn !h-8 text-[12px]" onClick={() => { setSettings({ positionDefaults: cfg }); setNote(tr('Saved as default')) }}>{tr('Save as default')}</button>
          {note && <span className="text-[11px] text-faint">{note}</span>}
          <div className="ml-auto flex gap-2">
            <button className="tb-btn !h-8 !px-3" onClick={() => finish(false)}>{tr('Cancel')}</button>
            <button className="h-8 rounded-md px-4 text-[13px] font-medium disabled:opacity-40" disabled={!valid} style={{ background: 'var(--accent)', color: 'var(--bg)' }} onClick={() => finish(true)}>{tr('Ok')}</button>
          </div>
        </div>
      }
    >
      <div className="px-4 py-3">
        <div className="pb-1 text-[12px] text-faint">{tr('Trading account')}</div>
        {field(tr('Account size'), raw.acct, (v) => setRaw((r) => ({ ...r, acct: v })))}
        {field(tr('Risk'), raw.risk, (v) => setRaw((r) => ({ ...r, risk: v })), `% = ${risk.toLocaleString('en-US', { maximumFractionDigits: 2 })}`)}
        {field(tr('Qty decimals'), raw.qp, (v) => setRaw((r) => ({ ...r, qp: v })))}
        <div className="pb-1 pt-3 text-[12px] text-faint">{tr('Levels')}</div>
        {field(tr('Entry price'), prices.entry, (v) => setPrices((x) => ({ ...x, entry: v })))}
        {field(tr('Target price'), prices.target, (v) => setPrices((x) => ({ ...x, target: v })), valid ? `${(Math.abs(t - e) / e * 100).toFixed(2)}%` : undefined)}
        {field(tr('Stop price'), prices.stop, (v) => setPrices((x) => ({ ...x, stop: v })), valid ? `${(Math.abs(e - s) / e * 100).toFixed(2)}%` : undefined)}
        <div className="mt-3 rounded-lg border border-line px-3 py-2 text-[12px] text-muted">
          {valid
            ? <>{tr('Qty')} <b className="text-text">{qty.toFixed(cfg.qtyPrecision)}</b> · {tr('Risk/Reward')} <b className="text-text">{(Math.abs(t - e) / Math.abs(e - s)).toFixed(2)}</b> · {tr('Target amount')} <b className="num-up">{(qty * Math.abs(t - e)).toFixed(2)}</b> · {tr('Stop amount')} <b className="num-down">{risk.toFixed(2)}</b></>
            : <span style={{ color: 'var(--warn)' }}>{tr(long ? 'For a long, target must be above entry and stop below.' : 'For a short, target must be below entry and stop above.')}</span>}
        </div>
      </div>
    </Modal>
  )
}
