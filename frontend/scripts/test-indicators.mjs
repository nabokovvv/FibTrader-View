#!/usr/bin/env node
/**
 * Checks of the pure indicator calculations (src/chart/indicators/calc.ts) on hand-made bars: values, edge cases,
 * calendar edges, daylight saving, and that no level depends on bars after the one it is drawn on (bar replay).
 * calc.ts is bundled with the rolldown Vite already ships, so this runs on any Node the build runs on.
 *
 *   node scripts/test-indicators.mjs      # exit 1 and a list when something is wrong
 */
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { rolldown } from 'rolldown'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'chart', 'indicators', 'calc.ts')
const bundle = await rolldown({ input: SRC, logLevel: 'silent' })
const { output } = await bundle.generate({ format: 'esm' })
const C = await import(`data:text/javascript;base64,${Buffer.from(output[0].code).toString('base64')}`)

const failures = []
let checks = 0
function ok(cond, what) { checks++; if (!cond) failures.push(what) }
const near = (a, b, eps = 1e-9) => a !== undefined && b !== undefined && Math.abs(a - b) <= eps
const H = 3_600_000
const D = 24 * H
const utc = (s) => Date.parse(`${s}Z`)
const bar = (t, o, h, l, c, v = 1) => ({ timestamp: t, open: o, high: h, low: l, close: c, volume: v })

// --------------------------------------------------------------------------------------------- oscillators
{
  const rising = Array.from({ length: 30 }, (_, i) => 100 + i)
  const rsi = C.rsiSeries(rising, 14)
  ok(rsi[13] === undefined && rsi[14] === 100, 'RSI: undefined before 14 changes, 100 on a steady rise')
  const zigzag = Array.from({ length: 40 }, (_, i) => (i % 2 ? 101 : 100))
  const zr = C.rsiSeries(zigzag, 14)
  ok(zr[39] > 50 && zr[39] < 56 && zr[38] < 50 && zr[38] > 44, 'RSI: around 50 on equal ups and downs (a bit above after an up)')

  const bars = Array.from({ length: 80 }, (_, i) => bar(i * H, 100 + Math.sin(i / 3) * 5, 106 + Math.sin(i / 3) * 5, 94 + Math.sin(i / 3) * 5, 100 + Math.sin(i / 3 + 0.4) * 5, 10 + i))
  const sr = C.stochRsi(bars, 14, 14, 3, 3)
  const firstK = sr.findIndex((x) => x.k !== undefined)
  ok(firstK === 14 + 13 + 2, `Stoch RSI: first %K after RSI 14 + stoch 14 + smoothing 3 (got index ${firstK})`)
  ok(sr.every((x) => x.k === undefined || (x.k >= 0 && x.k <= 100)), 'Stoch RSI: %K within 0…100')
  ok(sr.every((x, i) => x.d === undefined || near(x.d, (sr[i].k + sr[i - 1].k + sr[i - 2].k) / 3, 1e-9)), 'Stoch RSI: %D = 3-bar average of %K')

  const up = Array.from({ length: 20 }, (_, i) => bar(i * H, 100 + i, 101 + i, 99 + i, 100 + i, 5))
  const down = Array.from({ length: 20 }, (_, i) => bar(i * H, 100 - i, 101 - i, 99 - i, 100 - i, 5))
  ok(C.mfi(up, 14)[13] === undefined && C.mfi(up, 14)[14] === 100, 'MFI: 100 when the typical price only rises')
  ok(C.mfi(down, 14)[19] === 0, 'MFI: 0 when it only falls')
  ok(C.mfi(up.map((b) => ({ ...b, volume: 0 })), 14).every((v) => v === undefined), 'MFI: nothing without volume')
  // hand-checked: 3 bars, length 2 → flows +tp2*v2 (rise) and −tp3*v3 (fall)
  const m = C.mfi([bar(0, 10, 11, 9, 10, 1), bar(1, 10, 12, 10, 11, 2), bar(2, 11, 11, 9, 10, 3)], 2)
  ok(near(m[2], 100 - 100 / (1 + (11 * 2) / (10 * 3))), 'MFI: hand-checked value')

  const atHigh = Array.from({ length: 25 }, (_, i) => bar(i * H, 100, 110, 90, 110, 7))
  const atLow = atHigh.map((b) => ({ ...b, close: 90 }))
  ok(C.cmf(atHigh, 20)[18] === undefined && near(C.cmf(atHigh, 20)[19], 1), 'CMF: +1 when every close is at the high')
  ok(near(C.cmf(atLow, 20)[24], -1), 'CMF: −1 when every close is at the low')
  ok(C.cmf(atHigh.map((b) => ({ ...b, volume: 0 })), 20).every((v) => v === undefined), 'CMF: nothing without volume')
}

