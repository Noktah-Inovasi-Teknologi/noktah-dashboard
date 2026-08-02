# Contract: Field Availability Determinations

**Feature**: `005-signal-field-coverage` | Satisfies FR-001, FR-001a, FR-001b, FR-002, FR-002a, FR-007

## 1. Status vocabulary (closed)

Exactly five values. A sixth requires a versioned vocabulary change (Constitution XII), a migration
to widen the CHECK constraint, and a documented decision — it is not an incidental edit.

| Status | Means | Consumer action |
|---|---|---|
| `available` | Published by the platform for this content type, and collected | Use the value. An empty one is a capture-outcome question, not an availability one. |
| `unavailable_platform_limit` | The platform does not publish this field for this content type | Never expect it. **Exclude from coverage denominators.** |
| `not_collected_by_decision` | Obtainable, deliberately not collected | Revisit only by reversing the recorded decision. |
| `inconclusive` | Investigated, no determination reached | Treat as unknown. Re-investigation may resolve it. |
| `undetermined` | Not yet investigated | Investigate. |

**The two most commonly confused**: `unavailable_platform_limit` is a fact about the platform;
`not_collected_by_decision` is a fact about us. Never record the second as the first — that
converts a reversible policy choice into an apparent law of nature (FR-012, FR-023).

## 2. Declarative source: `config/field_availability.yaml`

Source of truth (FR-001a). Version-controlled so each determination and its evidence is reviewable
as a diff.

> **Not `data/`.** `.gitignore:111` ignores any directory named `data`, which would leave the
> source untracked and defeat the point of FR-001a. `config/` is tracked.

```yaml
version: 1
determinations:
  - platform: instagram
    content_type: carousel
    field: comments
    status: available
    reason: >
      Returned by /v1/feed/user/<id>/ on the same media object shape the clips
      endpoint uses. gallery-dl fetches this payload but drops the field.
    evidence: >
      gallery-dl 1.32.6 extractor/instagram.py:240 maps like_count only;
      confirmed live 2026-08-0X with 1 probe request (budget 10, FR-012b).
    determined_on: 2026-08-0X

  - platform: instagram
    content_type: story
    field: likes
    status: unavailable_platform_limit
    reason: Instagram publishes no engagement counts on stories.
    evidence: collect.py:838 hardcodes all counts to None; 2/2 stored rows have no likes.
    determined_on: 2026-08-02

  - platform: tiktok
    content_type: carousel
    field: shares
    status: undetermined
    reason: Code path exists but has never produced a row; availability unverified.
    evidence: 0 tiktok carousel rows in harvested_signals as of 2026-08-02.
    determined_on: 2026-08-02
```

**Required fields**: `platform`, `content_type`, `field`, `status`, `reason`, `determined_on`.
`evidence` is required for every status except `undetermined`.

**Rejection rules** — the sync fails loudly rather than importing partial data:

- Unknown `status` → reject the file (FR-002a).
- Missing or empty `reason` → reject.
- Duplicate (platform, content_type, field) → reject.
- `status != undetermined` with no `evidence` → reject.

A parse failure MUST leave the existing table untouched. A determination silently reverting to a
stale value is worse than a sync that visibly failed.

## 3. Flow contract: `field-availability-sync`

```
docker exec prefect python flows/field_availability_sync.py
docker exec prefect python flows/field_availability_sync.py --validate-only
```

Per Constitution I, returns:

```python
{
  "start_time": "...", "end_time": "...",
  "data": [ {platform, content_type, field, status, action: "inserted"|"updated"|"unchanged"} ],
  "summary": {
      "determinations_total": int,
      "inserted": int, "updated": int, "unchanged": int,
      "rejected": int,
      "coverage_gaps": [ "<platform>/<content_type>/<field>" ],   # combos with no determination
  },
  "error": None,
}
```

- **Never raises** (Constitution I). A rejected file yields `error` set and zero writes.
- `--validate-only` is mandatory (Constitution, Data Management) — parses and reports without
  writing.
- **Idempotent**: re-running with an unchanged file produces `unchanged` for every row and zero
  writes.
- `coverage_gaps` is what SC-001 is measured against: it enumerates every (platform, content_type,
  field) present in `harvested_signals` that has no determination.

**Task**: `availability.determination.sync` (`api-group.resource.action`, Constitution I).

## 4. Read contract

```python
availability_determination_get(platform, content_type, field) -> dict | None
```

Returns the determination or `None`. A caller receiving `None` MUST treat it as `undetermined` and
MUST NOT infer availability from the presence or absence of stored values — inferring "the platform
must not publish it" from an empty column is the exact reasoning error this feature exists to
eliminate.

## 5. What this contract does not cover

- It does not gate collection. A determination is a record of what is knowable, not a switch that
  turns a capture on or off. Collection code paths remain independently readable.
- It does not carry per-row state. "Was it known this time" is
  [capture-outcome.md](./capture-outcome.md).
