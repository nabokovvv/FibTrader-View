import type { DeepPartial, Styles } from 'klinecharts'
import { t as tr } from '../i18n'
import type { Settings, Theme } from '../state/store'
import type { ChartType } from '../lib/types'

/** Indicator line palette: distinguishable for red-green colour blindness, no green/red pair. */
export const INDICATOR_COLORS = ['#ffab2e', '#c084fc', '#2dd4bf', '#f472b6', '#f1e9d6', '#fb923c', '#a78bfa', '#67e8f9']

const FONT = '"IBM Plex Sans", system-ui, sans-serif'

interface Tone {
  grid: string
  axisLine: string
  axisText: string
  text: string
  muted: string
  tooltipBg: string
  crosshair: string
  crossBg: string
  crossText: string
  separator: string
  separatorActive: string
  overlay: string
}

const DARK: Tone = {
  grid: '#20242b', axisLine: '#2a2f37', axisText: '#8b929c', text: '#d7dbe0', muted: '#8b929c',
  tooltipBg: 'rgba(29,33,39,.92)', crosshair: '#6b7380', crossBg: '#2f3540', crossText: '#f1e9d6',
  separator: '#2a2f37', separatorActive: 'rgba(241,233,214,.12)', overlay: '#f1e9d6',
}
const LIGHT: Tone = {
  grid: '#ebe8e1', axisLine: '#d9d5cc', axisText: '#6b7079', text: '#1f2328', muted: '#6b7079',
  tooltipBg: 'rgba(251,250,247,.92)', crosshair: '#8a9099', crossBg: '#2a2f37', crossText: '#fbfaf7',
  separator: '#d9d5cc', separatorActive: 'rgba(31,35,40,.08)', overlay: '#1f2328',
}

