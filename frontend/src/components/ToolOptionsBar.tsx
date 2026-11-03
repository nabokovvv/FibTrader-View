import { TOOLS } from '../chart/overlays'
import { t } from '../i18n'
import { toolStyle, useStore } from '../state/store'
import { ColorPicker, WidthPicker } from './StylePickers'

/** While a tool with remembered style (brush) is armed: pick colour & width for the next strokes. */
export function ToolOptionsBar() {
  const tool = useStore((s) => s.tool)
  const settings = useStore((s) => s.settings)
  const setSettings = useStore((s) => s.setSettings)
  const selected = useStore((s) => s.selectedOverlayId)
  if (!tool || selected) return null
  const st = toolStyle(settings, tool)
  if (!st) return null
  const meta = TOOLS.find((t) => t.name === tool)
  const set = (p: Partial<typeof st>) => setSettings({ toolStyles: { ...(settings.toolStyles ?? {}), [tool]: { ...st, ...p } } })
  return (
    <div className="pointer-events-auto absolute left-1/2 top-2 z-30 flex -translate-x-1/2 items-center gap-1 rounded-lg border border-line bg-panel px-2 py-1 shadow-xl">
      <span className="mr-1 text-[12px] text-muted">{meta ? t(meta.label) : tool}</span>
      <ColorPicker value={st.color} onChange={(color) => set({ color })} />
      <WidthPicker value={st.size} onChange={(size) => set({ size })} />
      <span className="ml-1 hidden text-[11px] text-faint sm:inline">{t('Esc to stop')}</span>
    </div>
  )
}
