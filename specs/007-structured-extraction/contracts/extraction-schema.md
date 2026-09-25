# Contract — Extraction Schema

**Feature**: `007-structured-extraction` | **Schema version**: `v1` | **Date**: 2026-08-03

The model-facing output contract. Declared once in `config/extraction/schema_v1.json`, used in three
places: roach's OpenRouter `response_format`, roach's client-side validation, and Prefect's
storage-boundary re-validation (R7). Three declarations would drift silently; one cannot.

---

## The schema is identical on both paths (FR-023)

Video and image extraction emit the **same shape**. The only permitted difference is the content of
`subtitle` / `subtitle_absence` — an image post has nothing to transcribe, which is a fact about the
media, not a difference in the contract.

Today the two paths request different key sets (`["subtitle","flow","summary"]` vs
`["flow","summary"]`) and the image path back-fills `subtitle` client-side to `""`
([analyze.py:312](../../service/roach/analyze.py#L312)). That is what makes the current output
non-comparable and what SC-004 tests against.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["subtitle", "subtitle_absence", "beats", "attributes", "summary"],
  "properties": {
    "subtitle": {
      "type": ["string", "null"],
      "description": "Full verbatim transcript, original language. Also transcribe on-screen text not spoken, prefixed with [on-screen]. null if there is nothing to transcribe."
    },
    "subtitle_absence": {
      "type": ["string", "null"],
      "enum": ["not_applicable_no_audio", "attempted_none_found", null],
      "description": "Required when subtitle is null; null when subtitle is present."
    },
    "beats": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["position", "function", "description"],
        "properties": {
          "position":    {"type": "integer", "minimum": 1},
          "function":    {"type": "string", "enum": ["hook", "setup", "main_point", "call_to_action", "unclassified"]},
          "description": {"type": "string", "minLength": 1}
        }
      }
    },
    "attributes": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["dimension", "value", "confidence"],
        "properties": {
          "dimension":  {"type": "string"},
          "value":      {"type": "string"},
          "confidence": {"type": "string", "enum": ["high", "medium", "low"]}
        }
      }
    },
    "summary": {"type": "string", "minLength": 1}
  }
}
```

The `beats.function` enum is **generated from `extraction_vocabulary_terms` at the recorded version**,
not hand-written into the file — otherwise the schema and the vocabulary become two sources of truth
for the same closed list. `attributes` is `minItems`-free and expected to be empty until S-05
publishes dimensions (FR-043); an empty array is the correct inert state, not a failure.

---

## What the schema cannot express, and who enforces it

| Rule | Source | Enforced by |
|---|---|---|
| Positions contiguous from 1, no gaps | FR-002 | roach validator, re-checked at storage |
| Positions unique within an extraction | FR-002 | roach validator + `uq_beat_position` |
| `subtitle_absence` set iff `subtitle` is null | FR-010 | roach validator + `chk_extraction_subtitle_absence` |
| At most one value per dimension | FR-012 | roach validator + `uq_attribute_dimension` |
| Attribute dimension/value valid at the recorded version | FR-015 | composite FK at storage |
| Response was not truncated | FR-017 | `finish_reason` check — **not** the schema |

JSON Schema can express `minItems` but not "contiguous", and can express `enum` but not "valid under
the version this row records". Anything in the right-hand column that lives only in the validator
gets a test; anything that can reach the database gets a constraint too. FR-016's "no unvalidated
path may exist" is why both layers exist rather than one.

---

## Truncation is not a schema concern

A response cut off at `max_tokens` may still parse and may still satisfy the schema — a beat array
truncated after three complete beats is structurally valid JSON describing a story that stops
mid-way. **The schema cannot catch this.** Only `finish_reason == "length"` can.

```
if choice.get("finish_reason") == "length":  -> failure_kind = "truncated", invalid, no salvage
```

This is checked **before** parsing, so a well-formed fragment never gets the chance to look valid.
It is the single most important line in the change: today `analyze.py` does not check
`finish_reason` at all, and `_extract_json`'s regex fallback actively rescues the fragment
([analyze.py:171-174](../../service/roach/analyze.py#L171-L174)).

---

## Prompt changes required

The prompts stop asking for "a short numbered breakdown" and start naming the vocabulary, with each
term's `description` taken from `extraction_vocabulary_terms` so prompt and validator cannot
disagree. Three rules the prompt must state explicitly, because each maps to a failure the
validator would otherwise have to catch on the retry:

1. **Every beat's function must be one of the listed terms.** If none fits, use `unclassified` —
   do not invent a term. (FR-006 rejects invented terms outright; the prompt should make that
   unnecessary rather than merely punished.)
2. **Emit at least one beat.** Content with no discernible structure is one `unclassified` beat
   covering the item — never an empty array. (FR-003)
3. **The transcript is verbatim and complete.** Do not summarise, trim, or paraphrase it to save
   room. (FR-008, FR-011)

Dates, engagement judgements, and performance claims remain absent from the prompt — the model
describes what is in the media and nothing else, exactly as today.

**`prompt_version` is bumped on any edit to this text**, including a whitespace-only one. A prompt
change that is not recorded makes FR-028 false and silently mixes two populations in every
distribution computed afterwards.

---

## Response envelope (roach → Prefect)

Both outcomes are `200 OK`. A quarantine is **not** an HTTP error — see
[validation-outcome.md](./validation-outcome.md) for why that matters and what breaks if it is.

```jsonc
// validated
{
  "ok": true,
  "content_id": "...",
  "analysis": {
    "status": "success",
    "subtitle": "...", "subtitle_absence": null,
    "beats": [ {"position": 1, "function": "hook", "description": "..."} ],
    "attributes": [],
    "summary": "..."
  },
  "provenance": {
    "model_requested": "xiaomi/mimo-v2.5",
    "model_served": "xiaomi/mimo-v2.5",
    "provider": "xiaomi",
    "media_path": "video",
    "prompt_version": "v1", "schema_version": "v1", "vocabulary_version": "v1",
    "content_hash": "sha256:...",
    "attempts": 1
  },
  "usage": {"prompt_tokens": 1204, "completion_tokens": 380, "cost_usd": 0.0009}
}
```

`usage` is new: today `_call_model` logs it and **discards it before returning**
([analyze.py:140-161, 219](../../service/roach/analyze.py#L140-L161)), which is why no cost baseline
exists anywhere (R3). `model_served` and `provider` are read from the response body, not assumed
from the request — provider routing has `allow_fallbacks: true`, so what was asked for and what
answered are not the same question (FR-024).
