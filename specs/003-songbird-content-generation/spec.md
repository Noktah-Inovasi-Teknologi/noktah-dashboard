# Feature Specification: Songbird — Targeted Content Generation

**Feature Branch**: `003-songbird-content-generation`

**Created**: 2026-07-15

**Status**: Draft

**Input**: User description: "Songbird — a targeted content-generation capability that produces a monthly content plan (scheduled) and on-demand incidental content (manual), biased toward audience 'hits' (not guaranteed) by learning from what already performs well, grounded in each client's knowledge base and informed by the client's own and competitors' harvested social content."

## Clarifications

### Session 2026-07-15

- Q: Where should the generation capability live and how is it triggered? → A: Inside the existing workflow-orchestration service. The monthly content plan runs on a schedule; incidental content is triggered manually. No new user-facing chatbot surface in this feature.
- Q: What signals feed the "hit" bias? → A: All four — the client's knowledge base, competitors' top-performing harvested content, the client's own top-performing harvested content, and explicit per-run marketing parameters.
- Q: Where does the generated monthly plan land? → A: Configurable. Default is a **draft** deliverable for human review; an operator may instead target the **live** content-plan sheet that the existing content-plan → issue-tracker workflow reads, so the plan flows onward automatically.
- Q: Should the system guarantee that generated content performs well? → A: No. "Hits" are a bias, explicitly **not guaranteed**. The system must surface this caveat and treat top performers as patterns to adapt, not templates to copy.
- Q: What happens when a client has no harvested signal yet? → A: Degrade gracefully — generate from the client's knowledge base and the run's marketing parameters alone, and note the reduced grounding in the run outcome.
- Q: What language should the generated content be in? → A: Primarily Indonesian (Bahasa Indonesia), but naturally code-mixed — English terms are retained where idiomatic (brand/proper names, hashtags, established loanwords like "reels"/"engagement", trending English phrases, and campaign taglines). Do NOT force-translate such terms into Bahasa; match how the client and its audience actually write.
- Q: What is the default monthly content quantity per client? → A: There is no single default — the number of content pieces varies per client and MUST be read from the existing per-client configuration in the Clients worksheet (spreadsheet id `1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY`), overridable per run.
- Q: What shape does on-demand incidental content take? → A: Standalone, draft-only ideas with no auto-distributed calendar dates and no live-worksheet handoff — the monthly plan is the only path that distributes dates and can target the live content-plan worksheet.
- Q: Over what time window are "top performers" ranked? → A: A rolling 180-day window (by published date), balancing freshness against enough signal volume for low-frequency accounts; configurable.
- Q: What columns does the draft deliverable carry? → A: The live content-plan columns PLUS reviewer-only rationale columns (adapted pattern, source exemplar reference, rationale, and the "hit not guaranteed" note); the rationale columns are dropped when promoting to the live worksheet.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Generate a monthly content plan for a client (Priority: P1)

Each month, without manual ideation, the marketing team receives a ready-to-review content plan for a
client: a set of dated content ideas, each with a topic, content form, purpose/theme, and a concrete
visualization/execution note. The ideas are grounded in the client's brand knowledge and biased toward
formats and hooks that have historically performed well for the client and its competitors.

**Why this priority**: This is the core reason the capability exists — turning accumulated client
knowledge and performance signal into a concrete, actionable monthly plan. Without it nothing else has
value. It is the MVP.

**Independent Test**: Trigger a monthly-plan run for one client and target month; confirm a reviewable
plan deliverable is produced containing the requested number of dated content ideas, each with at least a
topic, a publish date within the target month, and a content form.

**Acceptance Scenarios**:

1. **Given** a client with knowledge records and harvested performance signal, **When** an operator runs
   the monthly plan for a target month, **Then** a draft plan deliverable is produced with the requested
   number of content ideas, each carrying a topic, a publish date distributed within that month, a content
   form, and supporting creative fields.
2. **Given** the same client, **When** the plan is generated, **Then** each idea reflects the client's
   brand/voice/audience from its knowledge base rather than generic content.
3. **Given** competitors' and the client's own top-performing content exist as signal, **When** the plan
   is generated, **Then** the ideas adapt recurring winning patterns (hooks, formats, pacing) rather than
   copying specific posts, and the run outcome states that performance is not guaranteed.
4. **Given** a requested quantity of ideas, **When** generation completes, **Then** publish dates are
   spread across the target month rather than all assigned to one date.

---

### User Story 2 - Generate incidental content on demand (Priority: P1)