// ------------------------------------------------------------------------------------------ volume profile
{
  // a wide bar spreads 60 over 10…20; a narrow rising bar puts 100 into one row near 15
  const p = C.volumeProfile([bar(0, 20, 20, 10, 10, 60), bar(1, 15, 15.2, 15.1, 15.2, 100)], 10)
  ok(p && p.rows === 10 && near(p.lo, 10) && near(p.hi, 20), 'profile: range of the bars, 10 rows')
  const sum = p.total.reduce((s, v) => s + v, 0)
  ok(near(sum, 160, 1e-6), `profile: all volume is placed (got ${sum})`)
  ok(p.poc === 5 && p.up[5] === 100 && p.dn[5] === 6, 'profile: POC in the narrow bar\'s row; rising and falling split')
  ok(p.vaLo <= p.poc && p.poc <= p.vaHi, 'profile: value area holds the POC')
  const va = p.total.slice(p.vaLo, p.vaHi + 1).reduce((s, v) => s + v, 0)
  ok(va >= 0.7 * sum, 'profile: value area holds at least 70 %')
  // the value area grows towards the bigger neighbour: 60 in row 5, 50 over rows 5…9, 4 over rows 0…1
  const q = C.volumeProfile([bar(0, 15.2, 15.4, 15.1, 15.3, 60), bar(1, 15.1, 19.9, 15.1, 19.9, 50), bar(2, 11.9, 11.9, 10, 10, 4)], 10)
  ok(q && q.poc === 5 && q.vaLo === 5 && q.vaHi === 6, `profile: value area grows towards the fuller side (got ${q && [q.poc, q.vaLo, q.vaHi]})`)
  ok(C.volumeProfile([], 24) === null && C.volumeProfile([bar(0, 1, 1, 1, 1, 5)], 24) === null, 'profile: nothing for no bars or a flat range')
}

// ------------------------------------------------------------------------- previous day / week / month
{
  // UTC daily bars 2026-09-28 (Mon) … 2026-11-06 (Fri); high = 1000 + day number, low = −that
  const start = utc('2026-09-28T00:00:00')
  const daily = Array.from({ length: 40 }, (_, i) => ({ timestamp: start + i * D, high: 1000 + i, low: -(1000 + i) }))
  const dayNo = (s) => Math.round((utc(`${s}T00:00:00`) - start) / D)
  const t = utc('2026-11-04T12:00:00')            // Wednesday
  const [lv] = C.periodLevels([t], daily, 'UTC', false)
  ok(lv.pdh === 1000 + dayNo('2026-11-03') && lv.pdl === -(1000 + dayNo('2026-11-03')), 'PDH/PDL: the day before')
  ok(lv.pwh === 1000 + dayNo('2026-11-01'), 'PWH: the last day of the previous week (Mon 26 Oct … Sun 1 Nov)')
  ok(lv.pwl === -(1000 + dayNo('2026-11-01')), 'PWL: the previous week')
  ok(lv.pmh === 1000 + dayNo('2026-10-31') && lv.pml === -(1000 + dayNo('2026-10-31')), 'PMH/PML: October')
  // the first week and month in the list may be cut: September starts the list on the 28th
  const [early] = C.periodLevels([utc('2026-10-02T12:00:00')], daily, 'UTC', false)
  ok(early.pmh === undefined, 'PMH: a month the daily bars only partly cover is not used')
  const [first] = C.periodLevels([utc('2026-10-06T12:00:00')], daily.slice(2), 'UTC', false)  // list starts on Wed 30 Sep
  ok(first.pwh === undefined, 'PWH: a week the daily bars only partly cover is not used')
  const [whole] = C.periodLevels([utc('2026-10-06T12:00:00')], daily.slice(2), 'UTC', true)
  ok(whole.pwh === 1000 + dayNo('2026-10-04'), 'PWH: … unless nothing older exists (a new listing)')
  ok(Object.keys(C.periodLevels([start - D], daily, 'UTC')[0]).length === 0, 'levels: nothing before the daily bars')
  // no look-ahead: a bar's levels do not change when later days arrive
  const times = Array.from({ length: 24 * 30 }, (_, i) => start + 7 * D + i * H)
  const full = C.periodLevels(times, daily, 'UTC', true)
  const same = times.every((tt, i) => {
    const upTo = daily.filter((b) => b.timestamp <= tt)
    return JSON.stringify(C.periodLevels([tt], upTo, 'UTC', true)[0]) === JSON.stringify(full[i])
  })
  ok(same, 'levels: the same with only the daily bars up to the bar (bar replay)')
  // daily bars are labelled 00:00 UTC of the exchange's date (markets/_util.py); intraday bars keep real UTC times
  const days = (from, n) => Array.from({ length: n }, (_, i) => ({ timestamp: utc(`${from}T00:00:00`) + i * D, high: 10 + i, low: -i }))
  const msk = days('2026-11-02', 10)                       // Mon 2 Nov …
  const [m] = C.periodLevels([utc('2026-11-04T22:00:00')], msk, 'Europe/Moscow', true)    // = Thu 5 Nov 01:00 Moscow
  ok(m.pdh === 10 + 2, `PDH: the exchange's own calendar date (Moscow; got ${m.pdh})`)
  const syd = days('2026-11-02', 10)
  const [s1] = C.periodLevels([utc('2026-11-02T23:00:00')], syd, 'Australia/Sydney', true)   // = Tue 3 Nov 10:00 Sydney
  ok(s1.pdh === 10, `PDH: a session that opens the previous UTC day (Sydney; got ${s1.pdh})`)
  const ny = days('2026-11-02', 5)                          // Mon … Fri, no weekend bars
  const [n1] = C.periodLevels([utc('2026-11-09T15:00:00')], ny, 'America/New_York', true)   // next Monday
  ok(n1.pdh === 14 && n1.pwh === 14, 'PDH/PWH on Monday: Friday and the week before (weekend without bars)')
  ok(C.levelsNeedFrom(utc('2026-11-30T12:00:00'), 'UTC') === utc('2026-10-01T00:00:00'), 'levels: the oldest bar needs the whole previous month')
  ok(C.levelsNeedFrom(utc('2026-03-01T02:00:00'), 'America/New_York') === utc('2026-01-01T00:00:00'), 'levels: the previous month by the exchange date (28 Feb in New York)')
}

