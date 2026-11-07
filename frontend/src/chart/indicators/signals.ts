import type { Chart, XAxis, YAxis } from 'klinecharts'
import { t } from '../../i18n'
import { useStore } from '../../state/store'

/* ------------------------------------------------------------------------------------------------------------
 * Buy / sell markers of trend indicators (SuperTrend, AlphaTrend): a rounded badge with the word (white on the rise
 * colour under the line for a buy, on the fall colour above it for a sell) on a short stem from the indicator line,
 * so the signal is not told by colour alone. Only the bars on screen are drawn.
 * ---------------------------------------------------------------------------------------------------------- */
export function drawSignals(ctx: CanvasRenderingContext2D, chart: Chart, xAxis: XAxis, yAxis: YAxis,
  signalAt: (i: number) => 1 | -1 | undefined, anchorAt?: (i: number) => number | undefined) {
  const list = chart.getDataList()
  const vr = chart.getVisibleRange()
  const from = Math.max(0, Math.floor(vr.from))
  const to = Math.min(list.length, Math.ceil(vr.to))
  const { upColor, downColor } = useStore.getState().settings
  const words = { buy: t('Buy'), sell: t('Sell') }
  ctx.save()
  ctx.font = '600 11px sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  for (let i = from; i < to; i++) {
    const s = signalAt(i)
    if (!s) continue
    const up = s > 0
    const color = up ? upColor : downColor
    const x = xAxis.convertToPixel(i)
    // the badge hangs off the indicator line (or the bar's low / high), away from the candles
    const anchor = anchorAt?.(i) ?? (up ? list[i].low : list[i].high)
    const y = yAxis.convertToPixel(anchor)
    const text = up ? words.buy : words.sell
    const w = ctx.measureText(text).width + 12
    const h = 18
    const stem = 8
    const top = up ? y + stem : y - stem - h
    ctx.fillStyle = color
    // stem with a dot on the line
    ctx.beginPath()
    ctx.arc(x, y, 2.5, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillRect(x - 1, Math.min(y, up ? top : top + h), 2, stem)
    // rounded badge, white word
    const r = 4
    const left = x - w / 2
    ctx.beginPath()
    ctx.moveTo(left + r, top)
    ctx.arcTo(left + w, top, left + w, top + h, r)
    ctx.arcTo(left + w, top + h, left, top + h, r)
    ctx.arcTo(left, top + h, left, top, r)
    ctx.arcTo(left, top, left + w, top, r)
    ctx.closePath()
    ctx.fill()
    ctx.fillStyle = '#ffffff'
    ctx.fillText(text, x, top + h / 2 + 0.5)
  }
  ctx.restore()
}
