/**
 * The in-page half of the UI sweep — runs INSIDE the browser via
 * `page.evaluate(auditDocument, options)`, so it must stay self-contained:
 * no imports, no closures over the test file, plain DOM and CSSOM only.
 *
 * It answers four questions about the rendered page as it stands:
 *
 *   1. **overflow** — does anything spill sideways out of a box that does not
 *      scroll? (A page that pushes wider than the viewport, a table that
 *      escapes its card, a button row that leaves the screen.)
 *   2. **clipped** — is any text cut off by `overflow: hidden` WITHOUT the
 *      ellipsis that says the cut is deliberate?
 *   3. **crumpled** — has a short string been forced to wrap into a tall
 *      column because its box is too narrow? (The collapsed-sidebar failure:
 *      "Schedule & catalogue" rendered one word per line in a 64px rail.)
 *   4. **contrast** — does every piece of visible text, and every icon, clear
 *      WCAG AA against the background it actually sits on — walking up
 *      through transparent ancestors, compositing alpha, and reading a
 *      `::before` layer where Nuxt UI paints one (menu and nav highlights)?
 *
 * The checks are heuristics with deliberate blind spots (gradients,
 * background images, text over images) and they say so with `unknown`
 * rather than guessing.
 */
export interface AuditFinding {
  kind: 'overflow' | 'clipped' | 'crumpled' | 'contrast'
  /** A short CSS-ish path, enough to find the element in the source. */
  where: string
  /** The first few words, for a human reading the report. */
  text: string
  detail: string
  /** True for states WCAG exempts (disabled controls) — reported, never gating. */
  exempt?: boolean
}

export interface AuditOptions {
  /** Minimum contrast for ordinary text. WCAG AA = 4.5. */
  textRatio: number
  /** Minimum contrast for large text (≥24px, or ≥18.66px bold). WCAG AA = 3. */
  largeTextRatio: number
  /** Minimum contrast for icons and other non-text marks. WCAG AA = 3. */
  iconRatio: number
  /** Cap per kind so one broken list does not drown the report. */
  limit: number
}

