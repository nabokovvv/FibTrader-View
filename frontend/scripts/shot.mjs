// Usage: node scripts/shot.mjs <url> <out.png> [width height] [--mobile] [--scale 2] [--clip x,y,w,h] [--eval "js"] [--click "selector"]...
// Opens the page in system Chrome via playwright-core, prints console errors/warnings, saves a screenshot.
import { chromium, devices } from 'playwright-core'

const args = process.argv.slice(2)
const url = args[0]
const out = args[1]
const mobile = args.includes('--mobile')
const width = Number(args[2]) || 1440
const height = Number(args[3]) || 900
const scaleArg = args.indexOf('--scale'); const scale = scaleArg >= 0 ? Number(args[scaleArg + 1]) : 1
const clipArg = args.indexOf('--clip'); const clip = clipArg >= 0 ? (([x, y, w, h]) => ({ x, y, width: w, height: h }))(args[clipArg + 1].split(',').map(Number)) : undefined
const actions = []
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--click') actions.push({ type: 'click', sel: args[++i] })
  if (args[i] === '--eval') actions.push({ type: 'eval', js: args[++i] })
  if (args[i] === '--type') actions.push({ type: 'type', sel: args[++i], text: args[++i] })
  if (args[i] === '--key') actions.push({ type: 'key', key: args[++i] })
  if (args[i] === '--wait') actions.push({ type: 'wait', ms: Number(args[++i]) })
  if (args[i] === '--mouse') { const [x, y] = args[++i].split(',').map(Number); actions.push({ type: 'mouse', x, y }) }
  if (args[i] === '--dbl') { const [x, y] = args[++i].split(',').map(Number); actions.push({ type: 'dbl', x, y }) }
  if (args[i] === '--hover') { const [x, y] = args[++i].split(',').map(Number); actions.push({ type: 'hover', x, y }) }
  if (args[i] === '--drag') { const [x1, y1, x2, y2] = args[++i].split(',').map(Number); actions.push({ type: 'drag', x1, y1, x2, y2 }) }
  if (args[i] === '--shot') actions.push({ type: 'shot', path: args[++i] })
}

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext(mobile ? { ...devices['iPhone 14'] } : { viewport: { width, height }, deviceScaleFactor: scale })
const page = await context.newPage()
const logs = []
page.on('console', (m) => { if (['error', 'warning'].includes(m.type())) logs.push(`[${m.type()}] ${m.text()}`) })
page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`))
await page.goto(url, { waitUntil: 'networkidle' })
await page.waitForTimeout(2500)
for (const a of actions) {
  if (a.type === 'click') await page.click(a.sel)
  else if (a.type === 'eval') console.log('eval:', JSON.stringify(await page.evaluate(a.js)))
  else if (a.type === 'type') await page.fill(a.sel, a.text)
  else if (a.type === 'key') await page.keyboard.press(a.key)
  else if (a.type === 'wait') await page.waitForTimeout(a.ms)
  else if (a.type === 'dbl') await page.mouse.dblclick(a.x, a.y)
  else if (a.type === 'hover') await page.mouse.move(a.x, a.y, { steps: 4 })
  else if (a.type === 'drag') { await page.mouse.move(a.x1, a.y1, { steps: 3 }); await page.waitForTimeout(120); await page.mouse.down(); await page.mouse.move(a.x2, a.y2, { steps: 8 }); await page.mouse.up() }
  else if (a.type === 'shot') await page.screenshot({ path: a.path, clip })
  else if (a.type === 'mouse') { await page.mouse.move(a.x, a.y); await page.waitForTimeout(80); await page.mouse.down(); await page.waitForTimeout(60); await page.mouse.up() }
  await page.waitForTimeout(400)
}
await page.waitForTimeout(800)
await page.screenshot({ path: out, clip })
console.log(logs.length ? logs.join('\n') : '(no console errors/warnings)')
await browser.close()
