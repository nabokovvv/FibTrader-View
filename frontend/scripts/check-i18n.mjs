#!/usr/bin/env node
/**
 * UI strings must have a Russian translation in src/i18n/ru.ts, with the same {placeholders}.
 *
 * The sources are parsed with the TS/TSX parser Vite already ships (rolldown / oxc), and these forms are followed:
 *  - string literals given to t(...) / tr(...), anywhere (also inside template literals and JSX):
 *      t('Close'), t(on ? 'Hide' : 'Show'), t(x ?? 'Default'), `${t('Risk/Reward Ratio')}: …`;
 *  - t(obj.label) / t(meta.paramNames[i]) → every `label:` / `paramNames:` property value in the sources;
 *  - t(METRIC_LABEL[m]) → every string value of the object declared as METRIC_LABEL.
 * Not followed: a plain variable (t(name)) and text that is not passed through t() at all. The check is a safety
 * net for the usual forms, not a proof that every visible word is translated.
 *
 *   node scripts/check-i18n.mjs        # self-test, then the check; exit 1 and a list when something is missing
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseAst } from 'rolldown/parseAst'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const T_NAMES = new Set(['t', 'tr'])
// Property names too common to follow (t(p.name) would pull in every `name:` of every object)
const GENERIC = new Set(['name', 'id', 'key', 'type', 'value'])
// Words that stay as they are in Russian too (names, Ichimoku's Japanese line names). Abbreviations and tickers
// (no run of two lowercase letters: 'RSI: ', '%K: ', 'R1: ', 'MOEX') need no entry either.
const SAME_IN_BOTH = new Set(['Bybit', 'English', 'Tenkan: ', 'Kijun: ', 'Span A: ', 'Span B: ', 'Chikou: '])
const isAbbreviation = (s) => !/[a-z]{2}/.test(s)

function files(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) {
      // optional plugin folders: the private instance runs in English only
      if (relative(SRC, p).startsWith('plugins')) continue
      out.push(...files(p))
    } else if (/\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts') && relative(SRC, p) !== join('i18n', 'ru.ts')) out.push(p)
  }
  return out
}

/** Every node of an ESTree-like tree, depth first. */
function* walk(node) {
  if (!node || typeof node !== 'object') return
  if (Array.isArray(node)) { for (const n of node) yield* walk(n); return }
  if (typeof node.type === 'string') yield node
  for (const k in node) if (k !== 'parent' && node[k] && typeof node[k] === 'object') yield* walk(node[k])
}

/** Strip TS wrappers: `x as const`, `x satisfies T`, `x!`, `(x)`. */
function bare(n) {
  while (n && ['TSAsExpression', 'TSSatisfiesExpression', 'TSNonNullExpression', 'ParenthesizedExpression', 'TSTypeAssertion'].includes(n.type)) n = n.expression
  return n
}

const str = (n) => (n?.type === 'Literal' && typeof n.value === 'string' ? n.value
  : n?.type === 'TemplateLiteral' && n.expressions.length === 0 ? n.quasis[0].value.cooked : null)

const keyName = (p) => (p.computed ? null : p.key?.type === 'Identifier' ? p.key.name : str(p.key))

/** Strings in a text value: a literal, both branches of `c ? a : b`, both sides of `a ?? b` / `a || b`, arrays. */
function texts(n, out = []) {
  n = bare(n)
  if (!n) return out
  const s = str(n)
  if (s !== null) out.push(s)
  else if (n.type === 'ConditionalExpression') { texts(n.consequent, out); texts(n.alternate, out) }
  else if (n.type === 'LogicalExpression') { texts(n.left, out); texts(n.right, out) }
  else if (n.type === 'ArrayExpression') for (const e of n.elements) texts(e, out)
  return out
}

/** The value expressions of a t() argument (branches of conditionals), for following properties and maps. */
function branches(n) {
  n = bare(n)
  if (n?.type === 'ConditionalExpression') return [...branches(n.consequent), ...branches(n.alternate)]
  if (n?.type === 'LogicalExpression') return [...branches(n.left), ...branches(n.right)]
  return n ? [n] : []
}

