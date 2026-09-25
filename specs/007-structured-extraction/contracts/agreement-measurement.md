# Contract — Agreement Measurement

**Feature**: `007-structured-extraction` | **Date**: 2026-08-03

The controlled comparison between the two model paths. Implements FR-027 – FR-027f.

This is the clarified answer to "how do we know the two paths are comparable" — measured on
identical input, not inferred from side-by-side distributions.

---

## Why observational comparison was rejected

Video runs on `xiaomi/mimo-v2.5`, images on `google/gemini-2.5-flash-lite` (confirmed from the
running container, R8). If the video path shows more `hook` beats than the image path, there are two
explanations and the distributions cannot separate them:

1. the models label differently, or
2. Reels genuinely open differently from carousels.

Explanation 2 is not a confound to be controlled away — it is *true*, and it is what most of the
system's analysis is about. So the only way to measure the model effect is to hold the content
constant: same item, same prompt, same schema, two models.

Observational per-path distributions (FR-025, FR-026) remain available and remain a finding in their
own right. What FR-027f forbids is calling them evidence of a **model** difference.

---

## The overlap is images, and the measurement is one-directional

| Media class | `google/gemini-2.5-flash-lite` | `xiaomi/mimo-v2.5` | In sample? |
|---|---|---|---|
| image (130) | ✔ | probable, unverified | ✔ |
| carousel (323) | ✔ | probable, unverified | ✔ |
| story (2) | ✔ | probable, unverified | ✔ |
| **video (364)** | ✘ — vision-only, cannot take video | ✔ | **✘** |

455 of 819 items can be compared. 364 cannot, and no arrangement of the two configured models
changes that.

**So the result reads: "on image-class media, the two models agree X% of the time." It says nothing
about video.** FR-027a requires that limit be printed in the same report as the number, not buried
in a footnote — an agreement figure quoted without it will be read as a property of the system
rather than of one media class.

**Unverified, deliberately** (R8): the reason `OPENROUTER_IMAGE_MODEL` exists at all is that the
video model's providers did not reliably accept image input, alongside a history of "image-422
failures" ([analyze.py:22-31](../../service/roach/analyze.py#L22-L31)). If still true, some fraction
of the 455 will fail on `mimo`. **That is a finding, not sample attrition** — it means the two
models are not equally capable on the one class where they overlap. Those items are recorded as
`unsupported_media` quarantine rows and **excluded from the denominator with the exclusion
reported**, never dropped so the survivors can produce a cleaner-looking rate.

One live probe against a single image item settles this before a sample size is chosen. It has not
been run during planning because it costs a real model call.

---

## Running it

```bash
# What would it cost? Zero model calls. Same costing contract as backfill.
docker exec prefect python flows/roach_extract_calibrate.py --dry-run

# Real run over the fixed sample
docker exec prefect python flows/roach_extract_calibrate.py --sample image_overlap_v1 --confirm

# Read the current agreement (no calls — computed from stored extractions)
docker exec prefect python flows/roach_extract_calibrate.py --report
```

Each sampled item is extracted twice — once per model, same prompt version, same schema version,
same media bytes — and both extractions are stored with `purpose = 'calibration'`.

---

## The sample is fixed, and that is the point (FR-027b)

Membership lives in `calibration_sample_members`, not in a `LIMIT` or a random draw. When a model,
prompt, or schema version changes, the *same* items are re-measured — otherwise a change in the
agreement rate could be the new model or could be the new sample, and the measurement answers
nothing.

Every agreement figure carries the model identities and versions it was measured under. A figure
measured under superseded versions is reported as historical and never as current — the versions
are columns on the extractions being compared, so this cannot drift.

**Minimum sample (FR-027e, Constitution VIII)**: below `CALIBRATION_MIN_SAMPLE` (default 30 usable
pairs) the report states insufficient data and emits **no** agreement rate. Not a low-confidence
one — none. The threshold is not overridable from a flag.

---

## Agreement is not stored

It is computed from the `purpose = 'calibration'` extractions on demand. A stored rate could
silently disagree with the extractions it summarises, and re-deriving after a vocabulary change
would need a migration. This is the same reasoning that made `extraction_status` and feature 006's
`velocity_status` views rather than columns.

**What is reported:**

```
Agreement — sample image_overlap_v1 · 2026-08-03
  Models: xiaomi/mimo-v2.5 (A) vs google/gemini-2.5-flash-lite (B)
  Versions: prompt v1 · schema v1 · vocabulary v1
  Pairs attempted 60 · usable 52 · excluded 8 (unsupported_media on A)

  MEDIA CLASSES COVERED: image, carousel, story
  NOT COVERED: video (364 items) — B cannot accept video. No figure is asserted for it.

  Beat-function agreement by position       n     agree
    position 1                              52    83%
    position 2                              48    64%
    position 3+                             31    45%

  Beat count agreement (same number of beats)  52   58%
  Attribute agreement by dimension             —    (no dimensions published; S-05 pending)

  DISAGREEMENTS (52 pairs, 14 differing at position 1):
    A=hook        B=setup          9
    A=setup       B=hook           3
    A=main_point  B=hook           2
```

Three properties of that output are required, not stylistic:

- **Disagreements are enumerated with direction** (FR-027, acceptance 2). `A=hook B=setup` nine
  times and `A=setup B=hook` three times is a *systematic* skew; a single "72% agreement" hides it.
- **No single blended score.** Averaging position-1 and position-3 agreement into one number would
  merge a strong signal with a weak one, which FR-026 forbids.
- **Neither model is treated as ground truth.** There is no "accuracy", only agreement. Calling one
  correct would require a labelled reference this system does not have — and Constitution XI is
  explicit that no model is a final arbiter.

Falling agreement at later positions is expected (the tail of a flow is genuinely more ambiguous)
and is itself worth knowing: it bounds how much weight position-3 beats can carry in any downstream
analysis.

---

## Calibration extractions never enter the corpus (FR-027c)

Every sampled item is extracted twice. If both copies counted as production evidence, every
distribution over the sample would be double-weighted — and invisibly, since the rows look
identical to ordinary extractions.

Enforcement is structural: `purpose` is a column on `content_extractions`, every analytical query
filters `purpose = 'production'`, and `extraction_status` gives `calibration_only` its own terminal
state rather than treating it as evidence about the item.

SC-018 tests it directly: no item in the calibration sample contributes more than one extraction to
any figure.

---

## When both paths use the same model (FR-027d)

`IMAGE_MODEL` falls back to `MODEL` when unset ([analyze.py:26](../../service/roach/analyze.py#L26)).
Today they differ, so the comparison is meaningful — but it is one unset environment variable away
from being self-comparison.

In that case the run **reports that no cross-model comparison applies and makes no calls**:

```
No cross-model comparison applies.
  Both paths resolve to xiaomi/mimo-v2.5 (OPENROUTER_IMAGE_MODEL unset).
  Extracting the same item twice with the same model measures sampling noise, not agreement.
  No model calls were made.
```

Reporting ~100% agreement here would be true and completely misleading: it would read as "the two
paths agree" when there is only one path.

**Provider fallback mid-run** is the same failure wearing a disguise. `allow_fallbacks: true` means
a pair can end up served by models other than the intended two. `model_served` is recorded per
extraction, and a pair whose served models do not match the intended pair is excluded with the
exclusion counted — never folded into the rate.
