import type { IndicatorTemplate } from 'klinecharts'
import { fmtPrice } from '../../components/ui'
import { useStore } from '../../state/store'

/* ------------------------------------------------------------------------------------------------------------
 * Armed price alerts of the open symbol as dashed lines with a bell label at the right edge (like TradingView).
 * An internal indicator on the price pane: not in the indicator list, no legend, does not change the price scale.
 * Alerts on drawings are left out — the drawing itself already shows where they are.
 * ---------------------------------------------------------------------------------------------------------- */
export const ALERT_LINES = 'ALERTLINES'
export const ALERT_LINES_ID = '__alert_lines'
export interface AlertLevel { price: number; cond: 'cross' | 'up' | 'down' }

// the same 14 x 14 bell as the crosshair button (chart/theme.ts)
const BELL = 'M 3.5 10 L 3.5 6.6 Q 3.5 2.6 7 2.6 Q 10.5 2.6 10.5 6.6 L 10.5 10 M 2 10.4 L 12 10.4 M 5.8 12.2 Q 7 13.6 8.2 12.2 M 7 1.2 L 7 2.6'
const ARROW: Record<AlertLevel['cond'], string> = { cross: '', up: ' ↑', down: ' ↓' }

let bellPath: Path2D | null = null

export const ALERT_LINES_TEMPLATE: IndicatorTemplate<Record<string, never>, number, unknown> = {
  name: ALERT_LINES, shortName: '', calcParams: [], figures: [],
  calc: (d) => d.map(() => ({})),
  createTooltipDataSource: () => ({ name: '', calcParamsText: '', features: [], legends: [] }),
  draw: ({ ctx, indicator, bounding, yAxis }) => {
    const levels = ((indicator.extendData as { levels?: AlertLevel[] } | null)?.levels ?? [])
    if (!levels.length) return true
    const st = useStore.getState()
    const dark = st.theme === 'dark'
    const color = dark ? '#ffab2e' : '#c97a00'      // orange: readable for colour-blind users, unlike any candle colour
    const ink = dark ? '#1d2127' : '#ffffff'
    const precision = st.symbol?.price_precision ?? 2
    bellPath ??= new Path2D(BELL)
    ctx.save()
    ctx.font = '11px "IBM Plex Sans", system-ui, sans-serif'
    ctx.textBaseline = 'middle'
    for (const l of levels) {
      const y = Math.round(yAxis.convertToPixel(l.price)) + 0.5
      if (!(y >= 0 && y <= bounding.height)) continue
      ctx.setLineDash([5, 4])
      ctx.strokeStyle = color
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(0, y)
      ctx.lineTo(bounding.width, y)
      ctx.stroke()
      const text = fmtPrice(l.price, precision) + ARROW[l.cond]
      const w = ctx.measureText(text).width + 26
      const h = 18
      const x = bounding.width - w - 4
      ctx.setLineDash([])
      ctx.fillStyle = color
      ctx.beginPath()
      if (ctx.roundRect) ctx.roundRect(x, y - h / 2, w, h, 3)
      else ctx.rect(x, y - h / 2, w, h)
      ctx.fill()
      ctx.save()
      ctx.translate(x + 4, y - 7)
      ctx.strokeStyle = ink
      ctx.lineWidth = 1.3
      ctx.stroke(bellPath)
      ctx.restore()
      ctx.fillStyle = ink
      ctx.fillText(text, x + 20, y + 0.5)
    }
    ctx.restore()
    return true
  },
}
