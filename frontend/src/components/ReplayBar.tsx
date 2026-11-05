import { Pause, Play, SkipBack, StepForward, X } from 'lucide-react'
import { useEffect, useReducer, useState } from 'react'
import { getLang, t } from '../i18n'
import { live } from '../chart/live'
import { barEnd } from '../chart/periods'
import {
  REPLAY_SPEEDS, replayCancelPick, replayExit, replayNext, replayPick, replaySetSpeed, replayStart,
  replayTogglePlay, useReplay,
} from '../chart/replay'
import type { Interval } from '../lib/types'
import { useStore } from '../state/store'

/** Bar replay controls (bottom of the chart) and, while choosing the start, the marker on the chart. */
export function ReplayBar() {
  const end = useReplay((s) => s.end)
  const picking = useReplay((s) => s.picking)
  const active = end !== null
  // keyboard: → next bar, Esc stops choosing the start bar
  useEffect(() => {
    if (!active && !picking) return
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)) return
      if (e.key === 'Escape' && useReplay.getState().picking) replayCancelPick()
      else if (e.key === 'ArrowRight' && !e.shiftKey && !e.altKey && !e.metaKey && !e.ctrlKey && active && !useReplay.getState().picking) {
        e.preventDefault()
        replayNext()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, picking])

  if (!active && !picking) return null
  return picking ? <Picker /> : <Controls />
}

const panel = 'pointer-events-auto absolute bottom-10 left-1/2 z-30 flex max-w-[calc(100%-16px)] -translate-x-1/2 items-center gap-0.5 rounded-lg border border-line bg-panel p-1 shadow-xl'

function Controls() {
  const playing = useReplay((s) => s.playing)
  const speed = useReplay((s) => s.speed)
  const atEnd = useReplay((s) => s.atEnd)
  const waiting = useReplay((s) => s.waiting)
  const barTs = useReplay((s) => s.barTs)
  const interval = useStore((s) => s.interval)
  const timezone = useStore((s) => s.timezone)
  const nextSpeed = REPLAY_SPEEDS[(REPLAY_SPEEDS.indexOf(speed as (typeof REPLAY_SPEEDS)[number]) + 1) % REPLAY_SPEEDS.length]
  return (
    <div className={panel} role="toolbar" aria-label={t('Bar replay')}>
      <button className="tb-btn" onClick={replayPick} title={t('Choose another start bar')} aria-label={t('Choose another start bar')}><SkipBack /></button>
      {atEnd ? (
        <span className="px-2 text-[12px] text-muted">{t('Replay reached the present')}</span>
      ) : (
        <>
          <button className="tb-btn" data-active={playing} onClick={replayTogglePlay} title={t(playing ? 'Pause' : 'Play')} aria-label={t(playing ? 'Pause' : 'Play')}>
            {playing ? <Pause /> : <Play />}
          </button>
          <button className="tb-btn" onClick={replayNext} title={t('Next bar (→)')} aria-label={t('Next bar (→)')}><StepForward /></button>
          <button className="tb-btn !min-w-10 text-[12px] font-semibold" onClick={() => replaySetSpeed(nextSpeed)}
            title={t('Speed: {n} bars per second', { n: speed })} aria-label={t('Speed: {n} bars per second', { n: speed })}>{speed}×</button>
        </>
      )}
      <span className="min-w-0 truncate px-1.5 text-[12px] tabular-nums text-text" title={t('Last bar shown')}>
        {barTs !== null ? fmtWhen(barTs, interval, timezone) : '—'}{waiting ? ' …' : ''}
      </span>
      <button className="tb-btn" onClick={replayExit} title={t('Exit replay (back to live)')} aria-label={t('Exit replay (back to live)')}>
        <X />{atEnd && <span className="text-[12px]">{t('Back to live')}</span>}
      </button>
    </div>
  )
}

function fmtWhen(ts: number, iv: Interval, timeZone: string): string {
  const daily = iv === '1D' || iv === '1W' || iv === '1M'
  const opts: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'short', year: 'numeric', ...(daily ? {} : { hour: '2-digit', minute: '2-digit' }) }
  const locale = getLang() === 'ru' ? 'ru-RU' : 'en-GB'
  try { return new Intl.DateTimeFormat(locale, { ...opts, timeZone }).format(ts) } catch { return new Intl.DateTimeFormat(locale, opts).format(ts) }
}

interface Spot { ts: number; x: number; right: number; bottom: number }