// -------------------------------------------------------------------------------------- trading sessions
{
  const L = [{ tz: 'Europe/London', from: 8, to: 16.5 }]
  const inLondon = (s) => C.sessionStarts([utc(s)], L)[0][0] >= 0
  ok(inLondon('2026-07-01T07:00:00') && !inLondon('2026-07-01T06:30:00'), 'sessions: London opens 07:00 UTC in summer')
  ok(inLondon('2026-01-15T08:00:00') && !inLondon('2026-01-15T07:30:00'), 'sessions: London opens 08:00 UTC in winter')
  ok(inLondon('2026-01-15T16:00:00') && !inLondon('2026-01-15T16:30:00'), 'sessions: closes at 16:30 (exclusive)')
  const NY = [{ tz: 'America/New_York', from: 9.5, to: 16 }]
  const inNY = (s) => C.sessionStarts([utc(s)], NY)[0][0] >= 0
  ok(inNY('2026-07-01T13:30:00') && !inNY('2026-07-01T13:00:00') && inNY('2026-01-15T14:30:00') && !inNY('2026-01-15T14:00:00'),
    'sessions: New York 09:30 local in summer and winter')
  // one session = one start index; the next day is another session
  const hours = Array.from({ length: 48 }, (_, i) => utc('2026-07-01T00:00:00') + i * H)
  const [col] = C.sessionStarts(hours, L)
  ok(col[7] === 7 && col[15] === 7 && col[16] === -1 && col[31] === 31, 'sessions: bars of one session share its first bar')
  const wrap = C.sessionStarts([utc('2026-07-01T23:00:00'), utc('2026-07-02T01:00:00'), utc('2026-07-02T03:00:00')], [{ tz: 'UTC', from: 22, to: 2 }])[0]
  ok(wrap[0] === 0 && wrap[1] === 0 && wrap[2] === -1, 'sessions: a session over midnight stays one session')
  ok(C.sessionStarts(hours, [{ tz: 'UTC', from: 9, to: 9 }])[0].every((x) => x === -1), 'sessions: from = to switches it off')
  ok(C.sessionStarts(hours, [{ tz: 'UTC', from: NaN, to: 30 }])[0].every((x) => x === -1), 'sessions: bad hours switch it off')
}

