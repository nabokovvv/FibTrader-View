import { Check, Copy, Download, ImageDown, Link2, Share2 } from 'lucide-react'
import { useState } from 'react'
import { ApiError, api } from '../api/client'
import { chartHolder } from '../chart/ChartView'
import { POSITION_TOOLS, normalizePosition } from '../chart/overlays/position'
import { t } from '../i18n'
import { tickerOf } from '../lib/types'
import { useStore } from '../state/store'
import { Modal } from './ui'

/**
 * Position tools keep the account size and risk % in extendData. A shared link must not reveal them: only their
 * product (the money at risk, which the drawing already shows) is kept, so the shared drawing looks the same.
 */
function publicExtend(name: string, ext: unknown): unknown {
  if (!POSITION_TOOLS.has(name) || !ext || typeof ext !== 'object') return ext ?? undefined
  const c = normalizePosition(ext)
  return { ...(ext as Record<string, unknown>), accountSize: (c.accountSize * c.riskPct) / 100, riskPct: 100 }
}

/** What a shared link restores: symbol, interval, chart type, theme, indicators, visible drawings and range. */
export function snapshotState(): Record<string, unknown> | null {
  const st = useStore.getState()
  const chart = chartHolder.chart
  if (!st.symbol || !chart) return null
  const drawings = chart.getOverlays()
    .filter((o) => o.visible && o.name !== 'measure' && o.points.length)
    .map((o) => ({ name: o.name, points: o.points.map((p) => ({ timestamp: p.timestamp, value: p.value })), styles: o.styles ?? undefined, extendData: publicExtend(o.name, o.extendData) }))
  const list = chart.getDataList()
  const vr = chart.getVisibleRange()
  const to = list[Math.min(list.length - 1, Math.ceil(vr.to) - 1)]?.timestamp
  return {
    v: 1, symbol: st.symbol.id, interval: st.interval, chartType: st.chartType, theme: st.theme,
    indicators: st.indicators.map(({ key, name, main, calcParams, options }) => ({ key, name, main, calcParams, options })),
    drawings, ...(to ? { to } : {}),
  }
}

/** The chart as a PNG with a small caption (symbol · interval · date · site). */
async function chartImage(): Promise<Blob> {
  const chart = chartHolder.chart
  const st = useStore.getState()
  if (!chart || !st.symbol) throw new Error(t('The chart is not ready yet.'))
  const css = getComputedStyle(document.documentElement)
  const bg = css.getPropertyValue('--bg').trim() || '#14171c'
  const url = chart.getConvertPictureUrl(true, 'png', bg)
  const img = new Image()
  await new Promise<void>((res, rej) => { img.onload = () => res(); img.onerror = () => rej(new Error('image')); img.src = url })
  const pad = 28 * (window.devicePixelRatio || 1)
  const c = document.createElement('canvas')
  c.width = img.width
  c.height = img.height + pad
  const ctx = c.getContext('2d')!
  ctx.fillStyle = bg
  ctx.fillRect(0, 0, c.width, c.height)
  ctx.drawImage(img, 0, 0)
  ctx.fillStyle = css.getPropertyValue('--muted').trim() || '#8b929c'
  ctx.font = `${Math.round(pad * 0.45)}px system-ui, sans-serif`
  ctx.textBaseline = 'middle'
  const caption = `${tickerOf(st.symbol)} · ${t(st.interval)} · ${new Date().toISOString().slice(0, 16).replace('T', ' ')} UTC`
  ctx.fillText(caption, pad * 0.4, img.height + pad / 2)
  const site = 'FibTrader View · charts.fibtrader.dev'
  ctx.fillText(site, c.width - ctx.measureText(site).width - pad * 0.4, img.height + pad / 2)
  return await new Promise<Blob>((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error('image'))), 'image/png'))
}