/** The bar under the pointer (snapped; never the forming bar, which nothing could follow). */
function spotAt(clientX: number): Spot | null {
  const chart = live.chart, el = live.el
  const data = chart?.getDataList()
  if (!chart || !el || !data?.length) return null
  const iv = useStore.getState().interval
  let maxI = data.length - 1
  while (maxI >= 0 && barEnd(data[maxI].timestamp, iv) > Date.now()) maxI--
  if (maxI < 0) return null   // no closed bar yet
  const box = el.getBoundingClientRect()
  const p = chart.convertFromPixel([{ x: clientX - box.left, y: 0 }], { paneId: 'candle_pane' }) as Array<{ dataIndex?: number }>
  const i = p[0]?.dataIndex
  if (i == null || !Number.isFinite(i)) return null
  return spotOf(data[Math.max(0, Math.min(Math.round(i), maxI))].timestamp)
}

/** Where the marker after the bar opened at `ts` goes (the chart may have scrolled since it was chosen). */
function spotOf(ts: number): Spot | null {
  const chart = live.chart, el = live.el
  if (!chart || !el) return null
  const x = (chart.convertToPixel({ timestamp: ts }, { paneId: 'candle_pane' }) as { x?: number }).x
  if (x == null || !Number.isFinite(x)) return null
  const main = chart.getSize('candle_pane', 'main')
  const axis = chart.getSize('x_axis_pane')
  return {
    ts,
    x: Math.round(x + chart.getBarSpace().bar / 2),
    right: main ? main.left + main.width : el.clientWidth,
    bottom: axis ? axis.top : el.clientHeight - 28,
  }
}

function Picker() {
  const wasActive = useReplay((s) => s.end !== null)
  const interval = useStore((s) => s.interval)
  const timezone = useStore((s) => s.timezone)
  const [hover, setHover] = useState<Spot | null>(null)     // mouse: the bar under the pointer
  const [chosen, setChosen] = useState<number | null>(null) // touch: the tapped bar, waiting for "Start"
  const [, redraw] = useReducer((n: number) => n + 1, 0)
  // picking a drawing tool ends the choice (a click would otherwise both draw and start the replay)
  const tool = useStore((s) => s.tool)
  useEffect(() => { if (tool) replayCancelPick() }, [tool])

  useEffect(() => {
    const el = live.el, chart = live.chart
    if (!el || !chart) return
    let down: { x: number; y: number; at: number } | null = null
    const onMove = (e: PointerEvent) => { if (e.pointerType === 'mouse') setHover(spotAt(e.clientX)) }
    const onLeave = (e: PointerEvent) => { if (e.pointerType === 'mouse') setHover(null) }
    const onDown = (e: PointerEvent) => { down = e.button === 0 ? { x: e.clientX, y: e.clientY, at: Date.now() } : null }
    const onUp = (e: PointerEvent) => {
      const d = down
      down = null
      // a click / tap, not the end of a pan or a long press
      if (!d || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 6 || Date.now() - d.at > 600) return
      const s = spotAt(e.clientX)
      if (!s) return
      if (e.pointerType === 'mouse') replayStart(s.ts, useStore.getState().interval)
      else setChosen(s.ts)
    }
    // capture: the chart handles these events itself; we only watch
    el.addEventListener('pointermove', onMove, true)
    el.addEventListener('pointerleave', onLeave, true)
    el.addEventListener('pointerdown', onDown, true)
    el.addEventListener('pointerup', onUp, true)
    // the marker follows the chart when it is panned or zoomed
    const onRange = () => redraw()
    chart.subscribeAction('onVisibleRangeChange', onRange)
    return () => {
      el.removeEventListener('pointermove', onMove, true)
      el.removeEventListener('pointerleave', onLeave, true)
      el.removeEventListener('pointerdown', onDown, true)
      el.removeEventListener('pointerup', onUp, true)
      chart.unsubscribeAction('onVisibleRangeChange', onRange)
    }
  }, [])

  const spot = chosen !== null ? spotOf(chosen) : hover
  const coarse = typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches
  return (
    <>
      {spot && spot.x < spot.right && (
        <>
          <div className="pointer-events-none absolute top-0 z-10" style={{ left: spot.x, width: Math.max(0, spot.right - spot.x), height: spot.bottom, background: 'color-mix(in srgb, var(--bg) 62%, transparent)' }} />
          <div className="pointer-events-none absolute top-0 z-10" style={{ left: spot.x, height: spot.bottom, borderLeft: '2px solid var(--brand)' }} />
        </>
      )}
      <div className={panel} role="toolbar" aria-label={t('Bar replay')}>
        {chosen !== null ? (
          <button className="tb-btn !px-3 text-[12px] font-semibold text-text" onClick={() => replayStart(chosen, interval)}>
            <Play />{t('Start from {when}', { when: fmtWhen(chosen, interval, timezone) })}
          </button>
        ) : (
          <span className="px-2 text-[12px] text-muted">{t(coarse ? 'Tap the bar to start the replay from' : 'Click the bar to start the replay from')}</span>
        )}
        <button className="tb-btn text-[12px]" onClick={replayCancelPick}>{t(wasActive ? 'Back to the replay' : 'Cancel')}</button>
      </div>
    </>
  )
}
