import { ChevronDown } from 'lucide-react'
import { t } from '../i18n'
import { useEffect, useRef, useState, type ReactNode } from 'react'

export const PALETTE = [
  '#f1e9d6', '#ffab2e', '#f23645', '#089981', '#2962ff',
  '#c084fc', '#2dd4bf', '#f472b6', '#a3e635', '#8b929c',
]
export const WIDTHS = [1, 2, 3, 4, 6, 8]

function Popover({ button, children, label }: { button: ReactNode; children: (close: () => void) => ReactNode; label: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent | TouchEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('touchstart', onDoc)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('touchstart', onDoc) }
  }, [open])
  return (
    <div ref={ref} className="relative">
      <button className="tb-btn !min-w-0 !gap-1 !px-1.5" aria-label={label} aria-expanded={open} title={label} onClick={() => setOpen((o) => !o)}>
        {button}
        <ChevronDown className="!h-3 !w-3 text-faint" />
      </button>
      {open && (
        <div className="absolute left-1/2 top-full z-40 mt-1.5 -translate-x-1/2 rounded-lg border border-line bg-panel p-2 shadow-xl">
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  )
}

/** Colour button → palette + custom colour. */
export function ColorPicker({ value, onChange }: { value: string; onChange: (c: string) => void }) {
  return (
    <Popover label={t('Colour')} button={<span className="block h-4 w-4 rounded-full border border-line" style={{ background: value }} />}>
      {(close) => (
        <div className="w-[176px]">
          <div className="grid grid-cols-5 gap-1.5">
            {PALETTE.map((c) => (
              <button
                key={c} className="h-7 w-7 rounded-full border-2" aria-label={t('Colour {c}', { c })}
                style={{ background: c, borderColor: c.toLowerCase() === value.toLowerCase() ? 'var(--text)' : 'transparent' }}
                onClick={() => { onChange(c); close() }}
              />
            ))}
          </div>
          <label className="mt-2 flex cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-[12px] text-muted hover:bg-raised">
            <span className="relative h-6 w-6 shrink-0 overflow-hidden rounded-full border border-line"
              style={{ background: 'conic-gradient(#f23645, #ffab2e, #a3e635, #2dd4bf, #2962ff, #c084fc, #f23645)' }}>
              <input type="color" value={value} onChange={(e) => onChange(e.target.value)} className="absolute inset-0 cursor-pointer opacity-0" aria-label={t('Custom colour…')} />
            </span>
            {t('Custom colour…')}
          </label>
        </div>
      )}
    </Popover>
  )
}

/** Width button → list of widths drawn at their real thickness. */
export function WidthPicker({ value, onChange, widths = WIDTHS }: { value: number; onChange: (w: number) => void; widths?: number[] }) {
  return (
    <Popover label={t('Line width')} button={<span className="flex items-center gap-1 text-[12px]"><span className="block w-4 rounded-full bg-current" style={{ height: Math.min(value, 6) }} />{value}px</span>}>
      {(close) => (
        <div className="grid w-[132px] gap-0.5">
          {widths.map((w) => (
            <button key={w} className="tb-btn w-full !justify-start gap-3 !px-2" data-active={w === value} onClick={() => { onChange(w); close() }}>
              <span className="block w-14 rounded-full bg-current" style={{ height: w }} />
              <span className="text-[12px]">{w}px</span>
            </button>
          ))}
        </div>
      )}
    </Popover>
  )
}