export function ShareModal({ onClose }: { onClose: () => void }) {
  const symbol = useStore((s) => s.symbol)
  const interval = useStore((s) => s.interval)
  const desktop = window.innerWidth >= 760 && window.matchMedia('(pointer: fine)').matches
  const [link, setLink] = useState<string | null>(null)
  const [copied, setCopied] = useState<'link' | 'image' | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const flash = (what: 'link' | 'image') => { setCopied(what); window.setTimeout(() => setCopied(null), 2000) }

  const download = async () => {
    setErr(null)
    try {
      const blob = await chartImage()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `${symbol ? tickerOf(symbol) : 'chart'}-${interval}.png`
      a.click()
      window.setTimeout(() => URL.revokeObjectURL(a.href), 5000)
    } catch (e) { setErr((e as Error).message) }
  }
  const copyImage = async () => {
    setErr(null)
    try {
      // the promise goes straight into ClipboardItem: Safari needs the write to start inside the click
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': chartImage() })])
      flash('image')
    } catch { setErr(t('This browser cannot copy images. Use “Download image”.')) }
  }
  const makeLink = async () => {
    setErr(null)
    const state = snapshotState()
    if (!state) return
    setBusy(true)
    try {
      const { id } = await api.createShare(state)
      const url = `${window.location.origin}/s/${id}`
      setLink(url)
      try { await navigator.clipboard.writeText(url); flash('link') } catch { /* shown in the field */ }
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 413 ? t('Too many drawings to share in one link.') : (e as Error).message)
    } finally { setBusy(false) }
  }
  const nativeShare = () => { if (link) void navigator.share?.({ url: link, title: `${symbol ? tickerOf(symbol) : ''} · FibTrader View` }).catch(() => { /* cancelled */ }) }

  return (
    <Modal title={t('Share chart')} onClose={onClose} width={480}>
      <div className="flex flex-col gap-3 p-4">
        {desktop && (
          <div className="flex flex-wrap gap-2">
            <button className="tb-btn !h-9 !px-3 text-[13px]" style={{ background: 'var(--raised)' }} onClick={() => void download()}><Download />{t('Download image')}</button>
            <button className="tb-btn !h-9 !px-3 text-[13px]" style={{ background: 'var(--raised)' }} onClick={() => void copyImage()}>
              {copied === 'image' ? <Check style={{ color: 'var(--up)' }} /> : <ImageDown />}{copied === 'image' ? t('Image copied') : t('Copy image')}
            </button>
          </div>
        )}
        <div className="rounded-lg border border-line p-3">
          <div className="mb-1 text-[13px] font-semibold">{t('Link to this chart')}</div>
          <p className="mb-2 text-[12px] text-muted">{t('Opens read-only with your drawings and indicators, no sign-in needed. Works for 90 days.')}</p>
          {link ? (
            <div className="flex items-center gap-2">
              <input readOnly value={link} className="h-8 min-w-0 flex-1 rounded-md border border-line bg-bg px-2 text-[12px]" onFocus={(e) => e.currentTarget.select()} />
              <button className="tb-btn" title={t('Copy link')} onClick={() => void navigator.clipboard?.writeText(link).then(() => flash('link'))}>
                {copied === 'link' ? <Check style={{ color: 'var(--up)' }} /> : <Copy />}
              </button>
              {'share' in navigator && <button className="tb-btn" title={t('Share…')} onClick={nativeShare}><Share2 /></button>}
            </div>
          ) : (
            <button className="tb-btn !h-9 !px-3 text-[13px]" style={{ background: 'var(--brand)', color: '#fff' }} disabled={busy} onClick={() => void makeLink()}>
              <Link2 />{t('Create link')}
            </button>
          )}
          {copied === 'link' && <p className="mt-1 text-[11px]" style={{ color: 'var(--up)' }}>{t('Link copied')}</p>}
        </div>
        {err && <p className="text-[12px]" style={{ color: 'var(--down)' }}>{err}</p>}
      </div>
    </Modal>
  )
}
