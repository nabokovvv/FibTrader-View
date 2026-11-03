import { RU } from './ru'

/**
 * Tiny i18n for the public instance: English source strings are the keys, Russian comes from ./ru.
 * The language is decided once at start-up (saved choice, else the browser language: ru* → Russian, anything else
 * → English); switching saves the choice and reloads, so every label, chart legend and canvas text follows.
 * The private instance always runs in English.
 */
export type Lang = 'en' | 'ru'

const KEY = 'fv.lang'
let lang: Lang = 'en'
let enabled = false

function saved(): Lang | null {
  try {
    const v = window.localStorage.getItem(KEY)
    return v === 'en' || v === 'ru' ? v : null
  } catch {
    return null
  }
}

export function browserLang(): Lang {
  const list = typeof navigator !== 'undefined' ? (navigator.languages?.length ? navigator.languages : [navigator.language]) : []
  return list[0]?.toLowerCase().startsWith('ru') ? 'ru' : 'en'
}

/** Call once the server config is known; `allowed` is true on the public instance. */
export function initLang(allowed: boolean) {
  enabled = allowed
  lang = allowed ? (saved() ?? browserLang()) : 'en'
  document.documentElement.lang = lang
}

export const getLang = () => lang
export const langSwitchable = () => enabled

export function setLang(next: Lang) {
  if (next === lang) return
  try { window.localStorage.setItem(KEY, next) } catch { /* private mode: applies to this page load only */ }
  window.location.reload()
}

/** Translate an English UI string; `{name}` placeholders are filled from `vars`. */
export function t(s: string, vars?: Record<string, string | number>): string {
  let out = lang === 'ru' && Object.hasOwn(RU, s) ? RU[s] : s
  if (vars) out = out.replace(/\{(\w+)\}/g, (_, k: string) => (k in vars ? String(vars[k]) : `{${k}}`))
  return out
}

/** Watchlist names are the user's own text: only the default list names are translated. */
const DEFAULT_LIST_NAMES = new Set(['Crypto'])
export const wlName = (name: string) => (DEFAULT_LIST_NAMES.has(name) ? t(name) : name)

/** Count + noun: plural(3, ['bar', 'bars'], ['бар', 'бара', 'баров']) → "3 bars" / "3 бара". */
export function plural(n: number, en: [string, string], ru: [string, string, string]): string {
  if (lang !== 'ru') return `${n} ${n === 1 ? en[0] : en[1]}`
  const a = Math.abs(n) % 100, b = a % 10
  const word = a > 10 && a < 20 ? ru[2] : b === 1 ? ru[0] : b >= 2 && b <= 4 ? ru[1] : ru[2]
  return `${n} ${word}`
}
