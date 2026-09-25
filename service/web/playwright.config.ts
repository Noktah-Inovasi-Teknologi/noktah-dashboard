import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright for the Hub, used by the UI sweep (`e2e/ui-sweep.e2e.ts`, the
 * `/ui-sweep` skill, and the push gate in scripts/claude-hooks/).
 *
 * The Hub's real login is Cloudflare Access, which a local browser can't pass,
 * and its data comes from hub-api on the office PC. So the sweep runs the dev
 * server in FIXTURE mode (NUXT_HUB_FIXTURES=1, server/fixtures/): sample data
 * in every state (full, empty, office PC down), dev server only. No Docker
 * stack is needed.
 *
 * Port 3100, not Nuxt's default 3000, so the sweep's fixture server never gets
 * confused with a `bun run dev` you already have open against real data.
 *
 * Windows: run from a properly-cased path (C:/…, not c:/…); Vite compares
 * drive letters case-sensitively.
 */
export default defineConfig({
  testDir: './e2e',
  // .e2e.ts, not .spec.ts, so `bun test` never picks Playwright files up.
  testMatch: /.*\.e2e\.ts/,
  // Playwright empties its outputDir before every run. Keep it apart from
  // test-results/ui-sweep/, whose last-clean.json marker must survive later runs.
  outputDir: './test-results/playwright',
  workers: 1,
  timeout: 120_000,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:3100',
    locale: 'id-ID',
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome']
  },
  webServer: {
    command: 'bun run dev --port 3100',
    url: 'http://localhost:3100',
    reuseExistingServer: true,
    timeout: 180_000,
    env: { NUXT_HUB_FIXTURES: '1' }
  }
})
