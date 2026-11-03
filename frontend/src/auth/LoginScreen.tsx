import { useEffect, useRef, useState } from 'react'
import { LangSwitch } from '../components/LangSwitch'
import { DonateModal } from '../components/DonateModal'
import { Logo } from '../components/Logo'
import { t } from '../i18n'
import { startHero } from './heroAnimation'
import { sendEmailCode, signInWithGoogle, verifyEmailCode } from './session'

const GoogleMark = () => (
  <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" aria-hidden="true">
    <path fill="#4285F4" d="M22.6 12.3c0-.8-.1-1.6-.2-2.3H12v4.4h5.9a5 5 0 0 1-2.2 3.3v2.7h3.6c2.1-1.9 3.3-4.8 3.3-8.1z" />
    <path fill="#34A853" d="M12 23c3 0 5.5-1 7.3-2.7l-3.6-2.7c-1 .7-2.2 1.1-3.7 1.1-2.9 0-5.3-1.9-6.2-4.5H2.1v2.8A11 11 0 0 0 12 23z" />
    <path fill="#FBBC05" d="M5.8 14.2a6.6 6.6 0 0 1 0-4.3V7.1H2.1a11 11 0 0 0 0 9.9l3.7-2.8z" />
    <path fill="#EA4335" d="M12 5.4c1.6 0 3.1.6 4.2 1.7l3.2-3.2A11 11 0 0 0 2.1 7.1l3.7 2.8C6.7 7.3 9.1 5.4 12 5.4z" />
  </svg>
)

const message = (e: unknown) => {
  const m = e instanceof Error ? e.message : String(e)
  if (/rate limit|seconds/i.test(m)) return t('Too many attempts. Wait a minute and try again.')
  if (/expired|invalid/i.test(m)) return t('That code is wrong or has expired. Check the latest email or send a new code.')
  return t(m)
}

function Hero() {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => (ref.current ? startHero(ref.current) : undefined), [])
  return <canvas ref={ref} className="absolute inset-0 block h-full w-full" aria-label={t('Animated chart: candles, moving averages, Bollinger bands and drawing tools')} role="img" />
}

/** Sign-in for the public instance: an animated chart on the left, Google or an email code on the right. */
export function LoginScreen() {
  const [donate, setDonate] = useState(false)
  const [step, setStep] = useState<'start' | 'code'>('start')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cooldown, setCooldown] = useState(0)
  const codeRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (cooldown <= 0) return
    const id = window.setTimeout(() => setCooldown((c) => c - 1), 1000)
    return () => window.clearTimeout(id)
  }, [cooldown])

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null)
    try { await fn() } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }

  const send = () => run(async () => {
    await sendEmailCode(email.trim())
    setStep('code'); setCode(''); setCooldown(60)
    window.setTimeout(() => codeRef.current?.focus(), 0)
  })

  const verify = () => run(() => verifyEmailCode(email.trim(), code.trim()))

  return (
    <div className="grid min-h-full bg-bg text-text md:h-full md:grid-cols-[minmax(0,1fr)_440px]" style={{ gridTemplateRows: 'auto' }}>
      <div className="relative h-[42vh] min-h-[260px] overflow-hidden md:h-auto"><Hero /></div>

      <div className="relative flex items-center justify-center border-t border-line px-4 pb-10 pt-8 md:border-l md:border-t-0 md:px-8 md:py-10">
        <LangSwitch className="absolute right-4 top-4" />
        <div className="w-full max-w-[360px]">
          <div className="mb-8 flex flex-col items-center text-center">
            <Logo className="mb-4 h-12 w-12 text-text" />
            <h1 className="text-[22px] font-semibold leading-7">FibTrader View</h1>
            <p className="mt-1.5 text-[14px] leading-5 text-muted">{t('Charts of crypto, stocks, indices and commodities with drawing tools. Sign in to keep your drawings and watchlists on every device.')}</p>
            <p className="mt-2 text-[12px] leading-4 text-faint">{t('No limit on indicators · alerts in Telegram · funding, open interest and liquidations')}</p>
          </div>

          {step === 'start' && (
            <div className="grid gap-3">
              <button className="flex h-11 items-center justify-center gap-2.5 rounded-lg border border-line bg-panel text-[14px] font-medium hover:bg-raised disabled:opacity-50"
                disabled={busy} onClick={() => run(signInWithGoogle)}>
                <GoogleMark />{t('Continue with Google')}
              </button>
              <div className="flex items-center gap-3 py-1 text-[12px] text-faint"><span className="h-px flex-1 bg-line" />{t('or')}<span className="h-px flex-1 bg-line" /></div>
              <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); if (email.trim()) void send() }}>
                <label className="grid gap-1.5">
                  <span className="text-[12px] text-muted">{t('Email')}</span>
                  <div className="field h-11 px-3">
                    <input type="email" required autoComplete="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@example.com" className="h-full flex-1 text-[14px] placeholder:text-faint" />
                  </div>
                </label>
                <button type="submit" className="h-11 rounded-lg text-[14px] font-medium disabled:opacity-50" style={{ background: 'var(--accent)', color: 'var(--bg)' }}
                  disabled={busy || !email.trim()}>{t('Email me a sign-in code')}</button>
              </form>
            </div>
          )}

          {step === 'code' && (
            <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); if (code.trim().length >= 6) void verify() }}>
              <p className="text-[14px] leading-5 text-muted">{t('We sent a code to {email}. Enter it here, or open the link in the email on this device.', { email: email.trim() })}</p>
              <label className="grid gap-1.5">
                <span className="text-[12px] text-muted">{t('Code')}</span>
                <div className="field h-11 px-3">
                  <input ref={codeRef} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
                    inputMode="numeric" autoComplete="one-time-code" placeholder="123456" className="h-full flex-1 text-[16px] tracking-[0.2em] placeholder:tracking-normal placeholder:text-faint" />
                </div>
              </label>
              <button type="submit" className="h-11 rounded-lg text-[14px] font-medium disabled:opacity-50" style={{ background: 'var(--accent)', color: 'var(--bg)' }}
                disabled={busy || code.trim().length < 6}>{t('Sign in')}</button>
              <div className="flex items-center justify-between text-[13px]">
                <button type="button" className="text-muted hover:text-text" onClick={() => { setStep('start'); setError(null) }}>{t('Use another email')}</button>
                <button type="button" className="text-muted hover:text-text disabled:opacity-50" disabled={busy || cooldown > 0} onClick={() => void send()}>
                  {cooldown > 0 ? t('Send again in {s}s', { s: cooldown }) : t('Send a new code')}
                </button>
              </div>
            </form>
          )}

          {error && <p role="alert" className="mt-4 rounded-lg border px-3 py-2 text-[13px] leading-5" style={{ borderColor: 'var(--warn)', color: 'var(--warn)' }}>{error}</p>}
          <p className="mt-8 text-center text-[12px] leading-4 text-faint">{t('Bybit perpetual and spot markets. Your drawings are private to your account.')}</p>
          <p className="mt-3 text-center text-[12px]">
            <button className="text-muted underline decoration-line underline-offset-4 hover:text-text" onClick={() => setDonate(true)}>{t('Support the project')}</button>
          </p>
          {donate && <DonateModal onClose={() => setDonate(false)} />}
        </div>
      </div>
    </div>
  )
}
