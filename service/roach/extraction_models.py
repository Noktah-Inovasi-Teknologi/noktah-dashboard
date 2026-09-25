"""
Client-side validation of extraction output (feature 007-structured-extraction).

Provider-side `strict: true` json_schema is honoured only by providers that
implement it, and this service routes across four with `allow_fallbacks: true`.
Whatever comes back used to be parsed and used. FR-016 requires the system
validate output itself before anything is stored, and forbids any code path that
stores unvalidated output.

Pydantic rather than `jsonschema` for one specific reason (research.md R5): its
errors are STRUCTURED AND QUOTABLE, and the retry (FR-018) has to hand the model
its own specific failure back. A boolean "invalid" would make the retry a re-roll
— which is exactly what the code did before this feature.

Failure classification is deliberately two-pass:

    1. pydantic  -> `schema_invalid`      (shape, types, contiguity, cardinality)
    2. vocabulary -> `out_of_vocabulary`   (a term outside the recorded version)

Collapsing them would lose the distinction FR-006 and FR-021 depend on: an
invented beat function is a vocabulary problem worth counting on its own axis,
not a malformed response.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# The closed absence vocabulary (FR-010). "extraction did not complete" is
# deliberately NOT a member: such a run produces a quarantine row, never a
# content_extractions row, so it is not representable here at all.
SubtitleAbsence = Literal["not_applicable_no_audio", "attempted_none_found"]

# An ordered scale, not a continuous score. A self-reported 0.83 implies a
# calibration the model does not have (Constitution VI).
Confidence = Literal["high", "medium", "low"]


class Beat(BaseModel):
    """One element of the ordered content flow (FR-001)."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=1)
    # Deliberately `str`, not an enum. Membership is checked in the SECOND pass
    # so an invented term classifies as `out_of_vocabulary` rather than being
    # swallowed into a generic schema error.
    function: str = Field(min_length=1)
    description: str = Field(min_length=1)


class AttributeAssignment(BaseModel):
    """One dimension, one value, one confidence (FR-012, FR-013)."""

    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(min_length=1)
    value: str = Field(min_length=1)
    confidence: Confidence


class ExtractionResult(BaseModel):
    """The validated shape both model paths must produce (FR-023).

    The only permitted difference between the video and image paths is the
    CONTENT of subtitle/subtitle_absence — an image post has nothing to
    transcribe, which is a fact about the media, not a difference in the
    contract.
    """

    model_config = ConfigDict(extra="forbid")

    subtitle: Optional[str] = None
    subtitle_absence: Optional[SubtitleAbsence] = None
    beats: list[Beat] = Field(min_length=1)
    # No `min_length`: empty is the CORRECT inert state until the external
    # attribute vocabulary publishes dimensions (FR-043), not a failure.
    attributes: list[AttributeAssignment] = Field(default_factory=list)
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_cross_row_rules(self) -> "ExtractionResult":
        """The rules JSON Schema cannot express.

        `minItems` is expressible; "contiguous" is not. "one value per dimension"
        is not. "absence set iff subtitle is null" is not. Each of these would
        otherwise pass provider-side strict validation and reach storage.
        """
        positions = [b.position for b in self.beats]

        # Uniqueness before contiguity, so duplicates report as duplicates rather
        # than as a confusing gap.
        duplicates = sorted({p for p in positions if positions.count(p) > 1})
        if duplicates:
            raise ValueError(
                f"beat positions must be unique within an extraction; "
                f"position(s) {duplicates} appear more than once")

        expected = list(range(1, len(positions) + 1))
        if sorted(positions) != expected:
            raise ValueError(
                f"beat positions must be contiguous starting at 1; "
                f"got {sorted(positions)}, expected {expected}")

        if positions != sorted(positions):
            raise ValueError(
                f"beats must be listed in the order they occur; "
                f"got positions {positions} in that order")

        # FR-010: an empty value alone may not stand for three different facts.
        if self.subtitle is None and self.subtitle_absence is None:
            raise ValueError(
                "subtitle is null, so subtitle_absence is required and must be one of "
                "'not_applicable_no_audio' (the media carries no audio and no on-screen text) "
                "or 'attempted_none_found' (transcription ran and produced nothing)")
        if self.subtitle is not None and self.subtitle_absence is not None:
            raise ValueError(
                "subtitle_absence must be null when a subtitle is present; "
                f"got subtitle_absence={self.subtitle_absence!r} alongside a transcript")

        # FR-012, also enforced by uq_attribute_dimension at the database
        # boundary. Checked here too so the retry can name the offender.
        dimensions = [a.dimension for a in self.attributes]
        repeated = sorted({d for d in dimensions if dimensions.count(d) > 1})
        if repeated:
            raise ValueError(
                f"at most one value per attribute dimension; "
                f"dimension(s) {repeated} carry more than one")

        return self


