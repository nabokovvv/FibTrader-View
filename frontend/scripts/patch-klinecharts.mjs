// Fixes in KLineChart v10.1.0, applied after every `npm install` (postinstall). Idempotent; fails loudly when the
// library changed and a fix no longer applies, so an upgrade does not silently bring a bug back.
//
// 1. Indicator lines: a piece of a line made of a single segment (two points) was never drawn — `items.length > 1`
//    in IndicatorView skipped it. A SuperTrend that has just flipped (flip bar + current bar) showed no line,
//    and every two-bar piece in history was invisible.
// 2. Crosshair buttons (the alert bell next to the y-axis label) existed on the candle pane only. Indicator panes get
//    the same view (created on their first overlay draw; the candle pane overrides that method and keeps its own) and
//    show it when the app says so: `globalThis.__lvCrosshairFeaturePane(paneId)` returns true (panes of indicators
//    that can carry an alert).
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
  {
    name: 'crosshair button view on indicator panes',
    from: '    IndicatorWidget.prototype.updateOverlayContent = function (_ctx) {\n        // to do it\n    };',
    to: '    IndicatorWidget.prototype.updateOverlayContent = function (_ctx) { // patched: crosshair buttons on indicator panes\n        if (!this._lvFeatureView) {\n            this._lvFeatureView = new CrosshairFeatureView(this);\n            this.addChild(this._lvFeatureView);\n        }\n        this._lvFeatureView.draw(_ctx);\n    };',
  },
  {
    name: 'crosshair buttons on chosen indicator panes',
    from: 'if (isString(crosshair.paneId) && crosshair.paneId === pane.getId() && yAxis.isInCandle()) {',
    to: 'if (isString(crosshair.paneId) && crosshair.paneId === pane.getId() && (yAxis.isInCandle() || (typeof globalThis.__lvCrosshairFeaturePane === \'function\' && globalThis.__lvCrosshairFeaturePane(pane.getId())))) { // patched: candle pane only',
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
