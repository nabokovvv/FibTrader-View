import { Bell, BellOff, RotateCcw, Send, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { ApiError, api, type Alert, type AlertCond } from '../api/client'
import { serverLimits } from '../auth/session'
import { getLang, t } from '../i18n'
import { idTicker } from '../lib/types'
import { COND_LABEL, alertMarkets, alertTarget, useAlerts } from '../state/alerts'
import { useStore } from '../state/store'
import { fmtPrice, Modal } from './ui'

const CONDS: AlertCond[] = ['cross', 'up', 'down']

/** Alerts: Telegram link, a new alert for the current symbol (or the selected drawing), and the list. */
export function AlertsModal({ onClose }: { onClose: () => void }) {
  const symbol = useStore((s) => s.symbol)
  const lastBar = useStore((s) => s.lastBar)
  const quote = useStore((s) => (s.symbol ? s.quotes[s.symbol.id] : undefined))
  const { alerts, telegram, loaded, draft, load } = useAlerts()
  const [err, setErr] = useState<string | null>(null)
  const supported = !!symbol && alertMarkets().includes(symbol.market)
  const price = quote?.p ?? lastBar?.close
  // a price picked on the chart (crosshair bell / right-click) wins over the current price; it is used once
  const [level, setLevel] = useState(() => {
    const preset = useAlerts.getState().preset
    if (!draft && preset != null) return preset.toFixed(Math.max(0, Math.min(symbol?.price_precision ?? 2, 10)))
    return (draft?.level ?? price ?? '').toString()
  })
  useEffect(() => { useAlerts.setState({ preset: null }) }, [])
  const [cond, setCond] = useState<AlertCond>('cross')
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
    const lv = Number(level.replace(',', '.').replace(/\s/g, ''))
    if (!draft?.line && !(lv > 0)) { setErr(t('Enter a price above zero.')); return }
    setBusy(true)
    try {
      await api.createAlert({ symbol: symbol.id, cond, note: note.trim(), ...(draft?.line ? { line: draft.line } : { level: lv }),
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
              <div className="flex flex-wrap items-center gap-2">
                <select className="h-8 rounded-md border border-line bg-bg px-2 text-[13px]" value={cond} onChange={(e) => setCond(e.target.value as AlertCond)} aria-label={t('Condition')}>
                  {CONDS.map((c) => <option key={c} value={c}>{t(COND_LABEL[c])}</option>)}
                </select>
                {!draft?.line && (
                  <input className="h-8 w-[140px] rounded-md border border-line bg-bg px-2 text-[13px]" inputMode="decimal" value={level}
                    readOnly={!!draft} onChange={(e) => setLevel(e.target.value)} aria-label={t('Price')} placeholder={t('Price')} />
                )}
                {price !== undefined && !draft && <span className="text-[11px] text-muted">{t('now {price}', { price: fmtPrice(price, symbol?.price_precision) })}</span>}
              </div>
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
        <span className="font-semibold">{name}</span>{spot && <span className="text-muted"> · {t('spot')}</span>}
        <span className="text-muted"> · {t(COND_LABEL[a.cond])} {alertTarget(a)}</span>
        {a.note && <span className="block truncate text-muted">{a.note}</span>}
        {!a.active && a.fired_at && <span className="block text-muted">{t('Fired {when} at {price}', { when, price: fmtPrice(a.fired_price) })}</span>}
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
