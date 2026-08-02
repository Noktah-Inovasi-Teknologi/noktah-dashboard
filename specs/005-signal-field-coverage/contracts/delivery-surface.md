# Contract: Reviewer Delivery Surface (Sheets)

**Feature**: `005-signal-field-coverage` | Satisfies FR-013, FR-016, FR-016a, FR-016b

## 1. Column layout

`ACCOUNT_HEADER` gains `shares` as a **trailing 20th column**. Every existing column keeps its
position (FR-016).

```
 1 id                11 views
 2 username          12 likes
 3 platform          13 comments
 4 content_id        14 drive_file_ids
 5 content_type      15 subtitle
 6 source_url        16 content_flow
 7 published_at      17 summary
 8 caption           18 harvest_name
 9 hashtags          19 harvest_date
10 (…)               20 advertisement
                     21 shares          ← NEW, appended
```

*(Positions above follow the existing `ACCOUNT_HEADER` order in
[social_harvest.py](../../../service/prefect/flows/common/social_harvest.py); `shares` is appended
after the last existing column, wherever that falls.)*

**Why trailing and not grouped with `views`/`likes`/`comments` where it logically belongs**:
inserting mid-layout shifts every column after it, silently repositioning `advertisement` — the one
column a human edits and `social-harvest-sync` reads back. Logical grouping is not worth
re-indexing reviewer-entered data. The metrics stay adjacent in Postgres, where order is irrelevant.

## 2. Header backfill (`sheet-header-backfill`)

One-off, idempotent flow bringing existing per-account tabs to the new layout (FR-016a).

```
docker exec prefect python flows/sheet_header_backfill.py --validate-only
docker exec prefect python flows/sheet_header_backfill.py
```

Contract:

- **Touches the header row only.** Never reads, writes, or reorders a data row. A reviewer's
  `advertisement` edits must be untouchable by a layout migration.
- **Idempotent**: a tab already carrying `shares` is reported `unchanged`. Safe to re-run — an
  operator cannot always know which tabs were done.
- **Never destructive**: if a tab's header does not match the expected previous layout, the flow
  **skips it and reports it** rather than overwriting. An unexpected header means an assumption is
  wrong, and guessing would corrupt a reviewer surface.
- Returns the standard flow dict with
  `summary: {tabs_scanned, tabs_updated, tabs_unchanged, tabs_skipped, skipped_detail[]}`.

## 3. Name-based column resolution (FR-016b)

All reads of the reviewer surface — notably the `advertisement` write-back in
`social_harvest_sync.py` — MUST resolve columns by **header name**, not fixed index.

```python
# Contract
def column_index(header_row: list[str], name: str) -> int   # raises if absent
```

- A missing expected column raises rather than defaulting to an index. Silently reading the wrong
  column is worse than a failed sync — it writes a reviewer's ad flag onto unrelated content.
- This is the safeguard for the window between deploying the code and completing the backfill, and
  for any tab created in between. It is **not** a substitute for the backfill (Assumptions).

**Task**: `google.sheets.ensure-header` — verifies/extends a tab's header to the current layout,
used by both the delivery path and the backfill flow so one definition governs both.

## 4. Write contract

Rows are written positionally against the **resolved** header, so a tab that has not yet been
backfilled receives its `shares` value only after `ensure-header` has extended it. A tab that
cannot be extended is reported, and its rows are written without `shares` rather than misaligned.

## 5. What does not change

- `advertisement` stays reviewer-owned with a `FALSE` default; nothing in this feature writes it.
- The detail-sheet variant keeps its extra `account_folder_id` column; it gains `shares` under the
  same trailing rule.
- No change to Drive folder structure or to `harvested_items`.
