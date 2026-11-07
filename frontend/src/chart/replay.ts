import type { KLineData } from 'klinecharts'
import { create } from 'zustand'
import { api } from '../api/client'
import { HistoryLocked, Stale, historyFetch, lockOf, setHistoryLock, untilText } from '../api/history'
import { t } from '../i18n'
import type { Bar, Interval } from '../lib/types'
import { useStore } from '../state/store'
import { live } from './live'
import { INTERVAL_MS, barEnd } from './periods'

/* ------------------------------------------------------------------------------------------------------------
 * Bar replay: the chart shows history up to a chosen moment and then reveals the following bars one at a time
 * (step, or play at 1 / 3 / 10 bars a second). Everything runs in the browser over the normal bars API:
 *  - the chart's data loader asks `replayCut` which bars of the first page it may show (only bars closed by the
 *    replay's "now", `end`); older pages are older than those, so indicators never see the future;
 *  - the bars after the last one shown wait in `buffer` (up to 1000 at a time, fetched ahead) and are handed to
 *    the chart through the same callback as live bars (`replayAttach`), so Heikin Ashi and indicators work unchanged;
 *  - `end` is a moment in time, so changing the symbol or the interval keeps the replay where it was.
 * While a replay runs the chart is not subscribed to live data. Every chart reload (`replayDetach`) drops the
 * buffer and anything in flight; the new series is accepted only after its first page arrived (`replayCut`).
 * ---------------------------------------------------------------------------------------------------------- */

export const REPLAY_SPEEDS = [1, 3, 10] as const   // bars per second
const AHEAD = 1000                                  // bars fetched ahead per request
const LOW_WATER = 200                               // fetch the next page when fewer bars are waiting
const SCAN_WINDOWS = 8                              // windows looked at per request for bars (then ask the user)
const DAY = 86_400_000
const MIN_SPACING: Record<Interval, number> = { ...INTERVAL_MS, '1M': 28 * DAY }

/** The longest window one bars request answers completely: at most AHEAD bars, and inside the server's look-back
 *  for the bars the interval is built from (1m bars: 35 days, other intraday: 120 days, daily: 6 years; the public
 *  server also fetches at most 3000 of them per request). */
function windowOf(iv: Interval): { ms: number; limit: number } {
  const cap = iv === '1M' || iv === '1W' ? 5 * 365 * DAY : iv === '1D' ? AHEAD * DAY : iv.endsWith('h') ? 100 * DAY : 30 * DAY
  const ms = Math.min(AHEAD * MIN_SPACING[iv], cap)
  return { ms, limit: Math.min(AHEAD, Math.ceil(ms / MIN_SPACING[iv])) }
}

interface ReplayState {
  /** Close time of the last bar shown: the replay's "now". null = no replay (live chart). */
  end: number | null
  /** Open time of the last bar shown (what the panel displays). */
  barTs: number | null
  /** Choosing the bar to start from (the chart is live, or still in the previous replay). */
  picking: boolean
  playing: boolean
  speed: number
  /** Nothing after the last bar shown: the replay reached the present (or the end of the data). */
  atEnd: boolean
  /** The next bars are on their way (the buffer ran dry). */
  waiting: boolean
}

export const useReplay = create<ReplayState>(() => ({
  end: null, barTs: null, picking: false, playing: false, speed: 1, atEnd: false, waiting: false,
}))

export const replayActive = () => useReplay.getState().end !== null

const toKLine = (b: Bar): KLineData => ({ timestamp: b[0], open: b[1], high: b[2], low: b[3], close: b[4], volume: b[5] })

// ------------------------------------------------------------------------------------------- engine state
interface Feed { id: string; iv: Interval; push: (k: KLineData) => void }
let feed: Feed | null = null
let series: { id: string; iv: Interval } | null = null   // the series whose first page the chart accepted
let buffer: KLineData[] = []
let cursor = 0              // the buffer continues with the bars after this time (open time of the last bar shown)
let exhausted = false       // no bars after the buffer (the present)
let scanFrom = 0            // no bars after the cursor before this time (a long gap already looked at)
let retryAt = 0
let fetching: Promise<void> | null = null
let gen = 0                 // bumped on every reload: answers for an older chart state are dropped
let stepToken = 0           // bumped on pause / new start / reload: a step waiting for bars gives up
let busyToken = -1          // a step of this token is running (presses meanwhile are ignored)
let timer: number | null = null

