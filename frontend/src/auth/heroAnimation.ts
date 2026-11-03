/**
 * Decorative animated chart for the sign-in screen: synthetic BTCUSDT-like candles race in, EMA 9/21 and
 * Bollinger bands follow, and drawing tools (Fib retracement, long position, trend line) appear anchored to bars.
 * The data is generated (labelled "demo"). Returns a stop function.
 */
import { t } from '../i18n'

type Candle = { o: number; h: number; l: number; c: number; vol: number; ticks: number[] }
type Anchor = { i: number; p: number }
type Drawing =
  | { kind: 'fib'; a: Anchor; b: Anchor; t0: number }
  | { kind: 'trend'; a: Anchor; b: Anchor; t0: number }
  | { kind: 'position'; i: number; entry: number; tp: number; sl: number; t0: number }

const COL = {
  bg: '#171a1f', grid: 'rgba(255,255,255,0.045)', axis: '#5c636d', text: '#8b929c', legend: '#d7dbe0',
  up: '#089981', down: '#f23645', ema9: '#ffab2e', ema21: '#f1e9d6', bb: '#c084fc', accent: '#f1e9d6',
}
const FIB: Array<[number, string]> = [[0, '#8b929c'], [0.236, '#f23645'], [0.382, '#ffab2e'], [0.5, '#089981'], [0.618, '#2dd4bf'], [0.786, '#c084fc'], [1, '#8b929c']]
const VISIBLE = 64, FUTURE = 9, CANDLE_MS = 150, TICKS = 7, LIFE = 7200
const FONT = '"IBM Plex Sans", system-ui, sans-serif'