class ExtractionInvalid(Exception):
    """Validation failed. Carries the classification AND the quotable error text.

    `errors` is what the retry sends back to the model verbatim (FR-018). It is
    the validator's own message, never a paraphrase — a paraphrase would be a
    second place for the rules to be stated, and it would drift.
    """

    def __init__(self, failure_kind: str, errors: list[str], raw: str = ""):
        self.failure_kind = failure_kind
        self.errors = errors
        self.raw = raw
        # Populated by the retry orchestration when this escapes as a quarantine:
        # BOTH attempts' errors (FR-019) and the last ModelCall, whose usage is
        # what stops a twice-failed extraction being recorded as free.
        self.attempts: list[dict] = []
        self.call = None
        super().__init__(f"{failure_kind}: {'; '.join(errors)}")


def _readable_errors(exc: ValidationError) -> list[str]:
    """Flatten pydantic's error list into lines a model can act on.

    Location first, because "beats.2.function" tells the model exactly which
    element to fix — the single most useful thing in the message.
    """
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"{loc}: {err['msg']}")
    return lines


def validate_extraction(
    payload: Any,
    beat_functions: Iterable[str],
    attribute_terms: Optional[dict[str, set[str]]] = None,
) -> ExtractionResult:
    """Validate one parsed model response against the schema and the vocabulary.

    Args:
        payload: the parsed JSON body.
        beat_functions: the closed set of legal beat functions AT THE VERSION
            THIS EXTRACTION RECORDS. Passed in rather than imported so a v2 run
            and a v1 run can validate side by side (FR-030).
        attribute_terms: {dimension: {legal values}} at that version. Empty or
            None means the vocabulary publishes no dimensions — in which case any
            attribute at all is invalid, because nothing legitimises it.

    Raises ExtractionInvalid, never returns something unvalidated.
    """
    if not isinstance(payload, dict):
        raise ExtractionInvalid(
            "schema_invalid",
            [f"(root): expected a JSON object, got {type(payload).__name__}"],
        )

    try:
        result = ExtractionResult.model_validate(payload)
    except ValidationError as e:
        raise ExtractionInvalid("schema_invalid", _readable_errors(e)) from e

    legal = set(beat_functions)
    if not legal:
        # A caller that could not load the vocabulary must not silently accept
        # everything. Failing here is what stops an empty vocabulary from being
        # indistinguishable from a permissive one.
        raise ExtractionInvalid(
            "out_of_vocabulary",
            ["the beat-function vocabulary is empty; no function can be validated against it"],
        )

    offenders = sorted({b.function for b in result.beats if b.function not in legal})
    if offenders:
        # NEVER coerced onto the residual (FR-006). Coercion would hide
        # vocabulary drift behind a plausible value, and FR-005's residual-share
        # measurement would then be reading our own repair rather than the
        # model's difficulty.
        raise ExtractionInvalid(
            "out_of_vocabulary",
            [f"beat function {o!r} is not one of: {', '.join(sorted(legal))}" for o in offenders],
        )

    terms = attribute_terms or {}
    attr_errors = []
    for a in result.attributes:
        if a.dimension not in terms:
            attr_errors.append(
                f"attribute dimension {a.dimension!r} is not published by this vocabulary version; "
                f"published dimensions: {', '.join(sorted(terms)) or '(none)'}")
        elif a.value not in terms[a.dimension]:
            attr_errors.append(
                f"attribute value {a.value!r} is not legal for dimension {a.dimension!r}; "
                f"legal values: {', '.join(sorted(terms[a.dimension]))}")
    if attr_errors:
        raise ExtractionInvalid("out_of_vocabulary", attr_errors)

    return result
