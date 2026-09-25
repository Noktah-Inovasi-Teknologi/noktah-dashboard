/**
 * The static half of the Hub's UI gate: rules that can be checked by reading
 * the source, in about a second, after every edit under app/ (the post-edit
 * hook in scripts/claude-hooks/ runs this). The rendered half (overflow,
 * clipping, contrast at every width in both themes) is e2e/ui-sweep.e2e.ts.
 *
 * Each rule exists because breaking it breaks the page in a way no unit test
 * sees: a raw control misses Nuxt UI's focus ring, sizing and dark styling; a
 * palette colour (text-gray-500) doesn't switch in dark mode and turns
 * unreadable; a literal colour or pixel size escapes the theme entirely; an
 * untranslated component shows English ("No data") to a Bahasa team.
 *
 * Run: bun test tests/ui-conventions.test.ts
 */
import { describe, expect, test } from 'bun:test'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const APP = join(import.meta.dir, '..', 'app')

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? walk(path) : [path]
  })
}

const VUE = walk(APP).filter(f => f.endsWith('.vue'))

/** The <template> part of a .vue file, with line numbers preserved. */
function template(source: string): string {
  return source.replace(/<script[\s\S]*?<\/script>/g, m => m.replace(/[^\n]/g, ''))
    .replace(/<style[\s\S]*?<\/style>/g, m => m.replace(/[^\n]/g, ''))
}

function findAll(files: string[], pattern: RegExp, pick: (src: string) => string = s => s): string[] {
  const hits: string[] = []
  for (const file of files) {
    const lines = pick(readFileSync(file, 'utf8')).split('\n')
    lines.forEach((line, i) => {
      for (const m of line.matchAll(pattern)) hits.push(`${relative(APP, file)}:${i + 1}  ${m[0]}`)
    })
  }
  return hits
}

describe('Hub UI conventions', () => {
  test('controls are Nuxt UI components (UButton, UInput, USelect, UTextarea, UTable), never raw HTML', () => {
    expect(findAll(VUE, /<(button|input|select|textarea|table)[\s>]/g, template)).toEqual([])
  })

  test('colours are Nuxt UI semantic classes (text-muted, bg-elevated, text-primary …), never palette steps that ignore dark mode', () => {
    const palette = /\b(?:dark:)?(?:text|bg|border|ring|outline|divide|fill|stroke|from|via|to|decoration|placeholder)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|black|white)(?:-\d{2,3})?(?:\/\d+)?\b/g
    expect(findAll(VUE, palette, template)).toEqual([])
  })

  test('no literal colours or pixel sizes in components (theme lives in app.config.ts and assets/css)', () => {
    const literal = /#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|oklch)\(|\[\d+px\]/g
    expect(findAll(VUE, literal)).toEqual([])
  })

  test('Nuxt UI speaks Bahasa (UApp gets the id locale), so built-in strings are not English', () => {
    const app = readFileSync(join(APP, 'app.vue'), 'utf8')
    expect(app).toMatch(/<UApp[^>]*:locale="id"/)
  })
})