export function buildStyles(theme: Theme, settings: Settings, chartType: ChartType): DeepPartial<Styles> {
  const t = theme === 'dark' ? DARK : LIGHT
  const up = settings.upColor
  const down = settings.downColor
  const flat = t.muted
  const lines = INDICATOR_COLORS.map((color) => ({ color, size: 1, style: 'solid' as const, smooth: false, dashedValue: [2, 2] }))
  return {
    grid: {
      show: settings.showGrid,
      horizontal: { show: true, color: t.grid, size: 1, style: 'solid', dashedValue: [2, 2] },
      vertical: { show: true, color: t.grid, size: 1, style: 'solid', dashedValue: [2, 2] },
    },
    candle: {
      type: chartType,
      bar: {
        compareRule: 'current_open',
        upColor: up, downColor: down, noChangeColor: flat,
        upBorderColor: up, downBorderColor: down, noChangeBorderColor: flat,
        upWickColor: up, downWickColor: down, noChangeWickColor: flat,
      },
      area: {
        lineSize: 2, lineColor: t.overlay, smooth: false, value: 'close',
        backgroundColor: [
          { offset: 0, color: theme === 'dark' ? 'rgba(241,233,214,.16)' : 'rgba(31,35,40,.14)' },
          { offset: 1, color: 'rgba(0,0,0,0)' },
        ],
        point: { show: true, color: t.overlay, radius: 3, rippleColor: theme === 'dark' ? 'rgba(241,233,214,.25)' : 'rgba(31,35,40,.2)', rippleRadius: 7, animation: true, animationDuration: 1000 },
      },
      priceMark: {
        show: true,
        high: { show: true, color: t.axisText, textOffset: 5, textSize: 10, textFamily: FONT, textWeight: 'normal' },
        low: { show: true, color: t.axisText, textOffset: 5, textSize: 10, textFamily: FONT, textWeight: 'normal' },
        last: {
          show: settings.showLastPrice,
          compareRule: 'current_open',
          upColor: up, downColor: down, noChangeColor: flat,
          line: { show: true, style: 'dashed', dashedValue: [4, 4], size: 1 },
          text: {
            show: true, style: 'fill', size: 11, paddingLeft: 4, paddingTop: 3, paddingRight: 4, paddingBottom: 3,
            color: '#111418', family: FONT, weight: 600, borderStyle: 'solid', borderSize: 0, borderColor: 'transparent',
            borderDashedValue: [2, 2], borderRadius: 3,
          },
          extendTexts: [],
        },
      },
      tooltip: {
        showRule: 'always',
        showType: 'standard',
        offsetLeft: 8, offsetTop: 6, offsetRight: 8, offsetBottom: 6,
        title: { show: true, size: 12, family: FONT, weight: 500, color: t.text, marginLeft: 8, marginTop: 4, marginRight: 8, marginBottom: 4, template: '{ticker} · {period}' },
        legend: {
          size: 12, family: FONT, weight: 400, color: t.muted, marginLeft: 8, marginTop: 4, marginRight: 8, marginBottom: 4, defaultValue: '—',
          template: [
            { title: tr('O'), value: '{open}' }, { title: tr('H'), value: '{high}' }, { title: tr('L'), value: '{low}' },
            { title: tr('C'), value: '{close}' }, { title: tr('Vol'), value: '{volume}' },
          ],
        },
        rect: { position: 'fixed', paddingLeft: 6, paddingRight: 6, paddingTop: 6, paddingBottom: 6, offsetLeft: 8, offsetTop: 8, offsetRight: 8, offsetBottom: 8, borderRadius: 6, borderSize: 1, borderColor: t.axisLine, color: t.tooltipBg },
        features: [],
      },
    },
    indicator: {
      ohlc: { compareRule: 'current_open', upColor: up, downColor: down, noChangeColor: flat },
      bars: [{ style: 'fill', borderStyle: 'solid', borderSize: 1, borderDashedValue: [2, 2], upColor: hexAlpha(up, 0.55), downColor: hexAlpha(down, 0.55), noChangeColor: flat }],
      lines,
      circles: [{ style: 'fill', borderStyle: 'solid', borderSize: 1, borderDashedValue: [2, 2], upColor: hexAlpha(up, 0.6), downColor: hexAlpha(down, 0.6), noChangeColor: flat }],
      lastValueMark: { show: false, text: { show: false, style: 'fill', color: '#111418', size: 11, family: FONT, weight: 500, borderStyle: 'solid', borderSize: 0, borderColor: 'transparent', borderDashedValue: [2, 2], borderRadius: 3, paddingLeft: 4, paddingTop: 2, paddingRight: 4, paddingBottom: 2 } },
      tooltip: {
        showRule: 'always', showType: 'standard', offsetLeft: 8, offsetTop: 6, offsetRight: 8, offsetBottom: 6,
        title: { show: true, showName: true, showParams: true, size: 12, family: FONT, weight: 500, color: t.text, marginLeft: 8, marginTop: 4, marginRight: 8, marginBottom: 4 },
        legend: { size: 12, family: FONT, weight: 400, color: t.muted, marginLeft: 8, marginTop: 4, marginRight: 8, marginBottom: 4, defaultValue: '—' },
        features: [],
      },
    },
    xAxis: {
      show: true, size: 'auto',
      axisLine: { show: true, color: t.axisLine, size: 1 },
      tickLine: { show: true, color: t.axisLine, size: 1, length: 3 },
      tickText: { show: true, color: t.axisText, family: FONT, weight: 'normal', size: 11, marginStart: 4, marginEnd: 4 },
    },
    yAxis: {
      show: true, size: 'auto',
      axisLine: { show: true, color: t.axisLine, size: 1 },
      tickLine: { show: true, color: t.axisLine, size: 1, length: 3 },
      tickText: { show: true, color: t.axisText, family: FONT, weight: 'normal', size: 11, marginStart: 4, marginEnd: 4 },
    },
    separator: { size: 1, color: t.separator, fill: true, activeBackgroundColor: t.separatorActive },
    crosshair: {
      show: true,
      horizontal: {
        show: true,
        line: { show: true, style: 'dashed', dashedValue: [4, 2], size: 1, color: t.crosshair },
        text: { show: true, style: 'fill', color: t.crossText, size: 11, family: FONT, weight: 'normal', borderStyle: 'solid', borderDashedValue: [2, 2], borderSize: 1, borderColor: t.crossBg, borderRadius: 2, paddingLeft: 4, paddingRight: 4, paddingTop: 3, paddingBottom: 3, backgroundColor: t.crossBg },
        features: [],
      },
      vertical: {
        show: true,
        line: { show: true, style: 'dashed', dashedValue: [4, 2], size: 1, color: t.crosshair },
        text: { show: true, style: 'fill', color: t.crossText, size: 11, family: FONT, weight: 'normal', borderStyle: 'solid', borderDashedValue: [2, 2], borderSize: 1, borderColor: t.crossBg, borderRadius: 2, paddingLeft: 4, paddingRight: 4, paddingTop: 3, paddingBottom: 3, backgroundColor: t.crossBg },
      },
    },
    overlay: {
      point: { color: t.overlay, borderColor: hexAlpha(t.overlay, 0.35), borderSize: 1, radius: 5, activeColor: t.overlay, activeBorderColor: hexAlpha(t.overlay, 0.35), activeBorderSize: 3, activeRadius: 5 },
      line: { style: 'solid', smooth: false, color: t.overlay, size: 1, dashedValue: [2, 2] },
      rect: { style: 'fill', color: hexAlpha(t.overlay, 0.12), borderColor: t.overlay, borderSize: 1, borderRadius: 0, borderStyle: 'solid', borderDashedValue: [2, 2] },
      polygon: { style: 'fill', color: hexAlpha(t.overlay, 0.12), borderColor: t.overlay, borderSize: 1, borderStyle: 'solid', borderDashedValue: [2, 2] },
      circle: { style: 'fill', color: hexAlpha(t.overlay, 0.12), borderColor: t.overlay, borderSize: 1, borderStyle: 'solid', borderDashedValue: [2, 2] },
      arc: { style: 'solid', color: t.overlay, size: 1, dashedValue: [2, 2] },
      text: { style: 'fill', color: '#111418', size: 12, family: FONT, weight: 'normal', borderStyle: 'solid', borderDashedValue: [2, 2], borderSize: 0, borderColor: 'transparent', borderRadius: 3, paddingLeft: 4, paddingRight: 4, paddingTop: 3, paddingBottom: 3, backgroundColor: t.overlay },
    },
  }
}

export function hexAlpha(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex)
  if (!m) return hex
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`
}
