import { Bell, BellOff, RotateCcw, Send, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api, type Alert, type AlertCond, type AlertMetric } from '../api/client'
import { serverLimits } from '../auth/session'
import { getLang, t } from '../i18n'
import { idTicker } from '../lib/types'
import { alertAvailable } from '../chart/indicators/catalog'
import { COND_LABEL, MARKET_METRICS, METRIC_LABEL, alertMarkets, alertTarget, fmtMetric, useAlerts } from '../state/alerts'
import { useStore } from '../state/store'
import { fmtPrice, Modal } from './ui'

const CONDS: AlertCond[] = ['cross', 'up', 'down']
const HINT: Partial<Record<AlertMetric, string>> = {
  funding: 'Current funding rate in %, e.g. 0.01 or -0.01 (Hyperliquid: hourly rate).',
  oi: 'Open interest in coins, as on the Open interest panel.',
  fng: 'Fear & Greed Index 0–100, checked once a day after 00:05 UTC.',
  yt: 'Influencer pressure (any sign), checked once a day after ~02:15 UTC.',
}
const DIGITS: Record<AlertMetric, number> = { price: 2, funding: 4, oi: 0, fng: 0, yt: 1 }

/** The newest value of a non-price metric, to prefill the level (funding: the last settled rate). */
async function latest(metric: AlertMetric, id: string): Promise<number | undefined> {
  const now = Date.now()
  if (metric === 'fng' || metric === 'yt') return (await api.sentiment(metric)).points.at(-1)?.[1]
  if (metric === 'funding') return ((await api.derivs('funding', id, '1D', now - 30 * 86_400_000, now + 60_000)).points.at(-1)?.[1] ?? NaN) * 100
  if (metric === 'oi') return (await api.derivs('oi', id, '1h', now - 3 * 86_400_000, now + 60_000)).points.at(-1)?.[1]
  return undefined
}

