---
name: "ui-sweep"
description: "Render every Noktah Hub page at every Tailwind breakpoint in light and dark, in every data state (full, empty, office PC down), and with the phone menu open; report overflow, clipped or crumpled text and contrast failures; fix them by the Hub's rules; leave the marker the push gate checks. Run after any change under service/web/app, and before pushing or opening a PR that touches it."
argument-hint: "Optional: a page slug or a concern to focus on (e.g. 'home dark', 'empty state', 'phone')"
metadata:
  author: "noktah-dashboard (adapted from venyu, 2026-09-25)"
  origin: "venyu's 2026-09-17 dashboard audit; first Hub run found failing badge/alert contrast in light mode, a hidden Status column at 360px, and an empty mobile menu"
user-invocable: true
---

## User Input

```text
$ARGUMENTS
```

If the text names a page or a concern, narrow the survey to it and say so. Otherwise sweep everything.

## What this is

`/ui-sweep` is the rendered half of the Hub's UI gate. The two halves:

- **Static half:** `service/web/tests/ui-conventions.test.ts`. It runs on every edit under `service/web/app` through the post-edit hook (`scripts/claude-hooks/post-edit-ui-gate.ts`) and takes about a second.
- **Rendered half:** this skill. It needs a browser. It opens every page listed in `service/web/e2e/ui-sweep.e2e.ts` and checks each one:
  - at widths 360 / 640 / 768 / 1024 / 1280 / 1536
  - in light and dark
  - in each **data state**: *full* (long names, many rows), *empty*, and *down* (office PC off)
  - with the phone menu or sidebar states, once those exist

  At each combination it runs the in-page checks in `service/web/e2e/ui-sweep/audit.ts`:

| Check | What it catches |
|---|---|
| **overflow** | content spilling past a box that does not scroll, such as a table wider than a phone or a button row leaving the screen |
| **clipped** | text cut by `overflow: hidden` with no ellipsis to show the cut is deliberate |
| **crumpled** | a short label wrapped into a tall column because its box is too narrow |
| **contrast** | text under 4.5:1 (3:1 for large text) and icons under 3:1 against the background they actually sit on |

The checks find *broken*; only your eyes find *ugly*. On the first Hub sweep the checks passed while the phone screenshot showed the Status column pushed off-screen and a menu button opening an empty panel. **Step 3 is not optional.**

**No login, no Docker stack.** The Hub's real login is Cloudflare Access, which a local browser can't pass. So the sweep's dev server runs in **fixture mode** (`NUXT_HUB_FIXTURES=1`, `service/web/server/fixtures/`), serving sample data in each data state. Fixture code exists only on the dev server; production builds strip it.

## Step 1: Preconditions

1. Work from a properly-cased path (`C:/…`, not `c:/…`); Vite compares drive letters case-sensitively.
2. Chromium for Playwright is installed: `cd service/web && bunx playwright install chromium` (once per machine).
3. Every page the change touched or added is listed in `ROUTES` in `service/web/e2e/ui-sweep.e2e.ts`, with the data states it can be in. **A page that isn't listed is a page nobody checked.** Pages that call a new API path need fixture responses in `server/fixtures/index.ts` for each state.

Playwright starts the fixture dev server itself, on port 3100, so it never mixes with a `bun run dev` you have open on 3000.

## Step 2: Survey

```bash
cd service/web && bun run ui:sweep
```

This runs all six widths with full-page screenshots in soft mode, which records findings without failing. Then read `service/web/test-results/ui-sweep/report.md`: one section per page, one subsection per failing combination.

To narrow the run: `bunx playwright test e2e/ui-sweep.e2e.ts -g "home"`.

## Step 3: Look

Screenshots land in `service/web/test-results/ui-sweep/shots/<theme>/<width>/<page>[.<data-state>][.drawer|.collapsed].png`. Open (Read) at least:

- every combination the report flags
- `light/xl` and `dark/xs` for every page you touched, findings or not
- the `.empty` and `.down` variants at `xs` for every page you touched
- any dialog, slideover, dropdown or tooltip your change opens. The sweep doesn't open them, so open them yourself with a short Playwright script (fixture mode, both themes) and screenshot them.

While looking, check these **states**: light / dark · each width · full / empty / down / loading · long names and many rows · an open modal, slideover, dropdown or tooltip · disabled and busy controls · keyboard focus rings · **Bahasa everywhere** (no English leftovers from components).

## Step 4: Fix by the rules

Never fix a symptom with a one-off class. Every finding maps to a rule; fix it at the rule's level.

| Finding | Rule | Do |
|---|---|---|
| faint text, badge or button in light mode | accents use readable shades in light mode | `app/assets/css/main.css` sets `--ui-primary`, `--ui-success`, … to darker steps under `:root:not(.dark)` (green: 800, others: 700, measured by the sweep). A new accent that fails is fixed there, not per component. |
| invisible or wrong colour in dark mode | colours are Nuxt UI **semantic** classes | `text-muted`, `text-highlighted`, `text-dimmed`, `bg-default`, `bg-elevated`, `bg-muted`, `border-default`, `text-primary`, `text-error` …, never palette steps (`text-gray-500`), which ignore dark mode. The static test names offenders. |
| wider than the phone | wide content wraps, or scrolls **inside its own box** | Let text cells wrap (`meta.class.td: 'whitespace-normal'` on the column); keep short badges `w-px`. Stack columns below `sm`. `min-w-0` on flex children that truncate. Horizontal scroll only for truly wide data, never for a two-column list. |
| a raw `<button>` / `<input>` / `<select>` / `<table>` | controls are **Nuxt UI's** | `UButton`, `UInput`, `USelect`, `UTextarea`, `UTable`, `UFormField`. They carry the theme, sizes and focus ring. |
| English text ("No data", "Close") | Nuxt UI speaks **Bahasa** | `<UApp :locale="id">` in `app.vue` (the static test checks it). Your own text is written in Bahasa; set explicit props like `empty="Belum ada klien."` where the default is generic. |
| a control that leads nowhere (empty menu, dead button) | show a control only when it does something | e.g. `<UHeader :toggle="false">` until the Hub has navigation |
| literal colour or px | theme lives in `app.config.ts` and `main.css` | the static test names it |

## Step 5: Gate

```bash
cd service/web && bun run test:ui && bun run ui:gate
```

`ui:gate` is gate mode: three widths, both themes, every data state, and it fails on findings. When it is clean it writes `service/web/test-results/ui-sweep/last-clean.json`, a fingerprint of the `service/web/app` files. The push gate (`scripts/claude-hooks/pre-push-ui-sweep-gate.ts`) refuses a `git push` or `gh pr create` that carries Hub page changes while that fingerprint is stale.

## Step 6: Report

Say what was found, per rule, with the file and line; what you changed; and which screenshots you looked at. Use short sentences. If something is left, name it and say why.
