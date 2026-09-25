# Deferred findings register

This lists things that were **identified, judged real, and deliberately not done** in the spec that found them, because doing them would have changed behaviour, needed a spec decision, or belonged to another feature. A later spec checks here before it starts, so nothing is rediscovered or silently forgotten.

**This is not a wish list.** Every row was found by a review, a spec, or real use, and records what it costs to leave it undone. Plans and priorities for the business live in the Eskala Ops Plan tracker, not here. This register only holds engineering findings that a spec deferred.

## How it works

- **Adding:** when a spec, review, or implementation turns up a finding it will not act on, add a row. Give its origin spec, why it was deferred, and, where known, the spec whose scope it belongs to (**Target**). The commit that defers it references the row.
- **Claiming:** `/speckit-grill`, `/speckit-specify` and `/speckit-plan` MUST read this file. Every row whose **Target** matches the new spec's scope (or says `any`) is either:
  - **claimed**: moved into that spec's requirements or tasks, and the row deleted in the same PR; or
  - **re-deferred**: given a fresh reason and a new target.

  Silence is not an option; the spec's checklist has a line for it.
- **Resolving outside a spec:** a row that turns out to be a small change may be fixed directly; delete the row in the same PR.
- **Never** let a row sit with no target and no reason. That's the "we forgot" state this file exists to prevent.

Rows are grouped by the part of the system they touch, newest at the bottom of each group. IDs are never reused: H = Hub, D = data and schema, A = automations (Prefect flows), C = collection (roach), S = songbird, I = infrastructure.

## Hub

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|
| H-1 | No Client groups: branches of one group (e.g. the SMEC clinics) each keep a full Client Card; shared brand facts are copied with "copy from another client", not shared. | spec 008 grill G-3, 2026-09-25 | Groups add a sharing model that could leak one branch's facts into another (ESKL-11176); copying is enough for now | none yet — revisit if copying brand facts becomes a chore | A shared logo or tagline change is repeated per branch by hand, and branches can drift apart |
| H-2 | Client Card facts and Requests are not pushed into Jira tickets or Slack; a Manager pastes a Jira link onto a Request by hand. | spec 008 grill G-1, G-26, 2026-09-25 | A separate capability (the tracker's "Show the Client Card inside every new Jira issue") | the Client Card → Jira/Slack spec | Staff still work from briefs without the card's facts; the caption errors the card exists to prevent can recur |

## Data & schema

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|

## Automations

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|
| A-1 | The automations (harvest via prefect.yaml, songbird, content-plan → Jira via the Clients sheet and hashmap.py) still read their own copies of the client list; spec 008 keeps the sheet updated from the Registry instead of switching them. Retiring hashmap.py as a source needs a constitution amendment (Data Management section). | spec 008 grill G-1, G-7, 2026-09-25 | Changes how running automations behave; deserves its own careful spec | the Registry switch-over spec | Four copies of the client list persist; the sheet-copy job must keep running and stay correct; harvest targets still drift from the Registry |

## Collection

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|

## Songbird

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|
| S-1 | Songbird grounds plans in the old free-form `knowledge_records`, not the new Client Card (Profil, Guideline with archetype/voice ratings, open Requests). | spec 008 plan, 2026-09-25 | Switching generation grounding is an automation change (G-1) and needs its own quality measurement | the Registry switch-over spec, or a songbird spec | Generated plans ignore the Guideline the Brand Manager approved, and keep citing stale facts the Card has corrected |

## Infrastructure

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|
