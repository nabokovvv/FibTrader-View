import { getLang, langSwitchable, setLang, type Lang } from '../i18n'
import { flushPendingSaves } from '../state/store'

const OPTIONS: Array<{ v: Lang; label: string; title: string }> = [
  { v: 'en', label: 'EN', title: 'English' },
  { v: 'ru', label: 'RU', title: 'Русский' },
]

/** Small EN / RU segmented switch (public instance only). */
export function LangSwitch({ className = '' }: { className?: string }) {
  if (!langSwitchable()) return null
  const cur = getLang()
  return (
    <div className={`inline-flex rounded-md border border-line p-0.5 text-[11px] font-medium ${className}`} role="group" aria-label="Language / Язык">
      {OPTIONS.map((o) => (
        <button key={o.v} title={o.title} aria-pressed={cur === o.v} onClick={() => { void flushPendingSaves().finally(() => setLang(o.v)) }}
          className="h-6 rounded px-2 tracking-wide"
          style={cur === o.v ? { background: 'var(--raised)', color: 'var(--text)' } : { color: 'var(--muted)' }}>
          {o.label}
        </button>
      ))}
    </div>
  )
}
