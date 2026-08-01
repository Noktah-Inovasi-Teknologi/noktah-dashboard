# Contract: Account Resolution at Write Time

**Feature**: `004-relational-spine` | Satisfies FR-014, FR-016, FR-016a/b/c, FR-024a

The rule the collection write path follows when turning a `profile_key` into an `account_id`.

## Resolution

```
normalise(handle)  →  lower(trim(handle))
lookup             →  account_handles WHERE platform = $1 AND handle_key = $2
                      (matches current AND former handles — FR-011c)
```

Resolution returns one of exactly three outcomes.

| Outcome | Condition | Action |
|---|---|---|
| `resolved` | handle found, `accounts.is_active = true` | Write the record with `account_id` |
| `unregistered` | no `account_handles` row | **Skip the item.** Do not write. Do not create an account |
| `inactive` | handle found, `accounts.is_active = false` | **Skip the item.** Do not write |

## The write path never creates an account

Accounts are created by `roster-sync` or by the one-time `spine-backfill` — never here (FR-016b).

This is a deliberate trade. It means a handle added to a spreadsheet but not yet reconciled
collects nothing, and a collection deployment pointed at a brand-new handle produces an empty
harvest. That cost was accepted so a typo in a spreadsheet cannot silently create a phantom account
that then accumulates real content under no client.

## Skips are classified, never silent

Per item skipped, the run summary records the handle, the reason, and the count. The three
zero-item outcomes must be **distinguishable from each other** (FR-016c, FR-023a):

| Summary key | Meaning | Operator action |
|---|---|---|
| `items_skipped_unregistered` | handle has no account | Add the handle to the sheet, run `roster-sync` |
| `items_skipped_inactive` | account exists, deactivated | Remove the collection target — it should not be running |
| *(neither present, zero new items)* | nothing new published | None |

A bare zero is ambiguous between all three, and only two of them are faults. Nothing gates a
harvest on reconciliation (FR-022a), so this summary is the **only** place a configuration gap
surfaces.

## Continue, never abort

A skip affects one item. The remaining items for that profile are still processed, and the run
still completes — consistent with constitution V and with how the existing engine already treats
analysis failures and signal-write failures.

## Follower observation

Best-effort, in the same pass, and **never able to fail a run** (FR-013b):

| Platform returned | Action |
|---|---|
| a follower count | Insert one `account_follower_observations` row |
| nothing | Insert **no** row (FR-013a) — absence is absence, not zero |

Measured today: TikTok returns a count; Instagram returns none for every account (research R3), so
one account produces observations at ship time. That is the expected, correct state.

## Reversibility

Every behaviour here is additive. With the new tables absent or empty, `social.account.resolve`
is not called and the write path behaves exactly as it does today — which is what makes US1
deployable independently of the backfill completing.