function resetEngine() {
  gen++
  stepToken++
  feed = null
  series = null
  buffer = []
  cursor = 0
  exhausted = false
  scanFrom = 0
  retryAt = 0
  fetching = null
  useReplay.setState({ waiting: false })
}

/** The newest `to` the bars API accepts. */
const maxTo = () => Date.now() + DAY

/** `to` for the first page of a replay chart (bars opened before the replay's "now"). */
export function replayTo(): number | null {
  const end = useReplay.getState().end
  return end === null ? null : Math.min(end, maxTo())
}

/** Bars of the first page that may be shown (closed by the replay's "now"); starts the engine for this series. */
export function replayCut(id: string, iv: Interval, bars: Bar[]): Bar[] {
  const end = useReplay.getState().end ?? Infinity
  const shown = bars.filter((b) => barEnd(b[0], iv) <= end)
  const firstHidden = bars.find((b) => barEnd(b[0], iv) > end)
  resetEngine()
  series = { id, iv }
  // the next bar is the first one after the last bar shown; with none shown, the first bar not closed yet (it
  // closes later in the replay), or else anything from `end` on (the page has every bar opened before it)
  const last = shown.at(-1)
  cursor = last ? last[0] : firstHidden ? firstHidden[0] - 1 : Number.isFinite(end) ? end - 1 : 0
  useReplay.setState({ barTs: last ? last[0] : null, atEnd: false })
  return shown
}

/** The chart is ready for replay bars (its data loader's subscribeBar while a replay runs). */
export function replayAttach(f: Feed) {
  // only for the series whose first page arrived (after a failed load nothing is handed out)
  if (!series || series.id !== f.id || series.iv !== f.iv) return
  feed = f
  void fetchAhead()
  schedule()
}

/** The chart reloads (symbol / interval / chart type change, new start, exit) or unmounts. */
export function replayDetach() {
  resetEngine()
}

function fetchAhead(): Promise<void> {
  if (fetching) return fetching
  const s = series
  if (!s || exhausted || Date.now() < retryAt) return Promise.resolve()
  const mine = gen
  const win = windowOf(s.iv)
  const job = (async () => {
    try {
      // A window (from, from + win.ms) holds fewer than win.limit bars and the server answers it completely, so its
      // page has every bar of it. Only bars that end inside the window and are closed are taken: a bar built from
      // smaller ones (2h from 1h, 1W / 1M from 1D) may be cut at the window's end, so it comes with the next window.
      let from = Math.max(buffer.at(-1)?.timestamp ?? cursor, scanFrom)
      for (let i = 0; i < SCAN_WINDOWS; i++) {
        const now = Date.now()
        const to = Math.min(from + win.ms, maxTo())
        // older history may be paced (this waits, one request at a time) or closed (HistoryLocked below)
        const res = await historyFetch(() => api.bars(s.id, s.iv, to, win.limit), () => gen !== mine, false)
        if (gen !== mine) return
        // a page cut at the edge of what may be seen: harmless if only bars before the cursor went, otherwise the
        // replay would skip bars, so it stops here
        const cut = lockOf(res)
        if (cut && (res.floor ?? Infinity) > from) { setHistoryLock(cut); throw new HistoryLocked(cut) }
        const inWin = res.bars.filter((b) => b[0] > from)
        const next = inWin.filter((b) => { const e = barEnd(b[0], s.iv); return e <= now && e <= to })
        if (next.length) { buffer.push(...next.map(toKLine)); return }
        if (inWin.length && barEnd(inWin[0][0], s.iv) <= now) { from = inWin[0][0] - 1; continue }  // across the end
        if (to >= now) { exhausted = true; return }   // only the forming bar (or nothing) up to now
        from = scanFrom = to - 1                       // no bar after the cursor before `to`
      }
      // a long stretch without bars (a market closed for long, history that begins later, a delisted symbol): stop
      // here rather than keep requesting; the next step looks further
      useReplay.setState({ playing: false })
      schedule()
      useStore.setState({ notice: t('No bars for a long stretch after this point. Press → to look further.') })
    } catch (e) {
      if (e instanceof Stale || gen !== mine) return
      retryAt = Date.now() + 3000
      useReplay.setState({ playing: false })   // no retry loop while playing: Play / → try again
      schedule()
      if (e instanceof HistoryLocked) {
        useStore.setState({ notice: e.lock.kind === 'login'
          ? t('Bar replay this far back needs signing in.')
          : t('Bar replay this far back opens again at {time}: the history limit is used up.', { time: untilText(e.lock.until) }) })
        return
      }
      useStore.setState({ error: t('Replay: the next bars could not be loaded ({message}).', { message: (e as Error).message }) })
    }
  })()
  fetching = job
  void job.finally(() => { if (fetching === job) fetching = null })
  return job
}

