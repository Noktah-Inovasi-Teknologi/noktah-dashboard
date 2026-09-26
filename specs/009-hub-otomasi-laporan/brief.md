# Brief: Noktah Hub — Otomasi & Laporan (spec 009)

Two new Hub menus, and the automations moved onto the Hub's Registry (deferred item A-1).

## Decided 2026-09-26 (settled; don't re-ask)

- **Otomasi** menu holds the Jira automation and roach/harvest. Songbird joins later: it is out of scope, as is its monthly schedule bug.
- Flows in scope read clients, teams, social accounts, Jira components and content-plan folders from the **Hub Registry**, not the Clients/Hashmaps sheet copies (docs/DEFERRED.md A-1). This needs a constitution amendment: its Data Management section makes `hashmap.py` the source today.
- **Jira:** each content plan (one client × one month, read from its Drive sheet) is greenlit **in the Hub** by a manager ("Setujui") before its issues can be created. Creation is **manual and in batches**: a manager picks several approved plans and presses "Buat issue Jira". No automatic daily run.
- **Harvest review moves into the Hub:** reviewers mark ads there; the Account Social Harvest sheets stop being written.
- **Harvest** reads own and competitor accounts from the Registry; one job replaces the 22 hand-written `harvest-monthly-*` deployments.
- **Laporan** menu, both in v1:
  - (a) **delivery** per client per month: planned vs Jira issues created vs published, with what's late. Needs a Jira issue sync into our database; nothing stores Jira issues today.
  - (b) **performance** per client per month from harvest: views, engagement, top posts, vs the client's competitors.
- **Access:** a new permission "Kelola otomasi", ticked per person; Owner and Brand Managers get it by default. Who sees Laporan is still open.

## Not built yet

- A hub-api → Prefect bridge (deployments, runs, trigger with parameters).
- A Jira issue sync table.
- One Registry-driven harvest job.

## Context

- `CONTEXT.md`, `specs/008-hub-registry-client-card/` (the Hub's spec).
- `service/prefect/flows/content_plan_spreadsheet_to_jira_issue.py`: an 8-step manual chain that writes JSON files, keeps no run records and sends no alerts.
- `service/prefect/flows/social_harvest*.py`, `service/prefect/prefect.yaml`.
- `docs/DEFERRED.md` A-1.
