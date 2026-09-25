#!/usr/bin/env bun
/**
 * Claude Code PreToolUse hook (Bash|PowerShell): the RENDERED Hub UI gate at
 * hand-back, which in this repo means pushing or opening a pull request (work
 * happens on master; changes leave through a short-lived branch + PR).
 *
 * Blocks (exit 2) a `git push` or `gh pr create` when Hub pages changed since
 * origin/master and the last CLEAN gate run of the UI sweep does not match the
 * pages as they stand. The marker is service/web/test-results/ui-sweep/
 * last-clean.json, written by e2e/ui-sweep.e2e.ts in gate mode.
 *
 * The sweep needs no Docker stack (its dev server runs on sample data), so the
 * gate can always be satisfied on this machine: run it, and push again.
 * Adapted from venyu's stop-ui-sweep-gate.ts, 2026-09-25: there the gate is
 * the Stop hook on a clean feature branch; here uncommitted older work is
 * normal, so the push is the reliable "handing back" moment.
 */
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'

const input = await Bun.stdin.text()
let command = ''
try {
  command = String(JSON.parse(input)?.tool_input?.command ?? '')
} catch {
  process.exit(0)
}
if (!/\bgit\s+push\b|\bgh\s+pr\s+create\b/.test(command)) process.exit(0)

function git(args: string[], stdin?: string): string {
  const run = Bun.spawnSync(['git', ...args], {
    stdin: stdin === undefined ? undefined : new TextEncoder().encode(stdin),
    stdout: 'pipe',
    stderr: 'pipe'
  })
  return run.exitCode === 0 ? new TextDecoder().decode(run.stdout).trim() : ''
}

// Hub page changes that would leave with this push/PR.
const touched = git(['diff', '--name-only', 'origin/master...HEAD', '--', 'service/web/app'])
if (!touched) process.exit(0)

/**
 * A hash of the CONTENT of every file under service/web/app as it stands on
 * disk, tracked or not. Must stay byte-identical to treeKey() in
 * service/web/e2e/ui-sweep.e2e.ts, or the gate never opens.
 */
function treeKey(): string {
  const files = git(['ls-files', '-co', '--exclude-standard', '--', 'service/web/app'])
    .split('\n')
    .filter(file => file && existsSync(file))
  const hashes = git(['hash-object', '--stdin-paths'], `${files.join('\n')}\n`).split('\n')
  const manifest = files.map((file, i) => `${file}:${hashes[i] ?? ''}`).join('\n')
  return createHash('sha1').update(manifest).digest('hex')
}

const markerPath = 'service/web/test-results/ui-sweep/last-clean.json'
let marker: { key?: string, at?: string } = {}
if (existsSync(markerPath)) {
  try {
    marker = JSON.parse(readFileSync(markerPath, 'utf8'))
  } catch {
    marker = {}
  }
}
if (marker.key === treeKey()) process.exit(0)

const count = touched.split('\n').length
console.error(
  `[Hub UI sweep gate: ${count} file(s) under service/web/app changed since origin/master, `
  + 'and no clean sweep matches the pages as they are now]\n'
  + 'Run the rendered UI gate, fix what it finds, then push again:\n'
  + '  cd service/web && bun run ui:gate\n'
  + '(every page, three widths, both themes, every data state; writes '
  + `${markerPath} when clean). For the full survey with screenshots, use /ui-sweep.`
)
process.exit(2)