Between monthly cycles, an operator needs a few pieces of content immediately for a specific purpose (a
campaign, an event, a reactive moment). They trigger an on-demand run with targeting parameters (audience,
platform, goal, tone, pillars, quantity) and receive generated ideas in the same reviewable shape as the
monthly plan.

**Why this priority**: On-demand generation is a distinct, equally essential trigger path; the monthly
schedule cannot serve time-sensitive needs. It shares the same generation core, so it is cheap to deliver
alongside Story 1.

**Independent Test**: Trigger an on-demand run with a client, quantity, and platform; confirm the
requested number of standalone content ideas is produced in a draft deliverable, targeted to the supplied
parameters, with no calendar dates auto-assigned.

**Acceptance Scenarios**:

1. **Given** an operator supplies a client and marketing parameters, **When** they start an on-demand run,
   **Then** the requested number of standalone content ideas is generated and delivered to a draft for
   review.
2. **Given** marketing parameters (audience, platform, goal, tone, pillars), **When** content is
   generated, **Then** the output visibly reflects those parameters.
3. **Given** an on-demand run, **When** it completes, **Then** it produces the same standardized run
   outcome (start/end, produced items, summary, and any error) as the monthly plan, but does NOT
   auto-distribute publish dates and does NOT write to the live content-plan worksheet.

---

### User Story 3 - Hand the plan off to the production pipeline (Priority: P2)

Once a plan is trusted, the operator wants it to flow into the existing content-production pipeline
without re-keying. They choose a "live" target so generated ideas land directly in the content-plan
worksheet that the existing content-plan workflow reads, ready to become production tasks — while the
default remains a safe draft for review.

**Why this priority**: Delivers end-to-end automation value, but depends on Story 1 producing trustworthy
plans first, so it is secondary. The draft path (P1) is usable without it.

**Independent Test**: Run generation with the live target against a throwaway content-plan worksheet;
confirm the generated rows appear under the correct existing columns and are consumable by the existing
content-plan workflow without manual reformatting.

**Acceptance Scenarios**:

1. **Given** the default target, **When** a plan is generated, **Then** it is delivered as a separate
   draft for human review and nothing is written into the live content-plan worksheet.
2. **Given** an operator selects the live target, **When** the plan is generated, **Then** the ideas are
   appended to the existing content-plan worksheet aligned to its existing columns, so the downstream
   content-plan workflow can consume them unchanged.
3. **Given** either target, **When** rows are produced, **Then** each row includes at minimum the topic,
   the publish date, and the content form that the downstream workflow requires.

---

### User Story 4 - Capture performance signal during harvesting (Priority: P2)

For the "hit" bias to work, the system must accumulate a queryable record of how harvested content
performed. As the existing social-content harvest runs, each delivered item's engagement metrics and
content analysis are recorded to a performance signal store that generation can later rank and learn from.

**Why this priority**: This is the data foundation that makes Stories 1–2 "targeted" rather than generic.
Generation degrades gracefully without it, so it is an enabling P2 rather than a blocking P1.

**Independent Test**: Run a harvest over one client-owned and one competitor profile; confirm each
delivered item appears in the performance signal store with its engagement metrics and analysis, and can
be ranked by engagement for those handles.

**Acceptance Scenarios**:

1. **Given** a harvest run delivers content items, **When** each item is delivered, **Then** its
   engagement metrics (views/likes/comments), caption, and analysis (transcript/flow/summary) are recorded
   to the performance signal store keyed by profile handle and content id.
2. **Given** an item is re-harvested later, **When** it is recorded again, **Then** the stored signal is
   updated with the freshest metrics rather than duplicated.
3. **Given** a recording failure for one item, **When** it occurs, **Then** the harvest run still
   completes and delivers that item (signal capture is best-effort and never aborts a harvest).

---

### Edge Cases

- **No harvested signal for the client**: generation proceeds using the client's knowledge base and the
  run's marketing parameters only, and the run outcome notes the reduced grounding.
- **No client knowledge records**: generation proceeds from marketing parameters (and any signal), and the
  outcome notes that the client was not found in the knowledge base.
- **A single idea fails to generate or parse**: the run continues and delivers the remaining ideas,
  counting the failure in the summary, rather than aborting the whole run.
- **The target month is fully or partially in the past**: publish dates are still distributed across the
  requested month; the operator is responsible for choosing a sensible month.
- **The live content-plan worksheet has a different or reordered column set**: generated values are aligned
  to the worksheet's existing columns by name; unmapped generated fields are omitted rather than shifting
  columns.
- **The generation provider is unavailable or rate-limits**: transient failures are retried with back-off;
  a persistent failure ends the run with a clear error in the standard outcome, without a partial
  corrupt deliverable.