export function auditDocument(options: AuditOptions): AuditFinding[] {
  const findings: AuditFinding[] = []
  const counts: Record<string, number> = {}

  function push(finding: AuditFinding) {
    counts[finding.kind] = (counts[finding.kind] ?? 0) + 1
    if (counts[finding.kind]! <= options.limit) findings.push(finding)
  }

  // ── Naming ─────────────────────────────────────────────────────────────
  function describe(el: Element): string {
    const parts: string[] = []
    let node: Element | null = el
    for (let depth = 0; node && depth < 3; depth += 1) {
      let name = node.tagName.toLowerCase()
      if (node.id) name += `#${node.id}`
      const slot = node.getAttribute('data-slot')
      if (slot) name += `[${slot}]`
      const classes = [...node.classList].filter(c => !/^(?:group|relative|flex|items-|gap-|w-|h-|min-|max-|shrink|grow|text-|font-|p[xytblr]?-|m[xytblr]?-|rounded|border|bg-|ring|size-)/.test(c)).slice(0, 2)
      if (classes.length) name += `.${classes.join('.')}`
      parts.unshift(name)
      node = node.parentElement
    }
    return parts.join(' > ')
  }

  function ownText(el: Element): string {
    let text = ''
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) text += child.textContent ?? ''
    }
    return text.replace(/\s+/g, ' ').trim()
  }

  function snippet(text: string): string {
    return text.length > 48 ? `${text.slice(0, 45)}…` : text
  }

  // ── Visibility ─────────────────────────────────────────────────────────
  function isRendered(el: Element): boolean {
    const style = getComputedStyle(el)
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false
    const rect = el.getBoundingClientRect()
    // A 1px box is the screen-reader-only idiom (clip + 1px), not a layout.
    if (rect.width <= 1 || rect.height <= 1) return false
    // Off-canvas (a closed drawer, a slideover parked off-screen).
    if (rect.right < 0 || rect.bottom < 0 || rect.left > innerWidth || rect.top > innerHeight + document.documentElement.scrollHeight) return false
    return true
  }

  function isHiddenFromAll(el: Element): boolean {
    let node: Element | null = el
    while (node) {
      if (node.getAttribute('aria-hidden') === 'true' || node.hasAttribute('inert')) return true
      const style = getComputedStyle(node)
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return true
      node = node.parentElement
    }
    return false
  }

  function isExempt(el: Element): boolean {
    let node: Element | null = el
    while (node) {
      if (node.hasAttribute('disabled') || node.getAttribute('aria-disabled') === 'true') return true
      node = node.parentElement
    }
    return false
  }

  // ── Colour ─────────────────────────────────────────────────────────────
  type Rgba = [number, number, number, number]

  // Nuxt UI / Tailwind v4 colours compute as `oklch(...)` (and sometimes
  // `color(srgb ...)`), which the rgb regex below cannot read. Unparsed, a dark
  // page background was skipped and the walk fell through to "the canvas is
  // white", reporting every light-on-dark text as 1:1. The browser converts
  // any CSS colour by painting it into a 1px canvas. (Noktah Hub, 2026-09-25.)
  const probe = document.createElement('canvas').getContext('2d', { willReadFrequently: true })
  const cache = new Map<string, Rgba | null>()

  function viaCanvas(value: string): Rgba | null {
    if (!probe) return null
    if (cache.has(value)) return cache.get(value)!
    probe.clearRect(0, 0, 1, 1)
    probe.fillStyle = '#000'
    probe.fillStyle = value
    probe.fillRect(0, 0, 1, 1)
    const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data
    const rgba: Rgba | null = a === undefined ? null : [r!, g!, b!, a / 255]
    cache.set(value, rgba)
    return rgba
  }

  function parseColor(value: string): Rgba | null {
    if (!value || value === 'transparent') return [0, 0, 0, 0]
    const m = value.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+%?))?\s*\)$/)
    if (!m) return viaCanvas(value)
    let alpha = 1
    if (m[4] !== undefined) alpha = m[4].endsWith('%') ? Number(m[4].slice(0, -1)) / 100 : Number(m[4])
    return [Number(m[1]), Number(m[2]), Number(m[3]), alpha]
  }

  function over(top: Rgba, bottom: Rgba): Rgba {
    const a = top[3] + bottom[3] * (1 - top[3])
    if (a === 0) return [0, 0, 0, 0]
    const mix = (i: 0 | 1 | 2) => (top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / a
    return [mix(0), mix(1), mix(2), a]
  }

  function luminance([r, g, b]: Rgba): number {
    const chan = (c: number) => {
      const s = c / 255
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
    }
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)
  }

  function contrast(a: Rgba, b: Rgba): number {
    const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x)
    return (l1! + 0.05) / (l2! + 0.05)
  }

  /**
   * The colour actually behind an element: walks up, compositing every
   * partially transparent layer, and reads a `::before` layer when one is
   * painted over the box (Nuxt UI's highlight idiom). `null` when a
   * background image or gradient makes the answer unknowable here.
   */
  function backgroundBehind(el: Element): Rgba | null {
    let stack: Rgba[] = []
    let node: Element | null = el
    while (node) {
      const style = getComputedStyle(node)
      if (style.backgroundImage && style.backgroundImage !== 'none') return null
      const before = getComputedStyle(node, '::before')
      if (before.content && before.content !== 'none' && before.content !== 'normal') {
        const layer = parseColor(before.backgroundColor)
        if (layer && layer[3] > 0 && before.position === 'absolute') stack.push(layer)
      }
      const own = parseColor(style.backgroundColor)
      if (own && own[3] > 0) {
        stack.push(own)
        if (own[3] >= 1) break
      }
      node = node.parentElement
    }
    // Nothing opaque all the way up: the canvas is white.
    let result: Rgba = [255, 255, 255, 1]
    stack = stack.reverse()
    for (const layer of stack) result = over(layer, result)
    return result
  }

  function isLargeText(style: CSSStyleDeclaration): boolean {
    const size = parseFloat(style.fontSize)
    const weight = Number(style.fontWeight) || (style.fontWeight === 'bold' ? 700 : 400)
    return size >= 24 || (size >= 18.66 && weight >= 700)
  }

  // ── Sweep ──────────────────────────────────────────────────────────────
  const all = document.body.querySelectorAll<HTMLElement>('*')
  const scrollsX = (style: CSSStyleDeclaration) => style.overflowX === 'auto' || style.overflowX === 'scroll'

  for (const el of all) {
    const tag = el.tagName.toLowerCase()
    if (tag === 'script' || tag === 'style' || tag === 'option' || tag === 'noscript') continue
    if (!isRendered(el) || isHiddenFromAll(el)) continue

    const style = getComputedStyle(el)
    const text = ownText(el)
    const rect = el.getBoundingClientRect()

    // 1. Overflow past a box that does not scroll. Skips the html/body pair
    //    (their own scrollWidth is the document) and anything already
    //    trimmed with an ellipsis, which is a deliberate cut.
    // An ellipsis is a deliberate cut — unless the box is so narrow that
    // nothing of the label survives it ("R…" for "Rentals" in a 64px rail).
    const ellipsis = style.textOverflow === 'ellipsis'
    if (text && ellipsis && el.scrollWidth > el.clientWidth + 2 && el.clientWidth < parseFloat(style.fontSize) * 3) {
      push({
        kind: 'clipped',
        where: describe(el),
        text: snippet(text),
        detail: `label truncated to almost nothing: ${el.clientWidth}px for "${text}"`
      })
    }
    if (el.scrollWidth > el.clientWidth + 2 && !scrollsX(style) && !ellipsis && el.clientWidth > 0) {
      const hidden = style.overflowX === 'hidden' || style.overflowX === 'clip'
      const kind = hidden ? 'clipped' : 'overflow'
      // A visible-overflow box whose content leaves the VIEWPORT is the case
      // a person sees; one that overflows within the page but stays inside
      // its parent is noise more often than not.
      const escapes = rect.left + el.scrollWidth > innerWidth + 1
      if (hidden || escapes) {
        push({
          kind,
          where: describe(el),
          text: snippet(text || el.textContent?.trim() || ''),
          detail: `${hidden ? 'hidden' : 'visible'} overflow: content ${el.scrollWidth}px in a ${el.clientWidth}px box`
        })
      }
    }

    // 1b. Unreachable overflow: a scrolling box whose content starts LEFT of
    //     its own left edge while scrolled to 0. Scrolling only reaches the
    //     right-hand overflow, so that part is cut off for good — what a
    //     centred (`items-center`) tab row wider than a phone does.
    if (scrollsX(style) && el.scrollWidth > el.clientWidth + 2 && el.scrollLeft === 0) {
      const edge = rect.left + parseFloat(style.borderLeftWidth || '0')
      for (const child of Array.from(el.children)) {
        const left = child.getBoundingClientRect().left
        if (isRendered(child) && left < edge - 2) {
          push({
            kind: 'clipped',
            where: describe(child),
            text: snippet(child.textContent?.trim() || ''),
            detail: `starts ${Math.round(edge - left)}px left of a scrolling box, out of scroll reach`
          })
          break
        }
      }
    }

    // 2. Vertical clipping of text: a box with overflow hidden that is
    //    shorter than what it holds, when it holds text of its own.
    if (text && (style.overflowY === 'hidden' || style.overflowY === 'clip') && el.scrollHeight > el.clientHeight + 2 && style.textOverflow !== 'ellipsis') {
      push({
        kind: 'clipped',
        where: describe(el),
        text: snippet(text),
        detail: `text ${el.scrollHeight}px tall in a ${el.clientHeight}px box`
      })
    }

    // 3. Crumpled: a short string forced into many lines. A 64px rail turns
    //    "Schedule & catalogue" into five lines; nothing under 40 characters
    //    should ever need more than three.
    if (text && text.length <= 40 && style.whiteSpace !== 'nowrap' && el.children.length === 0) {
      const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.3
      const lines = Math.round(rect.height / lineHeight)
      const words = text.split(' ').length
      // Either more lines than words (a word broke mid-way), or every word
      // on its own line inside a box narrower than a short phrase.
      if (lines > Math.max(3, words) || (words >= 2 && lines >= words && rect.width < 96)) {
        push({
          kind: 'crumpled',
          where: describe(el),
          text: snippet(text),
          detail: `${text.length} characters wrapped onto ${lines} lines in a ${Math.round(rect.width)}px box`
        })
      }
    }

    // 4a. Text contrast, on the element's own text nodes.
    if (text) {
      const fg = parseColor(style.color)
      const bg = backgroundBehind(el)
      if (fg && bg) {
        const colour = fg[3] < 1 ? over(fg, bg) : fg
        const ratio = contrast(colour, bg)
        const needed = isLargeText(style) ? options.largeTextRatio : options.textRatio
        if (ratio < needed) {
          push({
            kind: 'contrast',
            where: describe(el),
            text: snippet(text),
            detail: `${ratio.toFixed(2)}:1 (needs ${needed}:1) — ${style.color} on rgb(${bg.slice(0, 3).map(Math.round).join(', ')})`,
            exempt: isExempt(el)
          })
        }
      }
    }

    // 4b. Icon contrast. Nuxt UI draws icons as a masked span whose
    //     `background-color` is `currentColor`, so the colour is `color`.
    if (el.classList.contains('iconify') || tag === 'svg') {
      const fg = parseColor(style.color)
      const bg = el.parentElement ? backgroundBehind(el.parentElement) : null
      if (fg && bg) {
        const colour = fg[3] < 1 ? over(fg, bg) : fg
        const ratio = contrast(colour, bg)
        if (ratio < options.iconRatio) {
          push({
            kind: 'contrast',
            where: describe(el),
            text: `(icon ${[...el.classList].find(c => c.startsWith('i-')) ?? tag})`,
            detail: `${ratio.toFixed(2)}:1 (icon needs ${options.iconRatio}:1) — ${style.color} on rgb(${bg.slice(0, 3).map(Math.round).join(', ')})`,
            exempt: isExempt(el)
          })
        }
      }
    }
  }

  for (const [kind, n] of Object.entries(counts)) {
    if (n > options.limit) {
      findings.push({
        kind: kind as AuditFinding['kind'],
        where: '(report)',
        text: '',
        detail: `${n - options.limit} more ${kind} findings not listed`
      })
    }
  }

  return findings
}

/**
 * Relax the desk shell so a full-page screenshot captures the whole page.
 *
 * `UDashboardGroup` is `fixed inset-0 overflow-hidden` and the panel body is
 * the thing that scrolls, so Playwright's `fullPage` would otherwise capture
 * one viewport's worth. Returns an undo function.
 */
export function unfixShellForScreenshot(): void {
  const group = document.querySelector<HTMLElement>('.fixed.inset-0.overflow-hidden')
  if (!group) return
  group.setAttribute('data-ui-sweep-unfixed', group.getAttribute('style') ?? '')
  group.style.position = 'static'
  group.style.overflow = 'visible'
  group.style.height = 'auto'
  for (const body of group.querySelectorAll<HTMLElement>('[data-slot="body"]')) {
    body.setAttribute('data-ui-sweep-unfixed', body.getAttribute('style') ?? '')
    body.style.overflow = 'visible'
  }
}

export function refixShellAfterScreenshot(): void {
  for (const el of document.querySelectorAll<HTMLElement>('[data-ui-sweep-unfixed]')) {
    el.setAttribute('style', el.getAttribute('data-ui-sweep-unfixed') ?? '')
    el.removeAttribute('data-ui-sweep-unfixed')
  }
}