/** Show the next bar (the → key, the button, the play timer). A press while a bar is still loading is ignored. */
export function replayNext() {
  if (busyToken === stepToken) return
  const mine = (busyToken = stepToken)
  void step(mine).finally(() => { if (busyToken === mine) busyToken = -1 })
}

async function step(token: number): Promise<void> {
  const f = feed
  if (!f) return
  if (!buffer.length) {
    useReplay.setState({ waiting: !exhausted })
    await fetchAhead()
    // paused, restarted or reloaded meanwhile: this step is void
    if (token !== stepToken || feed !== f) return
    useReplay.setState({ waiting: false })
    if (!buffer.length) {
      if (exhausted) { useReplay.setState({ atEnd: true, playing: false }); schedule() }
      return
    }
  }
  const k = buffer.shift()!
  cursor = k.timestamp
  f.push(k)
  useReplay.setState({ end: barEnd(k.timestamp, f.iv), barTs: k.timestamp })
  useStore.getState().setLastBar({ ts: k.timestamp, open: k.open, close: k.close })
  if (buffer.length < LOW_WATER) void fetchAhead()
}

function schedule() {
  if (timer !== null) { window.clearInterval(timer); timer = null }
  const s = useReplay.getState()
  if (!s.playing || s.end === null) return
  timer = window.setInterval(replayNext, 1000 / s.speed)
}

// --------------------------------------------------------------------------------------------- actions
/** Choose the starting bar on the chart (the next click / tap). */
export function replayPick() {
  useStore.getState().setTool(null)
  stepToken++
  useReplay.setState({ picking: true, playing: false, waiting: false })
  schedule()
}

export function replayCancelPick() {
  useReplay.setState({ picking: false })
}

/** Start (or restart) the replay with the bar opened at `ts` as the last one shown. */
export function replayStart(ts: number, iv: Interval) {
  if (barEnd(ts, iv) > Date.now()) return   // the forming bar: nothing could follow it
  stepToken++
  useReplay.setState({ end: barEnd(ts, iv), barTs: ts, picking: false, playing: false, atEnd: false, waiting: false })
  useStore.getState().setLastBar(null)       // the header shows the replay's price once its bars are in
  schedule()
  live.chart?.resetData()
}

/** Back to the live chart. */
export function replayExit() {
  resetEngine()
  useReplay.setState({ end: null, barTs: null, picking: false, playing: false, atEnd: false, waiting: false })
  schedule()
  live.chart?.resetData()
}

export function replayTogglePlay() {
  const s = useReplay.getState()
  if (s.end === null || s.atEnd) return
  if (s.playing) stepToken++   // a step still waiting for bars must not land after the pause
  useReplay.setState(s.playing ? { playing: false, waiting: false } : { playing: true })
  schedule()
}

export function replaySetSpeed(speed: number) {
  useReplay.setState({ speed })
  schedule()
}
