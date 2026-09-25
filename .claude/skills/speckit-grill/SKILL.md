---
name: "speckit-grill"
description: "Interview the user about a change until the words and the hard decisions are settled — writing CONTEXT.md terms and ADRs the moment they resolve, and a decision ledger (grill.md) the spec must then trace row by row. Run before /speckit-specify when the plan is still fuzzy."
argument-hint: "The change to grill — or 'the repo' to settle vocabulary with no feature in mind"
compatibility: "Requires spec-kit project structure with .specify/ directory"
metadata:
  author: "noktah-dashboard (adapted from venyu, 2026-09-25)"
  source: "mattpocock/skills — grill-with-docs, grilling, domain-modeling (inlined and adapted)"
user-invocable: true
disable-model-invocation: true
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding. The text after `/speckit-grill` is the change to grill. If it is empty, ask for it in one line and stop.

## What this is

`/speckit-grill` is the head of the spec chain:

```text
/speckit-grill → /speckit-specify → /speckit-clarify → /speckit-plan → /speckit-tasks → /speckit-implement
```

It is an interview, run in **rounds**, that ends when you and the user share one understanding of the change. It is **stateful**: while it runs, it writes three kinds of file, each the moment something resolves — never batched at the end.

| What resolved | Where it lands | When |
|---|---|---|
| A **term**: Noktah's own word for a thing | `CONTEXT.md` (the glossary), inline | the moment the term resolves |
| A **decision** that is hard to reverse, surprising without context, AND a real trade-off | an ADR in `docs/adr/`, in this repo's format, plus its index line | when the user says yes to "record this as an ADR?" |
| **Every other decision** | `grill.md`, the ledger in the feature directory, one numbered row `G-n` each | the moment the user answers |

The third row is the one the original skill leaves in the conversation and loses. Here it is a file, and `/speckit-specify` must trace every `G-n` into the spec. Nothing settled in the interview exists only in the context window.

This file is deliberately **self-contained**. The upstream skill is a one-line delegation to two other skills (`grilling`, `domain-modeling`) and its most-reported failure is loading one without the other — an undifferentiated question dump with no paper trail. Both disciplines are inlined below. Do not delegate to any other skill to run the interview.

## Step 1 — Setup (silent; do this before the first question)

1. Read `.specify/memory/constitution.md` and `.claude/CLAUDE.md` (with the `.claude/rules/backend/*.md` files it points to). A question whose answer those already fix is **not a question** (flows return their errors and never raise; AI output is validated, retried once with the error, then quarantined, and never stored unvalidated; observations are append-only; the Hub web app never touches the database or OpenRouter, and `hub-api` is the only writer of company data). State the rule as settled and move on. A change that needs a principle changed is an ADR first (and a constitution amendment); say so.
2. Read `CONTEXT.md` if it exists. This is the vocabulary you challenge the user against. If it does not exist, the de-facto vocabulary is `.claude/CLAUDE.md`, the `.claude/rules/backend/*.md` files and the **Key Entities** sections of the specs in `specs/`; treat those as the glossary until `CONTEXT.md` is created (Step 3).
3. Read `docs/DEFERRED.md`. Every row whose **Target** names this change's scope (or says `any`) is a frontier question of the first round: *claim or re-defer?* The user decides; the answer is a ledger row.
4. Read the parts of `.claude/CLAUDE.md`, the rules files, the ADRs in `docs/adr/` (if any), and the existing specs that the change touches. Note which spec **owns** each behaviour the change would alter. A shipped spec is extended by a new spec (or a v(N+1)), not rewritten, so the history of why things were built stays readable. A spec still in draft (no plan yet) may be revised in place; see *Grilling a drafted spec* below.
5. Decide the **mode**:
   - **Feature mode** (default): the input describes a change. The ledger goes in a feature directory (Step 3).
   - **Repo mode**: the input is about the repo's vocabulary as a whole ("the repo", "help me document the vocabulary", no feature named). No feature directory, no ledger — only `CONTEXT.md` and, rarely, ADRs. Read the code and the ratified docs, and ask the user which of the words already in use are the right ones.
