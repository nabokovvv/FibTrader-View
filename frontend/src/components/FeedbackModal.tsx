import { Bug, Lightbulb } from 'lucide-react'
import { useState } from 'react'
import { ApiError, api } from '../api/client'
import { serverLimits } from '../auth/session'
import { getLang, t } from '../i18n'
import { useStore } from '../state/store'
import { Modal } from './ui'

/** "Suggest a feature" / "Report a bug" → a public GitHub issue (no email or account id in it). */
export function FeedbackModal({ onClose }: { onClose: () => void }) {
  const symbol = useStore((s) => s.symbol)
  const interval = useStore((s) => s.interval)
  const [kind, setKind] = useState<'idea' | 'bug'>('idea')
  const [text, setText] = useState('')
  const [attach, setAttach] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const ctx = {
    symbol: symbol?.id ?? '', interval, lang: getLang(),
    browser: browserName(), screen: `${window.innerWidth}×${window.innerHeight}`, version: __APP_VERSION__,
  }
  const send = async () => {
    setErr(null)
    if (text.trim().length < 3) { setErr(t('Write a few words first.')); return }
    setBusy(true)
    try {
      await api.sendFeedback(kind, text.trim(), attach ? ctx : undefined)
      useStore.setState({ notice: t('Thanks! Your message was sent.') })
      onClose()
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 429
        ? t('You can send {n} messages a day. Try again tomorrow.', { n: serverLimits()?.feedback_per_day ?? 5 })
        : (e as Error).message)
    } finally { setBusy(false) }
  }
  return (
    <Modal title={t('Suggest a feature')} onClose={onClose} width={520}>
      <div className="flex flex-col gap-3 p-4">
        <div className="inline-flex self-start rounded-md border border-line p-0.5 text-[12px]" role="group">
          {([['idea', 'Idea', Lightbulb], ['bug', 'Bug', Bug]] as const).map(([k, label, Icon]) => (
            <button key={k} className="flex h-7 items-center gap-1.5 rounded px-3" aria-pressed={kind === k} onClick={() => setKind(k)}
              style={kind === k ? { background: 'var(--raised)', color: 'var(--text)' } : { color: 'var(--muted)' }}>
              <Icon className="h-3.5 w-3.5" />{t(label)}
            </button>
          ))}
        </div>
        <textarea autoFocus className="min-h-[140px] rounded-md border border-line bg-bg p-2 text-[13px] outline-none focus:border-muted" maxLength={2000}
          value={text} onChange={(e) => setText(e.target.value)}
          placeholder={kind === 'idea' ? t('What would make the charts better for you?') : t('What happened, and what did you expect?')} />
        <div className="flex items-center justify-between text-[11px] text-faint">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={attach} onChange={(e) => setAttach(e.target.checked)} />
            {t('Attach: {ctx}', { ctx: [ctx.symbol, ctx.interval, ctx.browser].filter(Boolean).join(' · ') })}
          </label>
          <span>{text.length}/2000</span>
        </div>
        <p className="text-[11px] text-muted">{t('Your message will be published as a public GitHub issue, without your email or name.')}</p>
        {err && <p className="text-[12px]" style={{ color: 'var(--down)' }}>{err}</p>}
        <div className="flex justify-end">
          <button className="tb-btn !h-8 !px-4 text-[13px]" style={{ background: 'var(--brand)', color: '#fff' }} disabled={busy} onClick={() => void send()}>{t('Send')}</button>
        </div>
      </div>
    </Modal>
  )
}

function browserName(): string {
  const ua = navigator.userAgent
  const m = /(Edg|OPR|Firefox|Chrome|Version)\/(\d+)/.exec(ua)
  const name = !m ? 'Other' : m[1] === 'Edg' ? 'Edge' : m[1] === 'OPR' ? 'Opera' : m[1] === 'Version' ? 'Safari' : m[1]
  const os = /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /Mac/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : ''
  return [name + (m ? ` ${m[2]}` : ''), os].filter(Boolean).join(', ')
}
