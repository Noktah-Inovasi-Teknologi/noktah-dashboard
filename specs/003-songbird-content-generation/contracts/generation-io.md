# Contract: Generation I/O (OpenRouter)

The input assembled for and the structured output expected from the `openrouter.chat.complete` task when
songbird generates ideas. Enforced with a strict `json_schema` response format (via `array_schema`), so
the model returns guaranteed-valid JSON.

## Input (prompt assembly, built by the engine)

A single user prompt (with a system persona) composed from:

1. **Persona / rules** (system): a Bahasa-Indonesia content strategist for the client. Rules: write
   primarily in Indonesian with natural English code-mixing (brand names, hashtags, loanwords like
   "reels"/"engagement", trending phrases, taglines) — do not force-translate; mirror the client's own
   code-mixing style; **adapt** winning patterns (hooks, formats, pacing), never copy exemplars verbatim;
   performance is not guaranteed.
2. **Client context**: the `{subject: information}` knowledge records (may be empty → note "no KB
   grounding").
3. **Marketing params**: platform, audience, goal, tone, content pillars.
4. **Hit exemplars**: the ranked top performers — for each, its `content_flow`, `summary`, top hashtags,
   and engagement — labelled own vs competitor. (May be empty → generate from knowledge + params only.)
5. **Ask**: produce exactly `N` distinct content ideas as JSON.

The engine — not the model — supplies the count `N` (from Client Configuration / param) and later the
dates. The prompt must not request dates (FR-004).

## Output (strict JSON, `array_schema("content_ideas", …)`)

Root object with an `items` array; each item has exactly these string keys:

```json
{
  "items": [
    {
      "topik": "…",
      "bentuk": "Reels | Feed | Carousel | Story | …",
      "format": "…",
      "purpose_theme": "…",
      "strategic_application": "…",
      "visualisasi_konten": "…",
      "adapted_pattern": "which winning pattern/hook this adapts (or 'umum' if no signal)",
      "source_exemplar": "handle/content_id or short description (empty if no signal)",
      "rationale": "why this fits the client + goal"
    }
  ]
}
```

- All keys required (strict schema); empty string allowed where not applicable (e.g. `source_exemplar`
  when no signal), mirroring roach's empty-field convention.
- Values are single strings (no nested arrays/objects) so they map directly to sheet cells.
- The engine validates `len(items)` and continues on a short/failed batch (FR-020) rather than aborting;
  a per-idea shape violation drops that idea and counts it failed.

## Provider behaviour (inherited from `openrouter_tasks.py`)

- Deterministic provider order, reasoning disabled, 429 back-off with `Retry-After` (FR-021).
- Non-2xx (non-429) surfaces the provider error body and fails the task (Prefect retries `retries=2`).
