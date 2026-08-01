# Roster Maintenance Runbook

**Feature:** 004-relational-spine
**Audience:** whoever maintains the Clients / Hashmaps worksheets or the datastore they sync into.

This covers the two situations `roster-sync` cannot handle automatically, and — more importantly —
the two mistakes that are easy to make instead, each of which fails silently rather than loudly.
Neither failure is discoverable from the schema itself, which is why this document exists.

---

## 1. An account changes its handle on Instagram or TikTok

**Symptom:** a monthly harvest for a known client suddenly reports every item as
`items_skipped_unregistered`, or 0 items collected with no obvious cause.

**What happened:** the platform account renamed itself. The old handle in `account_handles` no
longer matches what roach lists, so `social.account.resolve` correctly reports the handle as
unregistered — that's it working as designed, not a bug.

### The mistake to avoid

**Do not just update the handle in the Clients worksheet (or the Hashmaps CLIENT_SOCIAL block) and
run `roster-sync`.** `roster.account.upsert` recognizes this shape — a new handle for a client that
already owns an active account on that platform — and **reports it as a rename candidate rather
than silently creating a second account** (see `roster-sync`'s summary,
`rename_candidates`). But if that report is ignored and the sheet edit is left as the only action
taken, the new handle stays unregistered forever, since nothing auto-creates the account for it. The
harvest keeps skipping.

Worse: if a maintainer works around that by clearing the row and re-adding it as if it were a new
client, `roster-sync` **will** create a second, disconnected account — silently splitting the
account's history in two. Every account-level query after that point undercounts the true history,
and nothing will tell you it happened.

### The correct procedure

1. Confirm the new handle in `account_handles.rename_candidates` from a recent `roster-sync` run
   (or run `docker exec prefect python flows/roster_sync.py --validate-only` to see it without
   writing anything).
2. Record the rename explicitly:
   ```python
   # from a Python shell inside the prefect container, or a one-off script
   from tasks.roster_tasks import roster_account_record_rename
   await roster_account_record_rename(
       account_id="<the existing account's id>",
       platform="instagram",
       new_handle_text="<the new handle>",
   )
   ```
3. **Then** update the Clients / Hashmaps worksheet to the new handle, so future `roster-sync` runs
   agree with what was just recorded.
4. Re-run `roster-sync` and confirm the account now shows `unchanged` (not `created`) for both the
   client and the account.

If the new handle already belongs to a *different* account in the datastore, this call raises
rather than merging — that's the shape of an account merge, which is deliberately never inferred
(FR-011b). Resolve the ambiguity by hand before retrying.

---

## 2. A client name mapping was seeded wrong

**Symptom:** a client's generated content, or its knowledge-base grounding, is drawing on the wrong
brand's facts.

**What happened:** the alias table (`client_aliases`) that maps a name to a client was seeded once,
automatically, from a token-subset matching rule over the roster and the knowledge base. That rule
is not always right — it is exactly what produced the original defect this feature fixes (see
`specs/004-relational-spine/spec.md` Context, the two Nirwana outlets).

### The mistake to avoid

**Do not hand-edit `client_aliases.client_id` directly in SQL**, and do not delete the row and let
the next `roster-sync` re-seed it. Both `roster.alias.upsert` and the knowledge-base alias seeding
step are **create-only with respect to `client_id`** — an existing alias is never rewritten by a
sync. That is deliberate: it is what stops the daily 03:30 WIB run from silently reverting a
correction back to the original (possibly wrong) mapping. But it also means a plain `UPDATE` against
the table works only until the next time someone re-derives the seed from scratch on an empty table,
and it does nothing about knowledge records already linked through the old mapping.

### The correct procedure

Use the reassignment path — the only one that updates both the alias and anything already linked
through it:

```python
from tasks.roster_tasks import roster_alias_reassign
result = await roster_alias_reassign(
    alias_key="<normalized alias, e.g. 'nirwana coffee space'>",
    new_client_id="<the correct client's id>",
)
# result["knowledge_records_relinked"] tells you how many knowledge_records rows moved with it
```

This re-tags the alias's `source` as `manual`, which is what makes it survive future syncs — a
`manual`-sourced alias is never touched by roster reconciliation. Verify with:

```sql
SELECT alias_key, alias_text, source, client_id FROM client_aliases WHERE alias_key = '<key>';
```

---

## Why both paths exist, in one sentence each

- **Rename recording** exists because a platform-side handle change is common (accounts rebrand),
  and without it, the account model this feature introduces — one stable identity across a handle
  history — degrades into exactly the "one row per collected string" problem it replaced.
- **Alias reassignment** exists because the seed seeded once, automatically, from a matching rule
  that is *known* to get at least one case wrong on this roster today — the point of putting the
  mapping in data (SC-009) is that it can be corrected without a code change, and this is that
  correction path.
