import { useStore } from '../state/store'
import { langSwitchable, t } from '../i18n'
import { LangSwitch } from './LangSwitch'
import { Modal, Toggle } from './ui'

const PALETTES = [
  { name: 'Standard', up: '#089981', down: '#f23645' },
  { name: 'Standard (muted)', up: '#26a69a', down: '#ef5350' },
  { name: 'Lime / coral (high contrast)', up: '#a3e635', down: '#ff5d52' },
  { name: 'Mono (hollow)', up: '#d7dbe0', down: '#6b7380' },
]

const TIMEZONES = [
  'UTC', 'Europe/Nicosia', 'Europe/Moscow', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Chicago',
  'Asia/Dubai', 'Asia/Singapore', 'Asia/Hong_Kong', 'Asia/Tokyo', 'Australia/Sydney',
]

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const settings = useStore((s) => s.settings)
  const setSettings = useStore((s) => s.setSettings)
  const theme = useStore((s) => s.theme)
  const setTheme = useStore((s) => s.setTheme)
  const timezone = useStore((s) => s.timezone)
  const setTimezone = useStore((s) => s.setTimezone)
  const local = Intl.DateTimeFormat().resolvedOptions().timeZone
  const tzs = TIMEZONES.includes(local) ? TIMEZONES : [local, ...TIMEZONES]

  return (
    <Modal title={t('Settings')} onClose={onClose} width={480}>
      <div className="px-4 py-3">
        {langSwitchable() && (
          <div className="flex items-center justify-between pb-3">
            <span className="text-[12px] text-faint">{t('Language')}</span>
            <LangSwitch />
          </div>
        )}
        <div className="pb-1 text-[12px] text-faint">{t('Appearance')}</div>
        <div className="flex gap-1 py-2">
          {(['dark', 'light'] as const).map((th) => (
            <button key={th} className="tb-btn flex-1 capitalize" data-active={theme === th} onClick={() => setTheme(th)}>{t(th)}</button>
          ))}
        </div>
        <div className="pt-3 pb-1 text-[12px] text-faint">{t('Candle colours')}</div>
        <div className="grid gap-1">
          {PALETTES.map((p) => (
            <button key={p.name} className="flex items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-raised"
              style={{ background: settings.upColor === p.up && settings.downColor === p.down ? 'var(--raised)' : undefined }}
              onClick={() => setSettings({ upColor: p.up, downColor: p.down })}>
              <span className="flex gap-1"><i className="block h-4 w-2.5 rounded-sm" style={{ background: p.up }} /><i className="block h-4 w-2.5 rounded-sm" style={{ background: p.down }} /></span>
              <span>{t(p.name)}</span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 py-2">
          <label className="flex items-center gap-2 text-[12px] text-muted">{t('Up')} <input type="color" value={settings.upColor} onChange={(e) => setSettings({ upColor: e.target.value })} className="h-7 w-10 cursor-pointer rounded border border-line bg-transparent" /></label>
          <label className="flex items-center gap-2 text-[12px] text-muted">{t('Down')} <input type="color" value={settings.downColor} onChange={(e) => setSettings({ downColor: e.target.value })} className="h-7 w-10 cursor-pointer rounded border border-line bg-transparent" /></label>
        </div>

        <div className="pt-3 pb-1 text-[12px] text-faint">{t('Chart')}</div>
        <Toggle label={t('Grid lines')} checked={settings.showGrid} onChange={(v) => setSettings({ showGrid: v })} />
        <Toggle label={t('Last price line')} checked={settings.showLastPrice} onChange={(v) => setSettings({ showLastPrice: v })} />

        <div className="pt-3 pb-1 text-[12px] text-faint">{t('Time zone')}</div>
        <select value={timezone} onChange={(e) => setTimezone(e.target.value)} className="field h-9 w-full px-2">
          {tzs.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
        </select>

        <div className="pt-4 text-[12px] text-faint">
          {t('Shortcuts:')} <span className="kbd">⌘K</span> {t('search')} · <span className="kbd">Alt+T</span> {t('trend line')} · <span className="kbd">Alt+H</span> {t('horizontal')} · <span className="kbd">Alt+V</span> {t('vertical')} · <span className="kbd">Alt+F</span> Fib · <span className="kbd">Esc</span> {t('cancel')} · <span className="kbd">Del</span> {t('delete selected')}
        </div>
      </div>
    </Modal>
  )
}
