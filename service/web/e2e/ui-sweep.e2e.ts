import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Browser, type Page } from '@playwright/test'
import { auditDocument, refixShellAfterScreenshot, unfixShellForScreenshot, type AuditFinding } from './ui-sweep/audit.ts'

/**
 * The Hub's UI sweep: every page, every breakpoint, both colour schemes, and
 * every data state, checked by rule rather than by eye.
 *
 * Adapted from venyu's sweep (2026-09-25). The in-page checks
 * (`ui-sweep/audit.ts`) are copied unchanged: overflow, clipped text, crumpled
 * text, and WCAG contrast for text and icons. What differs here:
 *
 *   - **No login step.** The Hub's login is Cloudflare Access; the dev server
 *     runs in fixture mode instead (playwright.config.ts), and each page is
 *     swept in each DATA state via the `hub-fixture` cookie: full (long names,
 *     many rows), empty, and down (office PC off). Loading/empty/error is where
 *     layouts break, so those states are cells, not afterthoughts.
 *   - **Shell states** (collapsed sidebar, phone drawer) are exercised when a
 *     route asks for them AND the controls exist, so the sweep is ready for the
 *     dashboard layout without failing on today's plain header.
 *
 * Two modes:
 *   - **Gate** (default): findings fail the test. Three widths, both themes.
 *     A clean gate run writes test-results/ui-sweep/last-clean.json, which
 *     the push gate (scripts/claude-hooks/pre-push-ui-sweep-gate.ts) checks.
 *   - **Sweep** (`UI_SWEEP_FULL=1`): all six widths plus full-page screenshots.
 *     `UI_SWEEP_SOFT=1` records without failing. The /ui-sweep skill runs this.
 *
 * No pixel snapshots: those fail on every intended change and get ignored
 * within a month. A rule-based check fails only when a rule is broken.
 */

const FULL = process.env.UI_SWEEP_FULL === '1'
const SOFT = process.env.UI_SWEEP_SOFT === '1'
const SHOTS = FULL || process.env.UI_SWEEP_SHOTS === '1'
const OUT = join(process.cwd(), 'test-results', 'ui-sweep')

/** Tailwind's breakpoints, plus 360: below `sm`, where a phone actually is. */
const ALL_VIEWPORTS = [
  { name: 'xs', width: 360 },
  { name: 'sm', width: 640 },
  { name: 'md', width: 768 },
  { name: 'lg', width: 1024 },
  { name: 'xl', width: 1280 },
  { name: '2xl', width: 1536 }
] as const
const GATE_VIEWPORTS = ALL_VIEWPORTS.filter(v => v.name === 'xs' || v.name === 'md' || v.name === 'xl')
const VIEWPORTS = FULL ? ALL_VIEWPORTS : GATE_VIEWPORTS
const THEMES = ['light', 'dark'] as const
type DataState = 'full' | 'empty' | 'down' | 'no_access' | 'failed' | 'cap'

const AUDIT_OPTIONS = { textRatio: 4.5, largeTextRatio: 3, iconRatio: 3, limit: 25 }

interface Route {
  slug: string
  path: string
  /** Which fixture data states to sweep this page in. */
  states: DataState[]
  /** Also exercise the collapsed sidebar and the phone drawer (once a sidebar exists). */
  shellStates?: boolean
}

/**
 * EVERY page of the Hub belongs here. A new page that is not listed is a page
 * nobody checked; add it in the same change that adds the page.
 */
const CLIENT = '/clients/00000000-0000-4000-8000-000000000007'