// ------------------------------------------------------------------------------------ SuperTrend / AlphaTrend
{
  // a rise, a fall, a rise again: both indicators must turn with it
  const path = [...Array.from({ length: 60 }, (_, i) => 100 + i), ...Array.from({ length: 60 }, (_, i) => 160 - i * 1.2), ...Array.from({ length: 60 }, (_, i) => 88 + i)]
  const bars = path.map((c, i) => bar(i * H, c - 0.3, c + 1 + (i % 3) * 0.2, c - 1 - (i % 2) * 0.3, c, 100 + (i % 7)))
  const st = C.superTrend(bars, 10, 3)
  const flips = st.map((r, i) => (i > 0 && st[i - 1].dir !== undefined && r.dir !== st[i - 1].dir ? (r.dir > 0 ? 1 : -1) : undefined))
  ok(st.every((r, i) => r.signal === flips[i]), 'SuperTrend: a signal exactly where the direction turns')
  const stSig = st.map((r, i) => [i, r.signal]).filter(([, x]) => x)
  ok(stSig.length >= 2 && stSig.some(([, x]) => x === -1) && stSig.some(([, x]) => x === 1), `SuperTrend: turns down and up again (${JSON.stringify(stSig)})`)

  const at = C.alphaTrend(bars, 1, 14)
  const firstAt = at.findIndex((r) => r.at !== undefined)
  ok(firstAt === 14, `AlphaTrend: starts once ATR and MFI (14) exist (got index ${firstAt})`)
  // the line never steps back while the money flow keeps its side: in the steady rise it does not fall
  const rise = at.slice(20, 58).map((r) => r.at)
  ok(rise.every((v, i) => i === 0 || v >= rise[i - 1]), 'AlphaTrend: does not step back during a rise')
  ok(at.slice(20, 58).every((r) => r.at < bars[at.indexOf(r)].low), 'AlphaTrend: below the bars in a rise')
  const sig = at.map((r, i) => [i, r.signal]).filter(([, x]) => x)
  ok(sig.length >= 2 && sig.every(([, x], k) => k === 0 || x !== sig[k - 1][1]), `AlphaTrend: buy and sell alternate (${JSON.stringify(sig)})`)
  ok(sig.some(([i, x]) => x === -1 && i > 60 && i < 120) && sig.some(([i, x]) => x === 1 && i > 120), 'AlphaTrend: a sell in the fall, a buy in the next rise')
  // without volume RSI takes over (same turns on this path)
  const noVol = C.alphaTrend(bars.map((b) => ({ ...b, volume: 0 })), 1, 14)
  ok(noVol.some((r) => r.signal === -1) && noVol.some((r) => r.signal === 1), 'AlphaTrend: works without volume (RSI)')
  // no look-ahead: values and signals of a bar do not change when later bars arrive (bar replay)
  const prefix = (fn) => [70, 100, 130, 170].every((n) => JSON.stringify(fn(bars.slice(0, n))) === JSON.stringify(fn(bars).slice(0, n)))
  ok(prefix((b) => C.alphaTrend(b, 1, 14)), 'AlphaTrend: no look-ahead')
  ok(prefix((b) => C.superTrend(b, 10, 3)), 'SuperTrend: no look-ahead')
}