6. Check the working directory is writable and note `git status --porcelain` — you will diff against it at the end to prove the files moved.

Do not narrate this step. The user's first message from you is the first round.

## Step 2 — The interview

Map the change as a **design tree**: every decision branches into the decisions that hang off it. The **frontier** is every decision whose prerequisites are already settled — the questions you can ask *now* without guessing at answers you have not heard. Ask the **whole frontier in one round**, number each question, and give your recommended answer. Then **wait** for the user's answers before the next round.

Format a round exactly like this:

```text
❓ **Q1** - **<question title>**: <question body — may be several paragraphs, may offer choices>

➡️ <your recommended answer, one or two sentences, with the reason>

---

❓ **Q2** - **<question title>**: <question body>

➡️ <your recommended answer>
```

Rules of the interview:

- **Plain language.** The user here is the business owner, not a reader of code. Ask about outcomes and choices in plain words; keep table names, routes and code mechanics in the ledger's *Facts looked up* section, not in the questions.

- **The boundary is always in the first round.** In feature mode, Q1 of round 1 is: *which spec owns this — a new spec, or v(N+1) of spec NNN — and what does it deliberately not include?* Recommend an answer from Step 1.4. Its answer is ledger row `G-1`.
- **Each round reshapes the tree.** Settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier after every round. A question whose answer depends on another question still open in *this* round belongs to a *later* round.
- **Facts are your job; decisions are the user's.** Never ask the user for anything the repo can answer. When a frontier question needs a fact from the codebase (does the table exist, what does the route return, which spec defines it), look it up — with Grep/Read directly or an `Explore` agent for a broad sweep — and do not block on it: ask the rest of the frontier now, and ask the questions downstream of the lookup next round.
- **Challenge against the glossary.** When the user uses a word that conflicts with `CONTEXT.md` (or the de-facto vocabulary), call it out immediately: *"The glossary defines a **Client** as one branch; you seem to mean the whole SMEC group. Which is it?"*
- **Sharpen fuzzy language.** When a word is vague or overloaded, propose the precise canonical term: *"You said 'account': do you mean a social account (Instagram/TikTok) or a person's login? Those are different things here."*
- **Stress-test with concrete scenarios.** When relationships between concepts are being discussed, invent specific edge cases that force the boundary: one competitor account shared by two clients, a price that changes mid-month, a person who leaves while assigned to three clients.
- **Cross-reference with code.** When the user states how something works, check whether the code agrees. Surface every contradiction: *"The harvest reads its targets from `prefect.yaml`, but you just said it follows the registry. Which is right?"*
- **Precision is kept.** Numbers, ordering guarantees, and negative requirements ("never", "must not", "exactly one") are recorded **verbatim** in the ledger. Do not soften an answer when you write it down.
- **No question cap.** `/speckit-clarify` has one (five); this skill does not. The session ends when the frontier is empty, not when a quota is hit. Keep rounds tight — four to seven questions is typical; a fifteen-question round means the tree was not pruned.
- **Never act on the design.** This skill writes glossary terms, ADRs and the ledger. It writes no code, no spec, no plan.

## Step 3 — Writing as you go

### 3a. Terms → `CONTEXT.md`

When a term resolves, write it to `CONTEXT.md` **right then**, in this format:

```md
**Term**:
One or two sentences: what it IS, not what it does.
_Avoid_: the other words people reach for
```