const ROUTES: Route[] = [
  { slug: 'home', path: '/', states: ['full', 'empty', 'down'], shellStates: true },
  // A signed-in Person without a role is sent from any page to /no-access (SC-009).
  { slug: 'no-access', path: '/', states: ['no_access'] },
  // One Client (fixture: Klinik Mata Sampang), each Client Card tab.
  { slug: 'client-ringkasan', path: `${CLIENT}?tab=ringkasan`, states: ['full', 'empty', 'down'] },
  { slug: 'client-profil', path: `${CLIENT}?tab=profil`, states: ['full', 'empty'] },
  { slug: 'client-guideline', path: `${CLIENT}?tab=guideline`, states: ['full', 'empty'], shellStates: true },
  { slug: 'client-permintaan', path: `${CLIENT}?tab=permintaan`, states: ['full', 'empty'] },
  { slug: 'client-registry', path: `${CLIENT}?tab=registry`, states: ['full', 'empty'], shellStates: true },
  { slug: 'client-riwayat', path: `${CLIENT}?tab=riwayat`, states: ['full', 'empty'] },
  { slug: 'approvals', path: '/approvals', states: ['full', 'empty', 'down'] },
  { slug: 'people', path: '/people', states: ['full', 'empty', 'down'] },
  // Person detail: an active PM (idOf(901)) and a leaver (idOf(904)).
  { slug: 'person', path: '/people/00000000-0000-4000-8000-000000000901', states: ['full', 'down'], shellStates: true },
  { slug: 'person-left', path: '/people/00000000-0000-4000-8000-000000000904', states: ['full'] },
  // Intake: Proposals with every flag (full), nothing found (empty), AI failed, cap reached.
  { slug: 'intake', path: `${CLIENT}/intake`, states: ['full', 'empty', 'down', 'failed', 'cap'], shellStates: true }
]

interface Cell {
  route: string
  path: string
  data: DataState
  theme: string
  viewport: string
  state: 'default' | 'collapsed' | 'drawer'
  findings: AuditFinding[]
}

const report: Cell[] = []

test.describe.configure({ mode: 'serial' })

test.beforeAll(() => {
  mkdirSync(OUT, { recursive: true })
  // Screenshots from an earlier sweep would pass for this one's.
  if (SHOTS) rmSync(join(OUT, 'shots'), { recursive: true, force: true })
})

test.afterAll(() => {
  mkdirSync(OUT, { recursive: true })
  writeFileSync(join(OUT, 'report.json'), JSON.stringify(report, null, 2))
  writeFileSync(join(OUT, 'report.md'), renderReport(report))

  // The marker the push gate reads: written only by a GATE run (never soft)
  // that swept every route, state, theme and width clean.
  const expected = ROUTES.reduce((n, r) => n + r.states.length, 0) * THEMES.length * VIEWPORTS.length
  const sweptAll = report.filter(c => c.state === 'default').length >= expected
  const clean = report.every(cell => gating(cell).length === 0)
  if (!SOFT && sweptAll && clean) {
    writeFileSync(join(OUT, 'last-clean.json'), JSON.stringify({ key: treeKey(), at: new Date().toISOString() }, null, 2))
  }
})

/**
 * A hash of the CONTENT of every file under service/web/app as it stands on
 * disk, tracked or not. Content rather than HEAD + diff, so committing the
 * swept files does not change the key. Must stay byte-identical to the one in
 * scripts/claude-hooks/pre-push-ui-sweep-gate.ts, or the gate never opens.
 */
function treeKey(): string {
  const root = join(process.cwd(), '..', '..')
  const git = (args: string[], input?: string) => {
    try {
      return execFileSync('git', args, { cwd: root, encoding: 'utf8', input }).trim()
    } catch {
      return ''
    }
  }
  const files = git(['ls-files', '-co', '--exclude-standard', '--', 'service/web/app'])
    .split('\n')
    .filter(file => file && existsSync(join(root, file)))
  const hashes = git(['hash-object', '--stdin-paths'], `${files.join('\n')}\n`).split('\n')
  const manifest = files.map((file, i) => `${file}:${hashes[i] ?? ''}`).join('\n')
  return createHash('sha1').update(manifest).digest('hex')
}

/** Wait until the reads have landed: no skeletons, nothing marked busy. */
async function settle(page: Page): Promise<void> {
  await page.waitForLoadState('domcontentloaded')
  await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {})
  await expect
    .poll(() => page.locator('.animate-pulse, [aria-busy="true"]').count(), { timeout: 10_000 })
    .toBe(0)
    .catch(() => {})
  await page.waitForTimeout(150)
}

async function audit(page: Page, route: Route, data: DataState, theme: string, viewport: string, state: Cell['state']): Promise<Cell> {
  const findings = await page.evaluate(auditDocument, AUDIT_OPTIONS)
  const cell: Cell = { route: route.slug, path: route.path, data, theme, viewport, state, findings }
  report.push(cell)

  if (SHOTS) {
    const dir = join(OUT, 'shots', theme, viewport)
    mkdirSync(dir, { recursive: true })
    await page.evaluate(unfixShellForScreenshot)
    const suffix = `${data === 'full' ? '' : `.${data}`}${state === 'default' ? '' : `.${state}`}`
    await page.screenshot({ path: join(dir, `${route.slug}${suffix}.png`), fullPage: true })
    await page.evaluate(refixShellAfterScreenshot)
  }
  return cell
}