/** UI strings of a set of sources: Map text → "file:line". */
export function collect(sources) {
  const keys = new Map(), props = new Set(), maps = new Set()
  const parsed = sources.map(({ file, code }) => {
    const lines = []
    for (let i = 0; i < code.length; i++) if (code[i] === '\n') lines.push(i)
    const lineOf = (pos) => { let lo = 0, hi = lines.length; while (lo < hi) { const m = (lo + hi) >> 1; if (lines[m] < pos) lo = m + 1; else hi = m } return lo + 1 }
    return { file, lineOf, ast: parseAst(code, { lang: file.endsWith('.tsx') ? 'tsx' : 'ts' }, file) }
  })
  const add = (v, at) => { if (v.trim() && !keys.has(v)) keys.set(v, at) }

  for (const { file, lineOf, ast } of parsed) {
    for (const n of walk(ast)) {
      if (n.type !== 'CallExpression' || n.callee.type !== 'Identifier' || !T_NAMES.has(n.callee.name) || !n.arguments[0]) continue
      const at = `${file}:${lineOf(n.start)}`
      for (const s of texts(n.arguments[0])) add(s, at)
      for (const b of branches(n.arguments[0])) {
        if (b.type !== 'MemberExpression') continue
        if (!b.computed && b.property.type === 'Identifier') { if (!GENERIC.has(b.property.name)) props.add(b.property.name) }
        else if (b.computed) {
          const o = bare(b.object)
          if (o.type === 'Identifier') maps.add(o.name)
          else if (o.type === 'MemberExpression' && !o.computed && !GENERIC.has(o.property.name)) props.add(o.property.name)
        }
      }
    }
  }
  for (const { file, lineOf, ast } of parsed) {
    for (const n of walk(ast)) {
      // `label: 'x'` / `paramNames: ['a', 'b']` in object literals (type members are TSPropertySignature: skipped)
      if (n.type === 'Property' && props.has(keyName(n)))
        for (const s of texts(n.value)) add(s, `${file}:${lineOf(n.start)}`)
      // const METRIC_LABEL: Record<…> = { price: 'Price', … }
      if (n.type === 'VariableDeclarator' && n.id.type === 'Identifier' && maps.has(n.id.name)) {
        const init = bare(n.init)
        if (init?.type === 'ObjectExpression')
          for (const p of init.properties) if (p.type === 'Property') for (const s of texts(p.value)) add(s, `${file}:${lineOf(p.start)}`)
      }
    }
  }
  return keys
}

/** ru.ts: the object literal assigned to RU. */
function readRu(code) {
  const ru = {}
  for (const n of walk(parseAst(code, { lang: 'ts' }, 'ru.ts')))
    if (n.type === 'VariableDeclarator' && n.id.name === 'RU')
      for (const p of bare(n.init).properties) { const k = keyName(p), v = str(p.value); if (k !== null && v !== null) ru[k] = v }
  return ru
}

/** The forms the checker must keep understanding (and the code it must not choke on). */
function selfTest() {
  const code = `
    import { t, tr } from './i18n'
    const re = /["']/g
    const LBL = { a: 'From a map', b: \`Map template\` }
    const CAT = [{ label: 'A label', paramNames: ['Param one', 'Param two'] }]
    type X = { label: string }
    export const Box = ({ c, k, m }: { c: boolean; k: 'a' | 'b'; m: X }) => (
      <p title={t('Plain call')}>Don't panic {t(c ? 'Branch one' : \`Branch two\`)} {t(m.label)} {t(CAT[0].paramNames[0])}
        {t(LBL[k])} {\`\${tr('In a template')}: \${c ? t('Nested branch') : ''}\`} {t(k ?? 'After nullish')}
        {t('With {count} vars', { count: 1 })}</p>)
    const ignored = (name: string) => t(name)`
  const got = new Set(collect([{ file: 'self.tsx', code }]).keys())
  const want = ['Plain call', 'Branch one', 'Branch two', 'A label', 'Param one', 'Param two', 'From a map', 'Map template',
    'In a template', 'Nested branch', 'After nullish', 'With {count} vars']
  const missed = want.filter((w) => !got.has(w))
  const extra = [...got].filter((g) => !want.includes(g))
  if (missed.length || extra.length) {
    console.error(`i18n checker self-test failed. missed: ${JSON.stringify(missed)} extra: ${JSON.stringify(extra)}`)
    process.exit(2)
  }
}

selfTest()
const keys = collect(files(SRC).map((f) => ({ file: relative(SRC, f), code: readFileSync(f, 'utf8') })))
const RU = readRu(readFileSync(join(SRC, 'i18n', 'ru.ts'), 'utf8'))
const ph = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',')
const missing = [], badVars = []
for (const [en, where] of keys) {
  if (SAME_IN_BOTH.has(en) || isAbbreviation(en)) continue
  if (!Object.hasOwn(RU, en)) missing.push(`  ${where}  ${JSON.stringify(en)}`)
  else if (ph(en) !== ph(RU[en])) badVars.push(`  ${where}  ${JSON.stringify(en)} → ${JSON.stringify(RU[en])}`)
}
if (missing.length || badVars.length) {
  if (missing.length) console.error(`i18n: ${missing.length} UI string(s) without a Russian translation in src/i18n/ru.ts:\n${missing.join('\n')}`)
  if (badVars.length) console.error(`i18n: placeholders differ between English and Russian:\n${badVars.join('\n')}`)
  process.exit(1)
}
console.log(`i18n: ${keys.size} UI strings, all translated (${Object.keys(RU).length} translations)`)