export function startHero(cv: HTMLCanvasElement): () => void {
  const ctx = cv.getContext('2d')
  if (!ctx) return () => {}

  // ---------------------------------------------------------------- market
  let seed = 7
  const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646 }
  const gauss = () => { let u = 0; for (let i = 0; i < 6; i++) u += rnd(); return u - 3 }
  let regime = { drift: 0.0006, vol: 0.0032, left: 40 }
  const nextRegime = () => {
    const r = rnd()
    regime = r < 0.38 ? { drift: 0.0011, vol: 0.0030, left: 25 + rnd() * 30 }
      : r < 0.62 ? { drift: -0.0010, vol: 0.0036, left: 15 + rnd() * 25 }
      : r < 0.85 ? { drift: 0.0000, vol: 0.0022, left: 15 + rnd() * 20 }
      : { drift: 0.0024, vol: 0.0048, left: 6 + rnd() * 8 }
  }
  const makeCandle = (open: number): Candle => {
    if (--regime.left <= 0) nextRegime()
    const ticks = [open]
    let p = open
    const pull = Math.log(76000 / open) * 0.02  // keep the demo price around 76k over long runs
    for (let i = 1; i < TICKS; i++) { p *= 1 + (regime.drift + pull) / TICKS + regime.vol / Math.sqrt(TICKS) * gauss(); ticks.push(p) }
    return { o: open, ticks, vol: (0.6 + rnd()) * (1 + Math.abs(p - open) / open * 400), h: open, l: open, c: open }
  }
  const bars: Candle[] = []
  let price = 76250
  for (let i = 0; i < 180; i++) {
    const b = makeCandle(price); b.h = Math.max(...b.ticks); b.l = Math.min(...b.ticks); b.c = b.ticks[TICKS - 1]; bars.push(b); price = b.c
  }
  let forming = makeCandle(price), formT = 0

  // ------------------------------------------------------------ indicators
  const ema = (n: number) => {
    const k = 2 / (n + 1), out: number[] = []
    let e = bars[0].c
    for (const b of bars) { e = b.c * k + e * (1 - k); out.push(e) }
    out.push(forming.c * k + e * (1 - k))
    return out
  }
  const boll = (n: number, m: number) => {
    const all = bars.map((b) => b.c).concat(forming.c), up: number[] = [], lo: number[] = [], mid: number[] = []
    for (let i = 0; i < all.length; i++) {
      if (i < n - 1) { up.push(NaN); lo.push(NaN); mid.push(NaN); continue }
      let s = 0; for (let j = i - n + 1; j <= i; j++) s += all[j]
      const a = s / n; let v = 0; for (let j = i - n + 1; j <= i; j++) v += (all[j] - a) ** 2
      const sd = Math.sqrt(v / n); mid.push(a); up.push(a + m * sd); lo.push(a - m * sd)
    }
    return { up, lo, mid }
  }

  // -------------------------------------------------------------- drawings
  const drawings: Drawing[] = []
  let nextTool = 1400, toolIdx = 0
  const swing = (from: number, to: number, hi: boolean) => {
    let k = from
    for (let i = from; i <= to; i++) if (hi ? bars[i].h > bars[k].h : bars[i].l < bars[k].l) k = i
    return k
  }
  const spawn = (now: number) => {
    const n = bars.length, kind = (['fib', 'position', 'trend'] as const)[toolIdx++ % 3]
    if (kind === 'fib') {
      const lowI = swing(n - 30, n - 10, false), highI = swing(lowI + 3, n - 1, true)
      if (highI - lowI >= 4) drawings.push({ kind, a: { i: lowI, p: bars[lowI].l }, b: { i: highI, p: bars[highI].h }, t0: now })
    } else if (kind === 'position') {
      const e = bars[n - 1].c
      drawings.push({ kind, i: n - 1, entry: e, tp: e * 1.016, sl: e * 0.9925, t0: now })
    } else {
      const a = swing(n - 36, n - 20, false), b = swing(n - 16, n - 3, false)
      if (b - a >= 6) drawings.push({ kind, a: { i: a, p: bars[a].l }, b: { i: b, p: bars[b].l }, t0: now })
    }
  }

  // ------------------------------------------------------------- rendering
  let W = 0, H = 0, dpr = 1
  const resize = () => {
    dpr = Math.min(window.devicePixelRatio || 1, 2)
    const r = cv.getBoundingClientRect(); W = r.width; H = r.height
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr)
  }
  const ro = new ResizeObserver(resize); ro.observe(cv); resize()

  let yMin = 0, yMax = 0, inited = false, last = 0, raf = 0
  let X = (i: number) => i, Y = (p: number) => p
  const fmt = (v: number) => v.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
  const ease = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : 1 - Math.pow(1 - x, 3))
  const niceStep = (raw: number) => { const p = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / p; return (n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10) * p }
  const hexA = (hex: string, a: number) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})` }
  const font = (px: number, w = 400) => `${w} ${px}px ${FONT}`
  const line = (arr: number[], from: number, color: string, w: number) => {
    ctx.beginPath(); let s = false
    for (let i = from; i < arr.length; i++) { const v = arr[i]; if (Number.isNaN(v)) continue; const x = X(i), y = Y(v); if (s) ctx.lineTo(x, y); else { ctx.moveTo(x, y); s = true } }
    ctx.strokeStyle = color; ctx.lineWidth = w; ctx.lineJoin = 'round'; ctx.stroke()
  }
  const roundRect = (x: number, y: number, w: number, h: number, r: number) => {
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath()
  }
  const handle = (x: number, y: number) => { ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fillStyle = COL.bg; ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = COL.accent; ctx.stroke() }

  const drawTool = (d: Drawing, now: number, plotR: number) => {
    const age = now - d.t0
    const alpha = Math.min(1, age / 200) * (age > LIFE - 700 ? Math.max(0, (LIFE - age) / 700) : 1)
    if (alpha <= 0) return
    ctx.save(); ctx.globalAlpha = alpha
    ctx.font = font(11); ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'left'
    if (d.kind === 'fib') {
      const draw = ease(age / 700)
      const ax = X(d.a.i), ay = Y(d.a.p), bx = X(d.b.i), by = Y(d.b.p)
      let lastLabelY = -1e9
      FIB.forEach(([lv, c], k) => {
        const tt = ease((age - 650 - k * 90) / 300); if (tt <= 0) return
        const p = d.b.p - (d.b.p - d.a.p) * lv, y = Y(p), x1 = ax + (plotR - ax) * tt
        const next = FIB[k + 1]
        if (next) { const y2 = Y(d.b.p - (d.b.p - d.a.p) * next[0]); ctx.fillStyle = hexA(c, 0.06 * tt); ctx.fillRect(ax, Math.min(y, y2), x1 - ax, Math.abs(y2 - y)) }
        ctx.strokeStyle = c; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(ax, Math.round(y) + 0.5); ctx.lineTo(x1, Math.round(y) + 0.5); ctx.stroke()
        if (Math.abs(y - lastLabelY) >= 12) {  // skip labels that would overlap on a small screen
          ctx.fillStyle = c; ctx.fillText(W < 600 ? `${lv}` : `${lv} (${fmt(p)})`, Math.max(ax, Math.min(150, W * 0.18)) + 4, y - 4)
          lastLabelY = y
        }
      })
      ctx.setLineDash([4, 4]); ctx.strokeStyle = 'rgba(241,233,214,0.6)'; ctx.lineWidth = 1
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(ax + (bx - ax) * draw, ay + (by - ay) * draw); ctx.stroke(); ctx.setLineDash([])
      handle(ax, ay); if (draw >= 1) handle(bx, by)
    } else if (d.kind === 'position') {
      const tt = ease(age / 500), x0 = X(d.i), x1 = x0 + 13 * (plotR / (VISIBLE + FUTURE)) * tt
      const ye = Y(d.entry), yt = Y(d.tp), ys = Y(d.sl)
      ctx.fillStyle = 'rgba(8,153,129,0.22)'; ctx.fillRect(x0, yt, x1 - x0, ye - yt)
      ctx.fillStyle = 'rgba(242,54,69,0.22)'; ctx.fillRect(x0, ye, x1 - x0, ys - ye)
      ctx.strokeStyle = 'rgba(241,233,214,0.7)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x0, ye); ctx.lineTo(x1, ye); ctx.stroke()
      if (tt > 0.6) {
        ctx.globalAlpha = alpha * ease((age - 400) / 300)
        ctx.font = font(11, 600)
        ctx.fillStyle = COL.up; ctx.fillText(`${t('Target')} 1.60%`, x0 + 6, yt + 14)
        ctx.fillStyle = COL.down; ctx.fillText(`${t('Stop')} 0.75%`, x0 + 6, ys - 6)
        ctx.font = font(11); ctx.fillStyle = COL.legend; ctx.fillText('R:R 2.13', x0 + 6, ye - 5)
      }
    } else {
      const draw = ease(age / 700), ax = X(d.a.i), ay = Y(d.a.p), bx = X(d.b.i), by = Y(d.b.p)
      const slope = (by - ay) / (bx - ax), ex = ax + (plotR - ax) * draw, ey = ay + slope * (ex - ax)
      ctx.strokeStyle = COL.accent; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(ex, ey); ctx.stroke()
      handle(ax, ay); if (ex >= bx) handle(bx, by)
    }
    ctx.restore()
  }

  const frame = (now: number) => {
    const dt = Math.min(64, now - (last || now)); last = now
    formT += dt
    while (formT >= CANDLE_MS) {
      formT -= CANDLE_MS
      forming.h = Math.max(...forming.ticks); forming.l = Math.min(...forming.ticks); forming.c = forming.ticks[TICKS - 1]
      bars.push(forming); forming = makeCandle(forming.c)
      if (bars.length > 400) {
        bars.shift()
        for (const d of drawings) { if (d.kind === 'position') d.i--; else { d.a.i--; d.b.i-- } }
      }
    }
    const prog = formT / CANDLE_MS
    const seen = forming.ticks.slice(0, 1 + Math.floor(prog * (TICKS - 1)))
    const tip = seen[seen.length - 1] + (forming.ticks[Math.min(TICKS - 1, seen.length)] - seen[seen.length - 1]) * ((prog * (TICKS - 1)) % 1)
    forming.c = tip; forming.h = Math.max(...seen, tip); forming.l = Math.min(...seen, tip)
    if (now > nextTool) { spawn(now); nextTool = now + 2600 }
    for (let i = drawings.length - 1; i >= 0; i--) if (now - drawings[i].t0 > LIFE) drawings.splice(i, 1)

    if (W < 10 || H < 10) { raf = requestAnimationFrame(frame); return }
    const axisW = W < 600 ? 66 : 74, top = 54, volH = H * 0.11, plotB = H - 18, priceB = plotB - volH - 8
    const plotR = W - axisW, slot = plotR / (VISIBLE + FUTURE), f = bars.length + prog
    X = (i: number) => plotR - (f - i + FUTURE) * slot + slot * 0.5
    const first = Math.max(0, Math.floor(f - VISIBLE - FUTURE - 2))
    const e9 = ema(9), e21 = ema(21), bb = boll(20, 2)
    const all = bars.concat(forming)
    let lo = Infinity, hi = -Infinity
    for (let i = first; i < all.length; i++) {
      lo = Math.min(lo, all[i].l, Number.isNaN(bb.lo[i]) ? Infinity : bb.lo[i])
      hi = Math.max(hi, all[i].h, Number.isNaN(bb.up[i]) ? -Infinity : bb.up[i])
    }
    const pad = (hi - lo) * 0.12; lo -= pad; hi += pad
    if (!inited) { yMin = lo; yMax = hi; inited = true }
    yMin += (lo - yMin) * 0.06; yMax += (hi - yMax) * 0.06
    Y = (p: number) => top + (yMax - p) / (yMax - yMin) * (priceB - top)

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.fillStyle = COL.bg; ctx.fillRect(0, 0, W, H)

    const step = niceStep((yMax - yMin) / 6)
    ctx.font = font(11); ctx.textBaseline = 'middle'
    for (let p = Math.ceil(yMin / step) * step; p < yMax; p += step) {
      const y = Y(p); if (y < top - 4 || y > priceB) continue
      ctx.fillStyle = COL.grid; ctx.fillRect(0, Math.round(y), plotR, 1)
      ctx.fillStyle = COL.axis; ctx.textAlign = 'left'; ctx.fillText(fmt(p).replace('.0', ''), plotR + 8, y)
    }
    for (let i = Math.ceil(first / 12) * 12; i < f + FUTURE; i += 12) {
      const x = X(i); if (x > 0 && x < plotR) { ctx.fillStyle = COL.grid; ctx.fillRect(Math.round(x), top - 10, 1, plotB - top + 10) }
    }

    // Bollinger band
    ctx.beginPath()
    let started = false
    for (let i = first; i < all.length; i++) { if (Number.isNaN(bb.up[i])) continue; const x = X(i), y = Y(bb.up[i]); if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true } }
    for (let i = all.length - 1; i >= first; i--) { if (Number.isNaN(bb.lo[i])) continue; ctx.lineTo(X(i), Y(bb.lo[i])) }
    ctx.closePath(); ctx.fillStyle = 'rgba(192,132,252,0.07)'; ctx.fill()
    line(bb.up, first, 'rgba(192,132,252,0.55)', 1); line(bb.lo, first, 'rgba(192,132,252,0.55)', 1)

    // volume
    let vmax = 0; for (let i = first; i < all.length; i++) vmax = Math.max(vmax, all[i].vol)
    for (let i = first; i < all.length; i++) {
      const b = all[i], h = b.vol / vmax * volH, x = X(i)
      ctx.fillStyle = b.c >= b.o ? 'rgba(8,153,129,0.24)' : 'rgba(242,54,69,0.24)'
      ctx.fillRect(x - slot * 0.36, plotB - h, slot * 0.72, h)
    }

    // candles
    const bw = Math.max(2, slot * 0.66)
    for (let i = first; i < all.length; i++) {
      const b = all[i], x = X(i)
      if (x < -slot || x > plotR) continue
      ctx.fillStyle = b.c >= b.o ? COL.up : COL.down
      ctx.fillRect(Math.round(x) - 0.5, Y(b.h), 1, Math.max(1, Y(b.l) - Y(b.h)))
      const yo = Y(b.o), yc = Y(b.c)
      ctx.fillRect(x - bw / 2, Math.min(yo, yc), bw, Math.max(1, Math.abs(yc - yo)))
    }

    line(e21, first, 'rgba(241,233,214,0.85)', 1.5)
    line(e9, first, COL.ema9, 1.5)
    for (const d of drawings) drawTool(d, now, plotR)

    // last price line + label
    const ly = Y(forming.c), upNow = forming.c >= forming.o
    ctx.strokeStyle = upNow ? COL.up : COL.down; ctx.setLineDash([2, 3]); ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(0, Math.round(ly) + 0.5); ctx.lineTo(plotR, Math.round(ly) + 0.5); ctx.stroke(); ctx.setLineDash([])
    ctx.fillStyle = upNow ? COL.up : COL.down
    roundRect(plotR + 2, ly - 10, axisW - 4, 20, 3); ctx.fill()
    ctx.fillStyle = '#fff'; ctx.textAlign = 'left'; ctx.font = font(11, 600); ctx.fillText(fmt(forming.c), plotR + 7, ly + 0.5)

    // fade the left edge into the page
    const fw = Math.min(140, W * 0.18), g = ctx.createLinearGradient(0, 0, fw, 0)
    g.addColorStop(0, 'rgba(23,26,31,1)'); g.addColorStop(1, 'rgba(23,26,31,0)')
    ctx.fillStyle = g; ctx.fillRect(0, 0, fw, H)

    // legend
    ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'
    const lx = W < 600 ? 16 : 28
    ctx.font = font(13, 600); ctx.fillStyle = COL.legend
    const title = `BTCUSDT · ${t('1h')}`
    ctx.fillText(title, lx, 26)
    let x = lx + ctx.measureText(title).width + 10
    ctx.font = font(11); ctx.fillStyle = COL.axis; ctx.fillText(t('demo'), x, 26)
    x = lx
    for (const [k, v] of [[t('O'), forming.o], [t('H'), forming.h], [t('L'), forming.l], [t('C'), forming.c]] as Array<[string, number]>) {
      ctx.fillStyle = COL.text; ctx.fillText(k.trim(), x, 44); x += ctx.measureText(k.trim()).width + 3
      ctx.fillStyle = upNow ? COL.up : COL.down; const s = fmt(v); ctx.fillText(s, x, 44); x += ctx.measureText(s).width + 10
    }
    if (W > 520) {
      x += 6
      for (const [k, v, c] of [['EMA 9', e9[e9.length - 1], COL.ema9], ['EMA 21', e21[e21.length - 1], COL.ema21], ['BB 20 2', bb.mid[bb.mid.length - 1], COL.bb]] as Array<[string, number, string]>) {
        ctx.fillStyle = c; const s = `${k} ${fmt(v)}`; ctx.fillText(s, x, 44); x += ctx.measureText(s).width + 12
      }
    }
    raf = requestAnimationFrame(frame)
  }
  raf = requestAnimationFrame(frame)
  return () => { cancelAnimationFrame(raf); ro.disconnect() }
}
