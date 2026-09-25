# Contract — Validation Outcome

**Feature**: `007-structured-extraction` | **Date**: 2026-08-03

What happens between "the provider responded" and "a row exists". Implements FR-016 – FR-022.

---

## The path

```
model response
   │
   ├─ finish_reason == "length" ─────────────► INVALID (truncated). No parse. No salvage.
   │
   ├─ empty content ─────────────────────────► INVALID (empty_content)
   │
   ├─ parse JSON ── fails ───────────────────► INVALID (schema_invalid)
   │      │
   │      └─ strict parse only. NO regex fallback.
   │
   ├─ validate against schema_vN ── fails ───► INVALID (schema_invalid)
   │
   ├─ cross-row checks ── fail ──────────────► INVALID (schema_invalid | out_of_vocabulary)
   │      contiguity · ≥1 beat · one value per dimension · terms ∈ recorded version
   │
   └─ all pass ──────────────────────────────► VALID → content_extractions (+ beats, attributes)

INVALID, attempt 1 ─► retry ONCE, with the validation error appended to the message
INVALID, attempt 2 ─► extraction_quarantine. Nothing reaches content_extractions.
```

Exactly one retry (FR-018). Not two, not a configurable count: a second correction attempt on a
model that has already ignored a quoted error is spend without evidence it helps, and the quarantine
row is more useful than a third roll.

---

## The retry must carry the error, not just re-ask

Today's retry loop re-sends the identical payload
([analyze.py:185-222](../../service/roach/analyze.py#L185-L222)) — it is a re-roll, not a
correction. The retry appends the failed output and the specific validation error as a follow-up
turn:

```
[original user message: prompt + media]
[assistant: <the invalid output, verbatim>]
[user: That response was rejected: beats[2].function was "demonstration",
       which is not one of: hook, setup, main_point, call_to_action, unclassified.
       Return corrected JSON only.]
```

The error text is the validator's own message, quoted — not a paraphrase. Pydantic's structured
errors are why R5 chose it over hand-rolled checks: a precise, quotable error is the entire
mechanism here.

**The media is not re-sent.** It is already in the conversation as the first message; re-uploading
base64 video to restate a JSON complaint would roughly double the input cost of every retry.

---

## A quarantine is not an HTTP error

Both outcomes return `200 OK`.

| Shape | Meaning | Harvest flow does |
|---|---|---|
| `200 {ok: true, analysis: {status: "success", ...}}` | validated | stores extraction |
| `200 {ok: true, analysis: {status: "quarantined", ...}}` | model failed twice | stores quarantine, **continues** |
| `404 {ok: false, code: "content_gone"}` | platform failure | skips the profile |
| `429 {ok: false, code: "rate_limited"}` | platform failure | backs off, rotates egress |
| `500` | **roach's own bug** | propagates |

`.claude/rules/backend/roach.md` is explicit that the `{ok: false, code, reason}` envelope means
*platform* failure and that the harvest flow **branches** on it. A model that wrote malformed JSON is
not a platform failure and not a roach bug. Dressing a quarantine as a 429 would make the flow back
off from a perfectly healthy profile and rotate egress in response to a model quirk; dressing it as
a 404 would make it skip the profile entirely.

That rules file records this exact mistake being made before — adding `stories_only` to
`list_profile` "turned into tests asserting `500 == 404` and `500 == 429`". The mitigation is the
same: a quarantine rides inside a successful response, exactly as `status: "failed"` does today
([analyze.py:368](../../service/roach/analyze.py#L368)).

---

## What is deleted

Two code paths must be **removed**, not bypassed, and each needs a regression test asserting it is
gone:

| Removed | Why |
|---|---|
| `_extract_json`'s `\{.*\}` regex fallback ([analyze.py:171-174](../../service/roach/analyze.py#L171-L174)) | It is the mechanism that makes a truncated response look valid. FR-017. |
| `_to_text`'s list coercion ([analyze.py:371-374](../../service/roach/analyze.py#L371-L374)) | It newline-joins arrays "because the model sometimes returns arrays despite the schema" — i.e. it silently repairs a schema violation. Under FR-006 that response is invalid and gets the retry. |

A test that merely stops *calling* them is not enough. If either survives in the module, a future
edit can reintroduce the salvage path and every SC-006 guarantee ("zero extractions originate from a
response that was cut short or pattern-salvaged") becomes unverifiable.

---

## Collection never fails because extraction did (FR-022)

A quarantine is not an error to the harvest. The item's media is still uploaded to Drive, its
metrics are still recorded, its sheet row is still written. This preserves today's behaviour, where
`analyze_item` returns `status: "failed"` rather than raising and the download is delivered anyway
([social_harvest.py:480-487](../../service/prefect/flows/common/social_harvest.py#L480-L487)).

What changes: the item's `content_flow` for the sheet is rendered from beats when extraction
succeeded, and left as it is today when it did not. SC-015 tests that the media-delivery rate is
unchanged from the pre-feature baseline.

---

## Counting (FR-020)

```sql
SELECT failure_kind, model_requested, prompt_version, schema_version, count(*)
FROM extraction_quarantine
GROUP BY 1,2,3,4 ORDER BY 5 DESC;
```

Every quarantine row keeps `raw_output` and both attempts' `validation_errors`, so a count can
always be opened up into the actual text that failed. A count without the evidence behind it would
tell you the rate was rising and nothing about why — which is the position the current code leaves
you in, since it discards the raw output and collapses every cause to `""`.

**Quarantine rows carry cost.** Tokens were spent whether or not the output was usable; omitting it
would bias the measured baseline low (R3) and make a pathologically-failing item look free.
