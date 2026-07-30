# Contract: Content-Plan Row (draft + live)

Defines how one generated Content Idea maps to worksheet columns, for both the draft deliverable and the
live content-plan worksheet.

Two authorities constrain this contract, and both must be checked before changing it:

1. **The content-plan layout ("DRAFT v5")** — the draft must be the same sheet reviewers already work in,
   column-for-column and in order.
2. **`service/prefect/tasks/utility_tasks.py::convert_content_plan_row_to_jira_issue`** — the downstream
   Jira converter; songbird must emit the columns that function reads.

## The v5 layout (20 columns, in order)

`No.`, `Tanggal`, `Waktu`, `Bentuk`, `Topik`, `Creator`, `Format`, `Purpose/Theme`,
`Strategic Application`, `Kebutuhan Personil`, `Known Facts`, `Shoot Guide`, `Reference`,
`Asset`, `Caption`, `Keterangan`, `Approval`, `Link Referensi`, `TicketID`, `Key`

### Columns songbird fills (`GENERATED_COLUMNS`)

| Column | Source | Required downstream | Notes |
|--------|--------|---------------------|-------|
| `No.` | engine | no | 1-based row sequence |
| `Tanggal` | engine-assigned (monthly) | yes (dates) | `YYYY-MM-DD`, distributed across the month (FR-004). Empty for on-demand. |
| `Bentuk` | **engine** | yes (content form) | `Post` / `Story` / `Short Video`. Set by the engine because generation is per content type — the model is not asked for it and so cannot get it wrong. |
| `Topik` | model `topik` | yes (issue summary) | specific, descriptive title |
| `Creator` | constant `Brand` | no | content originates from the brand |
| `Format` | **derived** = `Bentuk` | no | v5 mirrors Bentuk into Format rather than using a separate vocabulary |
| `Purpose/Theme` | model `purpose_theme` | no | 1–2 sentences on the content's aim |
| `Strategic Application` | model `strategic_application` | no | `Awareness` / `Consideration` / `Conversion` |
| `Shoot Guide` | model `shoot_guide` | no | Post → `-`; Story/Short Video → scene-by-scene real-footage plan (shot, angle, blocking, ambience), hook in the first 3 seconds |
| `Reference` | model `reference` | no | Post → slide-by-slide carousel design (`SLIDE n:` + Visual/Headline/Body/CTA, slide 2 a standalone hook); Video → reference link or flow; Story → frame flow + interactive elements |
| `Caption` | model `caption` | no | ready-to-post caption: hook first line, concise body, CTA, hashtags last |

### Columns left blank (production workflow owns them)

`Waktu`, `Kebutuhan Personil`, `Known Facts`, `Asset`, `Keterangan`, `Approval`, `Link Referensi`,
`TicketID`, `Key` — scheduling, personnel, assets, approval and ticketing are human/downstream
concerns and are never invented by the model.

## Rationale + disclaimer (FR-005 / FR-006)

The v5 layout has **no rationale columns**, so the draft has none either. `adapted_pattern`,
`source_exemplar` and `rationale` are still generated, and along with the hit disclaimer are recorded
in the **run-outcome JSON** (`result["data"][i]` and `summary.hit_disclaimer`). The audit trail is
preserved without deviating from the plan format.

## Alignment rules

- **Draft**: songbird creates the sheet with header = the 20 v5 columns in order, and appends rows
  positionally against that known header.
- **Live**: songbird reads the target worksheet's existing header row (`google_read_sheet_data`) and
  builds each row by **column-name lookup** — a value is placed only where its header exists, and
  unmapped fields are dropped. Missing columns are logged, never fabricated (FR-016).
- Cells must be scalar strings (no lists/dicts); multi-line briefs use `\n`.

## Acceptance checks

- Every produced row has non-empty `Topik`, `Bentuk` and `Caption`; monthly rows also have a valid
  in-month `Tanggal` (SC-001).
- `Format` equals `Bentuk`; `Creator` equals `Brand`; `No.` runs 1..N.
- Workflow columns listed above are empty.
- Live append: 100% of written cells land under an existing header of matching name; zero new columns
  created (SC-003).