/** Gating findings: everything except WCAG-exempt disabled states. */
function gating(cell: Cell): AuditFinding[] {
  return cell.findings.filter(f => !f.exempt && f.where !== '(report)')
}

function assertClean(cells: Cell[]) {
  const failures = cells.flatMap(cell => gating(cell).map(f =>
    `[${cell.data} ${cell.theme} ${cell.viewport} ${cell.state}] ${f.kind}: ${f.where} "${f.text}" — ${f.detail}`))
  if (SOFT) {
    test.info().annotations.push({ type: 'ui-sweep', description: `${failures.length} findings (soft)` })
    return
  }
  expect(failures, `UI sweep findings on ${cells[0]?.path}`).toEqual([])
}

async function exerciseShell(page: Page, route: Route, data: DataState, theme: string, cells: Cell[]) {
  // The collapsed rail (desktop). Skipped quietly while the Hub has no sidebar.
  await page.setViewportSize({ width: 1280, height: 900 })
  const collapse = page.getByRole('button', { name: /collapse sidebar|ciutkan/i })
  if (await collapse.count()) {
    await collapse.first().click()
    await page.waitForTimeout(250)
    cells.push(await audit(page, route, data, theme, 'xl', 'collapsed'))
    await page.getByRole('button', { name: /expand sidebar|bentangkan|perluas/i }).first().click()
    await page.waitForTimeout(250)
  }
  // The drawer / mobile menu (phone).
  await page.setViewportSize({ width: 360, height: 800 })
  const open = page.getByRole('button', { name: /open (sidebar|menu)|buka (menu|sidebar)/i })
  if (await open.count()) {
    await open.first().click()
    await page.waitForTimeout(300)
    cells.push(await audit(page, route, data, theme, 'xs', 'drawer'))
    await page.keyboard.press('Escape')
  }
}

async function sweepRoute(browser: Browser, route: Route): Promise<Cell[]> {
  const cells: Cell[] = []
  for (const data of route.states) {
    for (const theme of THEMES) {
      const context = await browser.newContext({
        locale: 'id-ID',
        colorScheme: theme,
        viewport: { width: 1280, height: 900 }
      })
      await context.addCookies([{ name: 'hub-fixture', value: data, url: 'http://localhost:3100' }])
      const page = await context.newPage()
      try {
        await page.goto(route.path)
        await settle(page)
        for (const viewport of VIEWPORTS) {
          await page.setViewportSize({ width: viewport.width, height: 900 })
          await page.waitForTimeout(120)
          cells.push(await audit(page, route, data, theme, viewport.name, 'default'))
        }
        if (route.shellStates) await exerciseShell(page, route, data, theme, cells)
      } finally {
        await context.close()
      }
    }
  }
  return cells
}

for (const route of ROUTES) {
  test(`${route.slug} (${route.path})`, async ({ browser }) => {
    assertClean(await sweepRoute(browser, route))
  })
}

function renderReport(cells: Cell[]): string {
  const lines: string[] = ['# UI sweep report', '', `${cells.length} cells (page × data state × theme × width × shell state).`, '']
  const byRoute = new Map<string, Cell[]>()
  for (const cell of cells) byRoute.set(cell.route, [...(byRoute.get(cell.route) ?? []), cell])
  for (const [route, group] of byRoute) {
    const total = group.reduce((n, c) => n + gating(c).length, 0)
    lines.push(`## ${route} — ${total} finding${total === 1 ? '' : 's'}  (${group[0]!.path})`, '')
    for (const cell of group) {
      const found = gating(cell)
      if (found.length === 0) continue
      lines.push(`### ${cell.data} · ${cell.theme} · ${cell.viewport} · ${cell.state}`, '')
      for (const f of found) lines.push(`- **${f.kind}** \`${f.where}\` "${f.text}" — ${f.detail}`)
      lines.push('')
    }
  }
  return lines.join('\n')
}
