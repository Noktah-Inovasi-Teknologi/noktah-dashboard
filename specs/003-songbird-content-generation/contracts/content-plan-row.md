# Contract: Content-Plan Row (draft + live)

Defines how one generated Content Idea maps to worksheet columns, for both the draft deliverable and the
live content-plan worksheet. The **authoritative downstream contract** is
`service/prefect/tasks/utility_tasks.py::convert_content_plan_row_to_jira_issue` — songbird must emit
columns that function reads. Verify that function before changing this contract.

## Content-plan columns (consumed downstream)

| Column (header) | Source | Required downstream | Notes |
|-----------------|--------|---------------------|-------|
| `Topik` | model `topik` | yes (issue summary) | the content topic/title |
| `Tanggal` | engine-assigned (monthly) | yes (dates) | `YYYY-MM-DD`; distributed across month (FR-004). Empty for on-demand. |
| `Bentuk` | model `bentuk` | yes (content form) | e.g. Reels, Feed, Story, Carousel |
| `Format` | model `format` | no | description field |
| `Purpose/Theme` | model `purpose_theme` | no | description field |
| `Strategic Application` | model `strategic_application` | no | description field |
| `Visualisasi Konten` | model `visualisasi_konten` | no | execution/visual note |

Columns the downstream converter also reads but songbird leaves for humans/scheduler:
`Waktu`, `Creator`, `Kebutuhan Personil`, `Asset` (omitted → empty; not invented by the model).

## Draft-only rationale columns (reviewer aid — FR-016a)

Appended **after** the content-plan columns in the draft sheet; **excluded** from the live worksheet.

| Column | Source | Purpose |
|--------|--------|---------|
| `Adapted Pattern` | model `adapted_pattern` | which winning pattern/hook this idea adapts |
| `Source Exemplar` | model `source_exemplar` | reference to the top-performer it learned from (handle/content_id or short desc) |
| `Rationale` | model `rationale` | why this idea fits the client + goal |
| `Hit Note` | constant | "Performa audiens adalah bias dari pola historis dan tidak dijamin." (FR-006) |

## Alignment rules

- **Draft**: songbird creates the sheet with header = content-plan columns + rationale columns, in the
  order above, and appends rows positionally against that known header.
- **Live**: songbird reads the target worksheet's existing header row (`google_read_sheet_data`) and builds
  each row by **column-name lookup** — a value is placed only where its header exists; unmapped generated
  fields are dropped; rationale columns are never written (FR-016). Missing content-plan columns in the
  live sheet are logged, not fabricated.
- Cells must be scalar strings (no lists/dicts); multi-line notes use `\n`.

## Acceptance checks

- Every produced row has non-empty `Topik` and `Bentuk`; monthly rows also have a valid in-month
  `Tanggal` (SC-001).
- Live append: 100% of written cells land under an existing header of matching name; zero new columns
  created (SC-003).