// ------------------------------------------------------- reference implementations (independent of calc.ts)
{
  // SuperTrend exactly as it was in indicators/index.ts before the move to calc.ts (Wilder ATR, bands, flips)
  function refSuperTrend(d, n, mult) {
    const tr = (i) => (i === 0 ? d[i].high - d[i].low : Math.max(d[i].high - d[i].low, Math.abs(d[i].high - d[i - 1].close), Math.abs(d[i].low - d[i - 1].close)))
    const atr = new Array(d.length); let prev, acc = 0
    for (let i = 0; i < d.length; i++) { const t = tr(i); if (i < n) { acc += t; if (i === n - 1) { prev = acc / n; atr[i] = prev } continue } prev = (prev * (n - 1) + t) / n; atr[i] = prev }
    const out = new Array(d.length); let upper = 0, lower = 0, dir = 1, st
    for (let i = 0; i < d.length; i++) {
      const a = atr[i]; if (a === undefined) { out[i] = {}; continue }
      const hl2 = (d[i].high + d[i].low) / 2; let bu = hl2 + mult * a; let bl = hl2 - mult * a
      const pc = i > 0 ? d[i - 1].close : d[i].close
      if (i > 0 && st !== undefined) { bl = bl > lower || pc < lower ? bl : lower; bu = bu < upper || pc > upper ? bu : upper; if (dir > 0 && d[i].close < lower) dir = -1; else if (dir < 0 && d[i].close > upper) dir = 1 }
      if (st !== undefined && dir > 0 && d[i].close > upper && out[i - 1]?.dir === -1) bl = hl2 - mult * a
      if (st !== undefined && dir < 0 && d[i].close < lower && out[i - 1]?.dir === 1) bu = hl2 + mult * a
      upper = bu; lower = bl; st = dir > 0 ? lower : upper
      out[i] = { up: dir > 0 ? st : undefined, dn: dir < 0 ? st : undefined, dir }
    }
    return out
  }
  // AlphaTrend from the Pine formulas: ATR = sma(tr) (tr na on the first bar), MFI of hlc3, AT starts from nz(AT[1]) = 0
  function refAlphaTrend(d, coeff, n) {
    const tr = d.map((b, i) => (i === 0 ? undefined : Math.max(b.high - b.low, Math.abs(b.high - d[i - 1].close), Math.abs(b.low - d[i - 1].close))))
    const sma = (v, i) => { if (i < n - 1) return undefined; let s = 0; for (let j = 0; j < n; j++) { if (v[i - j] === undefined) return undefined; s += v[i - j] } return s / n }
    const tp = d.map((b) => (b.high + b.low + b.close) / 3)
    const mfiAt = (i) => { if (i < n) return undefined; let up = 0, dn = 0; for (let j = i - n + 1; j <= i; j++) { const ch = tp[j] - tp[j - 1]; if (ch > 0) up += tp[j] * d[j].volume; else if (ch < 0) dn += tp[j] * d[j].volume } return dn === 0 ? 100 : 100 - 100 / (1 + up / dn) }
    const at = []; const color = []
    for (let i = 0; i < d.length; i++) {
      const a = sma(tr, i); const prev = at[i - 1] ?? 0; const m = mfiAt(i)
      const upT = a === undefined ? undefined : d[i].low - a * coeff; const downT = a === undefined ? undefined : d[i].high + a * coeff
      at[i] = m !== undefined && m >= 50 ? (upT === undefined || upT < prev ? prev : upT) : (downT === undefined || downT > prev ? prev : downT)
      color[i] = i < 2 ? undefined : at[i] > at[i - 2] ? true : at[i] < at[i - 2] ? false : i >= 3 && at[i - 1] > at[i - 3]
    }
    return { at, color }
  }
  // rise, fall, a long flat stretch, rise again; uneven ranges and volumes
  const path = [...Array.from({ length: 50 }, (_, i) => 100 + i), ...Array.from({ length: 60 }, (_, i) => 150 - i),
    ...Array.from({ length: 40 }, () => 90), ...Array.from({ length: 50 }, (_, i) => 90 + i * 0.8)]
  const bars = path.map((c, i) => bar(i * H, c - 0.4, c + 0.8 + (i % 4) * 0.25, c - 0.9 - (i % 3) * 0.2, c + ((i % 5) - 2) * 0.05, 50 + ((i * 37) % 23)))
  const ref = refSuperTrend(bars, 10, 3)
  const got = C.superTrend(bars, 10, 3)
  ok(got.every((r, i) => r.up === ref[i].up && r.dn === ref[i].dn && r.dir === ref[i].dir), 'SuperTrend: identical to the implementation before the move')
  const pine = refAlphaTrend(bars, 1, 14)
  const mine = C.alphaTrend(bars, 1, 14)
  const from = pine.at.findIndex((v) => v !== 0)
  const sameAt = mine.slice(from + 2).every((r, k) => near(r.at, pine.at[from + 2 + k], 1e-9))
  ok(from > 0 && from < 20 && sameAt, `AlphaTrend: equal to the Pine formula once the original leaves its zero start (from bar ${from})`)
  ok(mine.slice(from + 5).every((r, k) => r.rising === pine.color[from + 5 + k]), 'AlphaTrend: colour as in the original, also on the flat stretch')
  // volume appearing later must not change bars before it (no look-ahead through the RSI / MFI choice)
  const quiet = bars.slice(0, 80).map((b, i) => ({ ...b, volume: i < 60 ? 0 : b.volume }))
  ok(JSON.stringify(C.alphaTrend(quiet.slice(0, 60), 1, 14)) === JSON.stringify(C.alphaTrend(quiet, 1, 14).slice(0, 60)), 'AlphaTrend: volume appearing later changes nothing before it')
}

if (failures.length) {
  console.error(`indicators: ${failures.length} of ${checks} checks failed:\n${failures.map((f) => `  - ${f}`).join('\n')}`)
  process.exit(1)
}
console.log(`indicators: ${checks} checks passed`)
