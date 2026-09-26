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
| H-2 | Client Card facts and Requests are not pushed into Jira tickets or Slack; a Manager pastes a Jira link onto a Request by hand. | spec 008 grill G-1, G-26, 2026-09-25 | A separate capability (the tracker's "Show the Client Card inside every new Jira issue"). Re-deferred by spec 009 grill G-2, 2026-09-26: spec 009 changes when and how issues are created, not what they say | the Client Card → Jira/Slack spec | Staff still work from briefs without the card's facts; the caption errors the card exists to prevent can recur |
| H-3 | The Hub's performance report is internal only; the monthly report each Client receives (Framework v2.1 G2: late or undelivered is a violation) is still made by hand, outside the Hub. | spec 009 grill G-41, 2026-09-26 | The client report has its own format and voice and deserves its own spec | a client monthly report spec | Account Executives keep assembling client reports by hand from the same numbers the Hub already holds, and a late one is a G2 violation |

## Data & schema

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|

## Automations

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|
| A-1 | Songbird still reads its own copy of the client list (the Clients and Hashmaps sheets via hashmap.py), so spec 008's Registry → sheet copy must keep running. Spec 009 moved the harvest and the content-plan → Jira automation onto the Registry; only songbird is left. | spec 008 grill G-1, G-7, 2026-09-25; narrowed by spec 009 G-2, 2026-09-26 | Songbird is out of spec 009's scope | a songbird spec | The sheet-copy job must keep running and stay correct; songbird's handles can drift from the Registry |

## Collection

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|

## Songbird

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|

## Infrastructure

| # | Finding | Found by | Why deferred | Target | Cost if never done |
|---|---|---|---|---|---|