- One `CONTEXT.md` at the repo root (this repo is one bounded context: Eskala's agency operations and the automation and Hub that serve them). If a `CONTEXT-MAP.md` ever exists at the root, it names the contexts and where each `CONTEXT.md` lives; infer which one the term belongs to, and ask if unclear.
- Create the file lazily — on the first resolved term — with a title, a one-or-two-sentence description of the context, and a `## Language` section. Group terms under subheadings when natural clusters exist.
- **Be opinionated.** When several words exist for one concept, pick the best and list the rest under `_Avoid_`.
- **Only project-specific terms.** General programming concepts (timeout, retry, cursor) do not belong, however often the code uses them. A term may be in Bahasa when that is the word the team uses (e.g. **Content Plan**, **Bentuk**).
- **No implementation detail, ever.** `CONTEXT.md` is a glossary and nothing else: not a spec, not a scratch pad, not a list of decisions. Table names, routes and field names do not appear in it.
- A term already in the glossary that the session **sharpens** is edited in place — the glossary is the one document here that is not append-only.

### 3b. Decisions that pass the three gates → an ADR

Offer an ADR only when **all three** hold:

1. **Hard to reverse** — changing your mind later costs something real.
2. **Surprising without context** — a future reader will look at the code and wonder "why on earth did they do it this way?"
3. **A real trade-off** — there were genuine alternatives and one was picked for specific reasons.

If any is missing, skip it: an easy-to-reverse decision will just be reversed; an unsurprising one needs no explanation; a decision with no alternative is "we did the obvious thing". Most sessions produce **zero** ADRs. That is working as designed.

When all three hold, ask: *"This passes the three gates. Record it as ADR-00NN, «title»?"* On yes, write it **now**:

- File: `docs/adr/NNNN-slug.md`, where `NNNN` is the highest existing number plus one (scan `docs/adr/`; the first ADR is `0001`). Create `docs/adr/` lazily with the first one.
- Shape (MADR-lite): `# NNNN. Title`, then `- Status: Accepted`, `- Date: YYYY-MM-DD`, `- Deciders: Noktah core team`, `- See also:` links to the ADRs, specs or rules files it touches; then `## Context`, `## Decision`, `## Consequences` (Positive / Negative or Costs / Neutral), `## Alternatives considered`.
- Add its line to the index in `docs/adr/README.md` (create it with the first ADR) under the right heading (Platform & structure · Data & automation · Hub · Cross-cutting), with a one-line italic gloss.
- If it reverses or redefines an accepted ADR, it **supersedes** it: say so in the new ADR and change only the old ADR's status line. If it extends one without contradiction, append a dated **Amendment** section to the old ADR instead of writing a new one. Never edit what an accepted ADR decided or why.
- When it changes how the system is put together, add a one-line pointer to it from `.claude/CLAUDE.md` in the relevant section.

A structural decision the user makes in the interview and declines to record is still written to the ledger, with "ADR declined" and the reason, so the next reader knows it was offered.

### 3c. Everything else → `grill.md`, the ledger

Every answered question that is not a term and not an ADR is a **ledger row**. Write it the moment the user answers.

**Location.** Feature mode only. On the first row:

- Allocate the feature directory the way `/speckit-specify` does: a 2–4 word short name from the input (action-noun, e.g. `hub-registry-client-card`, `songbird-batch-plan`), the next 3-digit number after scanning `specs/`, `specs/NNN-short-name/`. If the boundary answer (`G-1`) says this is v(N+1) of an existing spec, the short name carries the suffix (`client-card-v2`).
- Create `specs/NNN-short-name/grill.md` and persist `{"feature_directory": "specs/NNN-short-name"}` to `.specify/feature.json` — exactly what `/speckit-specify` will read to find the directory instead of allocating a new one.
- Do **not** create `spec.md`, and do not create a git branch (work happens on `master`; changes reach GitHub through pull requests the user merges).

**Grilling a drafted spec.** When `.specify/feature.json` names a directory that already has a `spec.md` but no `plan.md` and no `grill.md`, and the input is about that feature, grill *that* feature: write `grill.md` in the same directory, set **Owning spec** to it, and treat the draft spec as one more input to challenge (its decisions are frontier questions, not settled facts, unless the user confirms them). `/speckit-specify` then revises the draft in place to trace every `G-n`. A spec that already has a `plan.md` is not re-grilled; propose a v(N+1) instead.

**Format:**

```md
# Grill ledger: <Feature name>

**Session**: YYYY-MM-DD · **Status**: open
**Input**: "<the user's argument, verbatim>"
**Owning spec**: <new spec NNN | vN+1 of spec NNN> — see G-1

Every row below is traced into spec.md by /speckit-specify, once, as a
requirement, scenario, edge case or assumption — or dropped there with a
reason. Numbers, ordering and negatives are copied verbatim.

## Decisions

### G-1 — <question title>
- **Decision**: <the user's answer, precise; numbers, ordering, negatives verbatim>
- **Why**: <one line — the reason the user gave, or the recommendation they accepted>
- **Lands in**: <requirement | scenario | edge case | assumption | CONTEXT.md «Term» | ADR-00NN | ADR declined: reason>

### G-2 — …

## Deferred register rows in scope
- <id> — claimed (see G-n) | re-deferred: <new target, reason>

## Facts looked up (not decisions)
- <the codebase facts the session relied on, one line each, with the file or ADR>
```

- A row is appended, never rewritten. If the user changes their mind later in the session, add a new row that says "supersedes G-n" — the ledger keeps its history, like everything else in this repo.
- A DEFERRED row that was **claimed** stays in `docs/DEFERRED.md` for now; `/speckit-specify` deletes it when the spec claims it. A row **re-deferred** here is updated in `docs/DEFERRED.md` now, with the new target and reason.
- A term that resolves *and* is a decision gets both: the glossary entry and a ledger row whose "Lands in" says `CONTEXT.md «Term»`.

## Step 4 — Closing

The session is done when the **frontier is empty**: every branch of the design tree visited, nothing left silently assumed. Then:

1. Say, in a few lines, what you now believe the change is, and ask the user to confirm you have reached a shared understanding. Do not close until they do.
2. Mark the ledger `**Status**: closed` with the date.
3. **Prove the files moved.** Run `git status --porcelain` and compare with Step 1.6. List every file this session created or changed: `CONTEXT.md`, each ADR and the index, `grill.md`, `docs/DEFERRED.md`, `.specify/feature.json`. If the interview happened but no file changed, that is the known failure of this skill running inside an orchestration layer — say so plainly, and write the ledger from the conversation before reporting.
4. Report and stop. Do not run `/speckit-specify` yourself.

## Completion Report

Report to the user:

- **Mode** — feature (`specs/NNN-short-name/`) or repo
- **Ledger** — path, number of rows, how many are "Lands in: ADR" / "CONTEXT.md" / requirement-shaped
- **Glossary** — terms added, terms sharpened (name each)
- **ADRs** — written (number, title) or "none qualified" (the normal outcome)
- **Deferred register** — rows claimed / re-deferred
- **Files changed** — the list from Step 4.3
- **Next** — exactly one line: `/speckit-specify <the same input>` in **this same conversation**, so the spec is synthesised from a session that is still in context. If the change is small enough to skip a spec (constitution VIII minor-change exemption), say that instead, and why.

## It's working if

- `CONTEXT.md` changes *during* the session, term by term, not in one lump at the end.
- The glossary reads as pure vocabulary — Noktah's words with tight definitions — and contains no implementation detail or spec-like prose.
- Questions the codebase can answer got answered by reading the codebase, not asked of the user.
- Few or no ADRs came out, and the ones that did are decisions someone would be annoyed to re-litigate.
- A word the user used was challenged because the glossary defines it differently.
- Every answer is a numbered row in `grill.md`, and `/speckit-specify` can cite each one.

## Done When

- [ ] Frontier empty and the user has confirmed a shared understanding
- [ ] Every resolved term is in `CONTEXT.md`; every qualifying decision is an ADR with an index line; every other decision is a `G-n` row in `grill.md`
- [ ] `.specify/feature.json` points at the feature directory (feature mode)
- [ ] `git status` shows the files this report claims were written
- [ ] Completion reported with the exact next command
