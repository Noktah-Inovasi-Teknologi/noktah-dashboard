#!/usr/bin/env bun
/**
 * Claude Code PostToolUse hook (Edit|Write): the STATIC half of the Hub's UI
 * gate, the moment a file under service/web/app is edited.
 *
 * Runs service/web/tests/ui-conventions.test.ts (about a second): Nuxt UI
 * controls instead of raw HTML, semantic colours that follow dark mode, no
 * literal colours or pixel sizes, Nuxt UI in Bahasa. Exit 2 cannot undo the
 * edit, but it puts the failure (file and line) in front of whoever just made
 * it. Anything else: exit 0, silent.
 *
 * The RENDERED half (every width, both themes, every data state) is the UI
 * sweep; see .claude/skills/ui-sweep/SKILL.md and pre-push-ui-sweep-gate.ts.
 * Adapted from venyu's post-edit-ui-gate.ts, 2026-09-25.
 */
import { isAbsolute, relative, sep } from 'node:path'

const input = await Bun.stdin.text()
let filePath = ''
try {
  filePath = String(JSON.parse(input)?.tool_input?.file_path ?? '')
} catch {
  process.exit(0)
}
if (!filePath) process.exit(0)

let rel = isAbsolute(filePath) ? relative(process.cwd(), filePath) : filePath
rel = rel.split(sep).join('/')
if (!/^service\/web\/app\/.+\.(?:vue|ts|css)$/.test(rel)) process.exit(0)

const run = Bun.spawnSync(['bun', 'test', 'tests/ui-conventions.test.ts'], {
  cwd: 'service/web', stdout: 'pipe', stderr: 'pipe'
})
if (run.exitCode === 0) process.exit(0)

const out = new TextDecoder().decode(run.stdout) + new TextDecoder().decode(run.stderr)
// Only the failing assertions and their hits; the full output is mostly green noise.
const relevant = out
  .split('\n')
  .filter(line => /^\s*(?:\(fail\)|error:|[+-]\s+"|\s+\d+ pass|\s+\d+ fail)/.test(line))
  .join('\n')

console.error(
  `[Hub UI gate: static checks FAILED after editing "${rel}"]\n${relevant}\n`
  + 'Fix before moving on: use Nuxt UI controls (UButton, UInput, USelect, UTable), '
  + 'semantic colour classes (text-muted, bg-elevated, text-primary …) rather than palette steps, '
  + 'no literal colours or px sizes, and keep <UApp :locale="id">. '
  + 'See .claude/skills/ui-sweep/SKILL.md.'
)
process.exit(2)