- **More ideas requested than can be responsibly dated in the month** (e.g. 100 in a 30-day month):
  multiple ideas may share dates, but the distribution remains spread rather than collapsed to one day.

## Requirements *(mandatory)*

### Functional Requirements

**Generation & targeting**

- **FR-001**: The system MUST generate a configurable number of content ideas for a specified client,
  each idea carrying at minimum a topic, a content form, and supporting creative fields (e.g. format,
  purpose/theme, strategic application, and a visualization/execution note).
- **FR-002**: The system MUST support a scheduled monthly-plan generation path and a manually-triggered
  on-demand generation path, both producing content ideas in the same standardized deliverable shape.
- **FR-003**: The system MUST accept per-run marketing parameters — at minimum client, platform, target
  audience, campaign goal, tone, content pillars, and quantity — and MUST visibly reflect them in the
  generated content.
- **FR-003a**: Generated content fields (topic, caption, hook, visualization/execution note, etc.) MUST be
  written primarily in Indonesian (Bahasa Indonesia), while naturally retaining English terms where
  idiomatic — brand/proper names, hashtags, established loanwords (e.g. "reels", "engagement"), trending
  English phrases, and campaign taglines. The system MUST NOT force-translate such terms into Bahasa, and
  SHOULD mirror the code-mixing style evident in the client's knowledge base and harvested content.
- **FR-003b**: The monthly content quantity per client MUST be sourced from the existing per-client
  configuration (the Clients worksheet) rather than a fixed global default; a per-run parameter MAY
  override it. When no configured quantity is found for a client, the run MUST fail fast with a clear
  message rather than guessing a count.
- **FR-004**: For the monthly plan, the system MUST distribute publish dates across the target month;
  publish dates MUST NOT be delegated to the content-generation model.
- **FR-005**: The system MUST frame historical top performers as patterns to adapt (hooks, formats,
  pacing) and MUST NOT reproduce specific harvested posts verbatim.
- **FR-006**: The system MUST include, in every run outcome, an explicit caveat that audience performance
  ("hits") is a bias and is not guaranteed.

**Hit signals**

- **FR-007**: The system MUST ground generation in the client's current knowledge (brand, voice, audience,
  subjects) drawn from the client knowledge base.
- **FR-008**: The system MUST incorporate the client's own top-performing harvested content as signal,
  ranked by engagement.
- **FR-009**: The system MUST incorporate competitors' top-performing harvested content as signal, ranked
  by engagement.
- **FR-009a**: Top-performer ranking MUST consider only content published within a rolling 180-day window
  (by published date) by default; the window MUST be configurable.
- **FR-010**: The system MUST resolve which profile handles are "own" versus "competitor" for a client
  from a maintained client→accounts mapping and/or per-run parameters.
- **FR-011**: When no harvested signal exists for the resolved handles, the system MUST still generate
  using knowledge and marketing parameters, and MUST record the reduced grounding in the run outcome.

**Performance signal capture**

- **FR-012**: The existing social-content harvest MUST record each delivered item's engagement metrics,
  caption/hashtags, and analysis (transcript, content flow, summary) to a performance signal store, keyed
  so it can be ranked by engagement per profile handle.
- **FR-013**: Signal capture MUST be idempotent per content item — re-harvesting an item updates its
  stored metrics/analysis rather than creating a duplicate.
- **FR-014**: Signal capture MUST be best-effort — a capture failure MUST NOT abort or fail the harvest
  run that already delivered the item.

**Handoff & delivery**

- **FR-015**: The monthly-plan path MUST support a configurable delivery target: a default **draft**
  deliverable for human review, and a **live** target that appends ideas into the existing content-plan
  worksheet consumed by the downstream content-plan workflow. The on-demand path is draft-only (no live
  target, no auto-distributed dates).
- **FR-016**: When targeting the live worksheet, the system MUST align generated values to the worksheet's
  existing columns by name and MUST NOT create duplicate or misaligned columns; generated fields with no
  matching column are omitted.
