import { HeartHandshake, LogOut, MessageSquarePlus } from 'lucide-react'
import { useStore } from '../state/store'
import { serverFeatures } from './session'
import { useEffect, useRef, useState } from 'react'
import { t } from '../i18n'
import { currentUser, signOut } from './session'

/** Avatar in the top bar with the account email and Sign out. Hidden on the private instance. */
export function AccountButton() {
  const user = currentUser()
  const [open, setOpen] = useState(false)
  const [broken, setBroken] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent | TouchEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('touchstart', onDoc)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('touchstart', onDoc) }
  }, [open])
  if (!user) return null
  const label = user.name || user.email || t('Account')
  const initial = label.trim().charAt(0).toUpperCase() || '?'
  return (
    <div ref={ref} className="relative">
      <button className="tb-btn" aria-label={t('Account')} title={label} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {user.avatar && !broken
          ? <img src={user.avatar} alt="" referrerPolicy="no-referrer" className="h-6 w-6 rounded-full" onError={() => setBroken(true)} />
          : <span className="flex h-6 w-6 items-center justify-center rounded-full bg-raised text-[12px] font-medium">{initial}</span>}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-40 mt-1.5 w-[240px] rounded-lg border border-line bg-panel p-1 shadow-xl">
          <div className="px-3 py-2">
            {user.name && <div className="truncate text-[13px] font-medium">{user.name}</div>}
            {user.email && <div className="truncate text-[12px] text-muted">{user.email}</div>}
          </div>
          <div className="my-1 h-px bg-line" />
          {serverFeatures().feedback && (
            <button className="tb-btn w-full !justify-start gap-2 !px-3 text-[13px]" onClick={() => { setOpen(false); useStore.getState().setSheet('feedback') }}>
              <MessageSquarePlus className="!h-4 !w-4" />{t('Suggest a feature')}
            </button>
          )}
          <button className="tb-btn w-full !justify-start gap-2 !px-3 text-[13px]" onClick={() => { setOpen(false); useStore.getState().setSheet('donate') }}>
            <HeartHandshake className="!h-4 !w-4" style={{ color: 'var(--brand)' }} />{t('Support the project')}
          </button>
          <div className="my-1 h-px bg-line" />
          <button className="tb-btn w-full !justify-start gap-2 !px-3 text-[13px]" onClick={() => void signOut()}>
            <LogOut className="!h-4 !w-4" />{t('Sign out')}
          </button>
        </div>
      )}
    </div>
  )
}