/** Alerts: Telegram link, a new alert for the current symbol (or the selected drawing), and the list. */
export function AlertsModal({ onClose }: { onClose: () => void }) {
  const symbol = useStore((s) => s.symbol)
  const lastBar = useStore((s) => s.lastBar)
  const quote = useStore((s) => (s.symbol ? s.quotes[s.symbol.id] : undefined))
  const { alerts, telegram, loaded, draft, load, metrics } = useAlerts()
  const [err, setErr] = useState<string | null>(null)
  const supported = !!symbol && alertMarkets().includes(symbol.market)
  const price = quote?.p ?? lastBar?.close
  // a price picked on the chart (crosshair bell / right-click) wins over the current price; it is used once
  const [preset] = useState(() => (draft ? null : useAlerts.getState().preset))
  const [level, setLevel] = useState(() => {
    if (preset) return preset.value.toFixed(preset.metric === 'price' ? Math.max(0, Math.min(symbol?.price_precision ?? 2, 10)) : DIGITS[preset.metric])
    return (draft?.level ?? price ?? '').toString()
  })
  useEffect(() => { useAlerts.setState({ preset: null }) }, [])
  const [cond, setCond] = useState<AlertCond>('cross')
  const [metric, setMetric] = useState<AlertMetric>(preset?.metric ?? 'price')
  const [now, setNow] = useState<number | undefined>(undefined)
  const choices = metrics.filter((m) => alertAvailable(m, symbol))   // where the metric's indicator works (catalog.ts)
  const pickGen = useRef(0)   // an answer for an earlier choice (metric or symbol) must not fill the current one
  const shownFor = useRef(symbol?.id)
  useEffect(() => {
    if (shownFor.current === symbol?.id) return  // the form opened on this symbol: keep what the chart picked
    shownFor.current = symbol?.id
    pickGen.current++; setMetric('price'); setNow(undefined)
  }, [symbol?.id])
  useEffect(() => {   // opened from an indicator panel: show its newest value next to the picked level
    if (!preset || preset.metric === 'price' || !symbol) return
    const gen = pickGen.current
    latest(preset.metric, symbol.id).then((v) => { if (gen === pickGen.current && v !== undefined && Number.isFinite(v)) setNow(v) }).catch(() => {})
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (m: AlertMetric) => {
    const gen = ++pickGen.current
    setMetric(m)
    setNow(undefined)
    if (m === 'price') { setLevel((price ?? '').toString()); return }
    setLevel('')
    if (!symbol) return
    latest(m, symbol.id).then((v) => {
      if (gen !== pickGen.current || v === undefined || !Number.isFinite(v)) return
      setNow(v)
      setLevel((cur) => cur || v.toFixed(DIGITS[m]))
    }).catch(() => { /* the level is typed by hand */ })
  }
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const max = serverLimits()?.alerts
  const active = alerts.filter((a) => a.active).length

  useEffect(() => { load().catch((e: Error) => setErr(e.message)) }, [load])

  const fail = (e: unknown) => setErr(e instanceof ApiError && e.status === 409
    ? (e.detail.includes('paused') ? t('Too many paused alerts. Delete the ones you no longer need.') : t('You have {n} armed alerts, the maximum. Delete or wait for some to fire.', { n: max ?? active }))
    : (e as Error).message)

  const create = async () => {
    if (!symbol) return
    setErr(null)
    const m: AlertMetric = draft ? 'price' : metric
    const raw = level.replace(',', '.').replace(/\s/g, '')
    const lv = raw === '' ? NaN : Number(raw)
    if (!draft?.line) {
      if ((m === 'price' || m === 'oi') && !(lv > 0)) { setErr(t(m === 'price' ? 'Enter a price above zero.' : 'Enter a value above zero.')); return }
      if (m === 'fng' && !(lv >= 0 && lv <= 100)) { setErr(t('Enter a value from 0 to 100.')); return }
      if (!Number.isFinite(lv)) { setErr(t('Enter a number.')); return }
    }
    setBusy(true)
    try {
      await api.createAlert({ symbol: symbol.id, cond, metric: m, note: note.trim(), ...(draft?.line ? { line: draft.line } : { level: lv }),
        ...(draft ? { drawing_id: draft.drawingId } : {}) })
      useAlerts.getState().setDraft(null)
      setNote('')
      await load()
      useStore.setState({ notice: t('Alert created') })
    } catch (e) { fail(e) } finally { setBusy(false) }
  }

  const act = async (fn: () => Promise<unknown>) => {
    setErr(null)
    try { await fn(); await load() } catch (e) { fail(e) }
  }

  const sorted = useMemo(() => alerts, [alerts])

  return (
    <Modal title={t('Alerts')} onClose={onClose} width={540}>
      <div className="flex flex-col gap-4 p-4">
        <TelegramBlock available={telegram} />

        <section className="rounded-lg border border-line p-3">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <h3 className="text-[13px] font-semibold">{symbol ? t('New alert: {name}', { name: symbol.display || symbol.symbol }) : t('New alert')}</h3>
            {max !== undefined && <span className="text-[11px] text-muted">{t('{n} of {max} armed', { n: active, max })}</span>}
          </div>
          {!supported ? (
            <p className="text-[12px] text-muted">{t('Alerts work for Bybit perpetuals and spot for now.')}</p>
          ) : (
            <div className="flex flex-col gap-2">
              {draft && (
                <p className="text-[12px] text-muted">
                  {draft.line ? t('On your trend line. The alert follows the line if you move it.') : t('On your horizontal line. The alert follows the line if you move it.')}
                </p>
              )}
              {!draft && choices.length > 1 && (
                <select className="h-8 rounded-md border border-line bg-bg px-2 text-[13px]" value={metric} onChange={(e) => pick(e.target.value as AlertMetric)} aria-label={t('Watch')}>
                  {choices.map((m) => <option key={m} value={m}>{t(METRIC_LABEL[m])}{MARKET_METRICS.includes(m) ? ` · ${t('whole market')}` : ''}</option>)}
                </select>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <select className="h-8 rounded-md border border-line bg-bg px-2 text-[13px]" value={cond} onChange={(e) => setCond(e.target.value as AlertCond)} aria-label={t('Condition')}>
                  {CONDS.map((c) => <option key={c} value={c}>{t(COND_LABEL[c])}</option>)}
                </select>
                {!draft?.line && (
                  <input className="h-8 w-[140px] rounded-md border border-line bg-bg px-2 text-[13px]" inputMode="decimal" value={level}
                    readOnly={!!draft} onChange={(e) => setLevel(e.target.value)} aria-label={t(metric === 'price' ? 'Price' : 'Level')} placeholder={t(metric === 'price' ? 'Price' : 'Level')} />
                )}
                {metric === 'price' && price !== undefined && !draft && <span className="text-[11px] text-muted">{t('now {price}', { price: fmtPrice(price, symbol?.price_precision) })}</span>}
                {metric !== 'price' && now !== undefined && <span className="text-[11px] text-muted">{t(metric === 'funding' ? 'last paid {value}' : 'latest {value}', { value: fmtMetric(metric, now) })}</span>}
              </div>
              {!draft && HINT[metric] && <p className="text-[11px] text-muted">{t(HINT[metric]!)}</p>}
              <input className="h-8 rounded-md border border-line bg-bg px-2 text-[13px]" maxLength={140} value={note} onChange={(e) => setNote(e.target.value)}
                placeholder={t('Note (optional), e.g. “breakout, check volume”')} />
              <div className="flex items-center gap-2">
                <button className="tb-btn !h-8 !px-3 text-[13px]" style={{ background: 'var(--brand)', color: '#fff' }} disabled={busy} onClick={() => void create()}>
                  <Bell className="!h-4 !w-4" />{t('Create alert')}
                </button>
                <span className="text-[11px] text-muted">{t('Fires once, then switches off.')}</span>
              </div>
            </div>
          )}
        </section>

        {err && <p className="text-[12px]" style={{ color: 'var(--down)' }}>{err}</p>}

        <section>
          <h3 className="mb-2 text-[13px] font-semibold">{t('Your alerts')}</h3>
          {!loaded ? <p className="text-[12px] text-muted">{t('Loading…')}</p> : !sorted.length ? (
            <p className="text-[12px] text-muted">{t('No alerts yet.')}</p>
          ) : (
            <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
              {sorted.map((a) => <AlertRow key={a.id} a={a} onAct={act} onOpen={() => { void useStore.getState().setSymbolById(a.symbol); onClose() }} />)}
            </ul>
          )}
        </section>
      </div>
    </Modal>
  )
}

function AlertRow({ a, onAct, onOpen }: { a: Alert; onAct: (fn: () => Promise<unknown>) => void; onOpen: () => void }) {
  const name = idTicker(a.symbol)
  const spot = a.symbol.startsWith('bybit-spot:')
  const when = a.fired_at ? new Date(a.fired_at).toLocaleString(getLang() === 'ru' ? 'ru-RU' : 'en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''
  return (
    <li className="flex items-center gap-2 px-3 py-2 text-[12px]">
      <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${a.active ? 'pulse' : ''}`} style={{ background: a.active ? 'var(--up)' : 'var(--faint)' }} />
      <button className="min-w-0 flex-1 text-left" onClick={onOpen} title={t('Open chart')}>
        {MARKET_METRICS.includes(a.metric ?? 'price')
          ? <span className="font-semibold">{t(METRIC_LABEL[a.metric!])}</span>
          : <><span className="font-semibold">{name}</span>{spot && <span className="text-muted"> · {t('spot')}</span>}
            {(a.metric ?? 'price') !== 'price' && <span className="text-muted"> · {t(METRIC_LABEL[a.metric!])}</span>}</>}
        <span className="text-muted"> · {t(COND_LABEL[a.cond])} {alertTarget(a)}</span>
        {a.note && <span className="block truncate text-muted">{a.note}</span>}
        {!a.active && a.fired_at && <span className="block text-muted">{t('Fired {when} at {price}', { when, price: fmtMetric(a.metric, a.fired_price) })}</span>}
        {!a.active && !a.fired_at && <span className="block text-muted">{t('Paused')}</span>}
      </button>
      {a.active
        ? <button className="tb-btn" title={t('Pause')} onClick={() => onAct(() => api.updateAlert(a.id, { active: false }))}><BellOff /></button>
        : <button className="tb-btn" title={t('Arm again')} onClick={() => onAct(() => api.updateAlert(a.id, { active: true }))}><RotateCcw /></button>}
      <button className="tb-btn" title={t('Delete')} onClick={() => onAct(() => api.deleteAlert(a.id))}><Trash2 /></button>
    </li>
  )
}

function TelegramBlock({ available }: { available: boolean }) {
  const [state, setState] = useState<{ linked: boolean; bot: string | null } | null>(null)
  const [waiting, setWaiting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!available) return
    let alive = true
    api.telegram().then((s) => { if (alive) setState(s) }).catch(() => { /* shown as not linked */ })
    return () => { alive = false }
  }, [available])

  useEffect(() => {
    if (!waiting) return
    let n = 0
    const id = window.setInterval(() => {
      if (++n > 60) { setWaiting(false); return }
      api.telegram().then((s) => { if (s.linked) { setState(s); setWaiting(false) } }).catch(() => { /* keep waiting */ })
    }, 3000)
    return () => window.clearInterval(id)
  }, [waiting])

  if (!available) {
    return <p className="rounded-lg border border-line p-3 text-[12px] text-muted">{t('Alerts show up here while the site is open.')}</p>
  }
  const connect = async () => {
    setErr(null)
    const w = window.open('about:blank', '_blank')  // opened inside the click: Safari blocks it after an await
    try {
      const { url } = await api.telegramLink(getLang())
      if (w) w.location.href = url
      else window.location.href = url
      setWaiting(true)
    } catch (e) { w?.close(); setErr((e as Error).message) }
  }
  return (
    <section className="flex items-center gap-3 rounded-lg border border-line p-3">
      <Send className="h-5 w-5 shrink-0" style={{ color: state?.linked ? 'var(--up)' : 'var(--muted)' }} />
      <div className="min-w-0 flex-1 text-[12px]">
        {state?.linked
          ? <><div className="font-semibold">{t('Telegram is connected')}</div><div className="text-muted">{t('Alerts come to @{bot}.', { bot: state.bot ?? '' })}</div></>
          : <><div className="font-semibold">{t('Get alerts in Telegram')}</div><div className="text-muted">{waiting ? t('Press Start in Telegram, then come back here.') : t('One tap: the bot opens, you press Start.')}</div></>}
        {err && <div style={{ color: 'var(--down)' }}>{err}</div>}
      </div>
      {state?.linked
        ? <button className="tb-btn !px-2 text-[12px]" onClick={() => void api.telegramUnlink().then(() => setState({ linked: false, bot: state.bot }))}>{t('Disconnect')}</button>
        : <button className="tb-btn !h-8 !px-3 text-[12px]" style={{ background: 'var(--raised)' }} onClick={() => void connect()}>{t('Connect Telegram')}</button>}
    </section>
  )
}