- **FR-016a**: The draft deliverable MUST carry the live content-plan columns PLUS reviewer-only rationale
  columns (at minimum: the winning pattern adapted, a source-exemplar reference, a rationale, and the "hit
  not guaranteed" note). These rationale columns MUST be excluded when promoting/writing to the live
  worksheet.
- **FR-017**: Generated rows MUST include the fields the downstream content-plan workflow requires (at
  minimum topic, publish date, and content form) so they are consumable without manual reformatting.
- **FR-018**: The system MUST NOT create issue-tracker items directly; handoff is via the content-plan
  worksheet only.

**Resilience & observability**

- **FR-019**: Each run MUST return a standardized outcome containing start time, end time, the produced
  content data, a summary (counts of ideas produced/failed, signal availability, target used), and an
  optional error; a run MUST NOT raise to its caller.
- **FR-020**: A failure generating or parsing a single idea MUST NOT abort the run; the run MUST continue
  and count the failure.
- **FR-021**: Transient generation-provider failures MUST be retried with back-off before the affected
  unit is counted as failed.
- **FR-022**: Both generation paths MUST be independently runnable for development/testing and MUST emit a
  human-readable summary of the run.

### Key Entities *(include if feature involves data)*

- **Content Idea**: A single generated unit of the plan. Attributes: topic, content form, format,
  purpose/theme, strategic application, visualization/execution note, and (for the monthly plan) an
  assigned publish date. Maps to one row in the plan deliverable.
- **Marketing Parameters**: The per-run targeting inputs — client, platform, audience, goal, tone, content
  pillars, quantity, and target month or date range. `quantity` defaults from the per-client configuration.
- **Client Configuration**: Per-client settings maintained by operators in the Clients worksheet
  (spreadsheet id `1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY`), including the monthly content quantity
  for that client. Read at run time to determine how many ideas to generate.
- **Client Knowledge**: The current, non-superseded facts about a client (brand, voice, audience,
  subjects) used to ground generation. Sourced from the existing client knowledge base.
- **Performance Signal**: A recorded harvested content item with its engagement metrics (views, likes,
  comments), caption/hashtags, analysis (transcript, content flow, summary), profile handle, platform, and
  content id. Rankable by engagement; the basis for the "hit" bias.
- **Client Accounts Mapping**: The association of a client to its own profile handles and its competitors'
  handles, used to select which performance signal informs a run.
- **Plan Deliverable**: The output surface for a run — either a draft workspace document for review or the
  live content-plan worksheet — holding one row per content idea. The draft carries the live content-plan
  columns plus reviewer-only rationale columns (adapted pattern, source-exemplar reference, rationale, "hit
  not guaranteed" note); the live worksheet receives only the content-plan columns.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a client with knowledge and signal, an operator can produce a reviewable monthly plan of
  the requested size in a single run, with 100% of ideas carrying a topic, an in-month publish date, and a
  content form.
- **SC-002**: An operator can produce on-demand content for a client in under 5 minutes of wall-clock time
  from trigger to reviewable deliverable for a typical small batch (e.g. up to 10 ideas).
- **SC-003**: When the live target is selected, 100% of generated rows land under the correct existing
  columns of the content-plan worksheet and are consumed by the downstream content-plan workflow with no
  manual reformatting.
- **SC-004**: Every run outcome includes the "not guaranteed" caveat and a summary reporting ideas
  produced, ideas failed, whether harvested signal was available, and the delivery target used.
- **SC-005**: A run for a client with no harvested signal still succeeds and produces the requested number
  of ideas, with the reduced-grounding condition recorded in the outcome.
- **SC-006**: After a harvest run, 100% of delivered items are retrievable from the performance signal
  store with their engagement metrics and analysis, and can be ranked by engagement for their handles.
- **SC-007**: A single failed idea reduces the delivered count by exactly one and never prevents the
  remaining ideas from being delivered.

## Assumptions

- The client knowledge base (feature 001) and the social-content harvest (feature 002) already exist and
  are the sources of client grounding and performance signal, respectively.
- Generation uses the project's existing house language-model provider; no new provider relationship is
  introduced by this feature.
- "Hit" is defined operationally as high engagement on harvested content. **Views (play count) are now
  captured for Instagram Reels** (via roach's Reels-grid enrichment — feature 002) and are the strongest
  reach signal; the v1 ranking uses likes + comments, and incorporating views for video is the top
  recommended enhancement. No external analytics or paid-platform metrics are in scope for v1.
- The downstream content-plan → issue-tracker workflow (existing) remains the sole path to issue creation;
  this feature stops at the content-plan worksheet.
- Client→accounts (own/competitor) associations are maintained by operators via a configuration mapping
  and/or supplied per run; automatic competitor discovery is out of scope.
- Publish-date distribution across the month uses a simple, even spread by default; sophisticated
  scheduling (best-time-to-post modeling) is out of scope for v1.
- Draft deliverables are stored in the project's existing document/workspace storage under a configured
  location; access control follows existing workspace conventions.
- Generation quality/relevance is reviewed by a human before publication; the system produces drafts, not
  auto-published content.
