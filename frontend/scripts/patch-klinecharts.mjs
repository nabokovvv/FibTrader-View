// Fixes in KLineChart v10.1.0, applied after every `npm install` (postinstall). Idempotent; fails loudly when the
// library changed and a fix no longer applies, so an upgrade does not silently bring a bug back.
//
// 1. Indicator lines: a piece of a line made of a single segment (two points) was never drawn — `items.length > 1`
//    in IndicatorView skipped it. A SuperTrend that has just flipped (flip bar + current bar) showed no line,
//    and every two-bar piece in history was invisible.
import { readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'

const require = createRequire(import.meta.url)
const dir = dirname(require.resolve('klinecharts/package.json'))
const file = join(dir, 'dist', 'index.esm.js')

const FIXES = [
  {
    name: 'draw one-segment indicator lines',
    from: 'lines_1.forEach(function (items) {\n                        var _a, _b, _c, _d;\n                        if (items.length > 1) {',
    to: 'lines_1.forEach(function (items) {\n                        var _a, _b, _c, _d;\n                        if (items.length > 0) { // patched: was > 1',
  },
]

let src = readFileSync(file, 'utf8')
let changed = false
for (const f of FIXES) {
  if (src.includes(f.to)) continue
  const n = src.split(f.from).length - 1
  if (n !== 1) {
    console.error(`patch-klinecharts: "${f.name}" does not apply (${n} matches) — check the KLineChart version`)
    process.exit(1)
  }
  src = src.replace(f.from, f.to)
  changed = true
}
if (changed) writeFileSync(file, src)
console.log(`patch-klinecharts: ${changed ? 'applied' : 'already applied'}`)
