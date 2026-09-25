"""
Validation, retry, and quarantine tests (feature 007-structured-extraction).

FIXTURE-DRIVEN, NO NETWORK. The Constitution requires extraction logic be
developable against fixtures rather than live targets, and every rejection rule
in contracts/validation-outcome.md is only observable by feeding the validator a
bad response on purpose — which is not something a live model can be asked for.

The truncation fixture is the important one and the reason ordering matters: its
payload is WELL-FORMED JSON THAT SATISFIES THE SCHEMA. No parser and no schema
validator can reject it. Only `finish_reason` can.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze  # noqa: E402
from extraction_models import ExtractionInvalid, validate_extraction  # noqa: E402

VOCAB = analyze.load_vocabulary()
BEAT_FUNCTIONS = VOCAB["beat_functions"]


def _valid_payload(**overrides):
    payload = {
        "subtitle": "halo semuanya",
        "subtitle_absence": None,
        "beats": [
            {"position": 1, "function": "hook", "description": "opens with a question"},
            {"position": 2, "function": "main_point", "description": "explains the procedure"},
        ],
        "attributes": [],
        "summary": "A short explainer about an eye procedure.",
    }
    payload.update(overrides)
    return payload


def _validate(payload):
    return validate_extraction(payload, beat_functions=BEAT_FUNCTIONS, attribute_terms={})


# ---------------------------------------------------------------------------
# The nine rejection fixtures (quickstart.md § 3)
# ---------------------------------------------------------------------------

def test_a_valid_payload_passes():
    result = _validate(_valid_payload())
    assert [b.position for b in result.beats] == [1, 2]
    assert result.subtitle == "halo semuanya"


def test_empty_beats_is_rejected():
    """FR-003. 'No discernible structure' is ONE residual beat, not zero beats."""
    with pytest.raises(ExtractionInvalid) as e:
        _validate(_valid_payload(beats=[]))
    assert e.value.failure_kind == "schema_invalid"


def test_non_contiguous_positions_are_rejected():
    """[1, 3] — a gap. Valid JSON, valid types, valid enum. Only this catches it."""
    with pytest.raises(ExtractionInvalid) as e:
        _validate(_valid_payload(beats=[
            {"position": 1, "function": "hook", "description": "a"},
            {"position": 3, "function": "setup", "description": "b"},
        ]))
    assert e.value.failure_kind == "schema_invalid"
    assert any("contiguous" in msg for msg in e.value.errors)


def test_duplicate_positions_are_rejected():
    with pytest.raises(ExtractionInvalid) as e:
        _validate(_valid_payload(beats=[
            {"position": 1, "function": "hook", "description": "a"},
            {"position": 1, "function": "setup", "description": "b"},
        ]))
    assert any("unique" in msg for msg in e.value.errors)


def test_out_of_vocabulary_function_is_rejected_and_NOT_coerced():
    """FR-006. The tempting fix is to write it as `unclassified` — that is the bug.

    Coercion would hide vocabulary drift behind a plausible value, and FR-005's
    residual-share measurement (the entire reason v1 is deliberately minimal)
    would then be measuring our own repair rather than the model's difficulty.
    """
    with pytest.raises(ExtractionInvalid) as e:
        _validate(_valid_payload(beats=[
            {"position": 1, "function": "demonstration", "description": "a"},
        ]))
    assert e.value.failure_kind == "out_of_vocabulary"
    assert "demonstration" in e.value.errors[0]
    # And specifically: it did not quietly become the residual.
    assert "unclassified" not in str(e.value.errors[0]).split("is not one of")[0]


def test_flow_returned_as_an_array_is_rejected():
    """The old `_to_text` newline-joined arrays 'because the model sometimes
    returns arrays despite the schema'. That is a schema violation being silently
    repaired; under FR-006 it is a retry."""
    with pytest.raises(ExtractionInvalid) as e:
        _validate({"subtitle": "x", "subtitle_absence": None,
                   "flow": ["step one", "step two"], "summary": "s"})
    assert e.value.failure_kind == "schema_invalid"


def test_two_values_for_one_dimension_are_rejected():
    with pytest.raises(ExtractionInvalid) as e:
        validate_extraction(
            _valid_payload(attributes=[
                {"dimension": "tone", "value": "playful", "confidence": "high"},
                {"dimension": "tone", "value": "serious", "confidence": "low"},
            ]),
            beat_functions=BEAT_FUNCTIONS,
            attribute_terms={"tone": {"playful", "serious"}},
        )
    assert "at most one value per attribute dimension" in e.value.errors[0]


def test_null_subtitle_without_a_reason_is_rejected():
    """FR-010: an empty value alone may no longer stand for three different facts."""
    with pytest.raises(ExtractionInvalid) as e:
        _validate(_valid_payload(subtitle=None, subtitle_absence=None))
    assert any("subtitle_absence is required" in m for m in e.value.errors)


def test_null_subtitle_with_a_reason_is_accepted():
    result = _validate(_valid_payload(subtitle=None, subtitle_absence="not_applicable_no_audio"))
    assert result.subtitle is None
    assert result.subtitle_absence == "not_applicable_no_audio"


def test_extra_keys_are_rejected():
    with pytest.raises(ExtractionInvalid):
        _validate(_valid_payload(unexpected="value"))


def test_attributes_are_rejected_when_the_vocabulary_publishes_no_dimensions():
    """FR-043's inert state is EMPTY attributes, not unvalidated ones.

    With no dimensions published there is nothing to legitimise an assignment, so
    one arriving anyway is invalid rather than stored on trust.
    """
    with pytest.raises(ExtractionInvalid) as e:
        _validate(_valid_payload(attributes=[
            {"dimension": "tone", "value": "playful", "confidence": "high"}]))
    assert e.value.failure_kind == "out_of_vocabulary"


def test_an_empty_vocabulary_rejects_rather_than_accepting_everything():
    """A caller that could not load the vocabulary must not become permissive."""
    with pytest.raises(ExtractionInvalid) as e:
        validate_extraction(_valid_payload(), beat_functions=[], attribute_terms={})
    assert e.value.failure_kind == "out_of_vocabulary"


# ---------------------------------------------------------------------------
# Truncation — checked BEFORE parsing
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body
        self.headers = {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _body(content, finish_reason="stop", model="test/model"):
    return {
        "model": model,
        "provider": "testprov",
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.001},
    }


def test_truncated_response_is_rejected_even_though_it_parses(monkeypatch):
    """THE fixture that matters.

    This payload is complete, well-formed JSON satisfying every schema rule —
    two contiguous beats, a valid enum value, a non-empty summary. A parser
    accepts it. A JSON Schema validator accepts it. Provider-side `strict`
    accepts it. It is nonetheless a story that stopped half way, and the ONLY
    signal that says so is finish_reason.
    """
    payload = json.dumps(_valid_payload())
    monkeypatch.setattr(analyze.requests, "post",
                        lambda *a, **kw: _Resp(_body(payload, finish_reason="length")))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    # Sanity: the fixture really is valid if you only look at its content.
    assert _validate(json.loads(payload)) is not None

    with pytest.raises(ExtractionInvalid) as e:
        analyze._call_model("p", [], "m", analyze.load_schema(vocabulary=VOCAB), 100)
    assert e.value.failure_kind == "truncated"
    # The MEASURED COST must survive the exception. A truncated response is the
    # most expensive kind of failure — it ran to the token ceiling, so it burned
    # the full completion budget. Losing it here biases the baseline low in
    # exactly the direction that matters, and it is what happened to the first
    # truncation this feature caught in production.
    assert e.value.call is not None
    assert e.value.call.usage["cost_usd"] == 0.001
    assert e.value.call.model_served == "test/model"


def test_all_empty_responses_classify_as_empty_content_not_provider_error(monkeypatch):
    """FR-021: `empty_content` is its OWN value in the closed vocabulary.

    Filing it as `provider_error` misleads in a specific direction — a provider
    error suggests the provider is unhealthy and the item should be retried
    later, whereas repeated empty content is a property of this item and this
    model. Measured on the first full backfill: 13 of 824 items behaved this way
    and every one was mis-filed as a provider fault.
    """
    monkeypatch.setattr(analyze.requests, "post", lambda *a, **kw: _Resp(_body("")))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    with pytest.raises(ExtractionInvalid) as e:
        analyze._call_model("p", [], "m", analyze.load_schema(vocabulary=VOCAB), 100)
    assert e.value.failure_kind == "empty_content"
    # Empty content is not free: prompt and media were uploaded and billed on
    # every attempt (FR-038).
    assert e.value.call is not None
    assert e.value.call.usage["cost_usd"] == 0.001


def test_unparseable_response_is_not_salvaged(monkeypatch):
    """Prose wrapping a JSON object used to be rescued by the regex fallback."""
    monkeypatch.setattr(analyze.requests, "post", lambda *a, **kw: _Resp(
        _body('Here is the analysis you asked for: {"summary": "s"} — hope that helps!')))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    with pytest.raises(ExtractionInvalid) as e:
        analyze._call_model("p", [], "m", analyze.load_schema(vocabulary=VOCAB), 100)
    assert e.value.failure_kind == "schema_invalid"


# ---------------------------------------------------------------------------
# The retry carries the error (FR-018)
# ---------------------------------------------------------------------------

def test_retry_sends_the_error_back_and_does_not_resend_media(monkeypatch):
    """Exactly two calls; the second carries the first response and the error.

    Also asserts the media is NOT re-sent: it is already in the conversation as
    the first message, and re-uploading base64 video to restate a JSON complaint
    would roughly double the input cost of every retry.
    """
    sent = []
    bad = json.dumps(_valid_payload(beats=[
        {"position": 1, "function": "demonstration", "description": "a"}]))
    good = json.dumps(_valid_payload())
    bodies = [_body(bad), _body(good)]

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return _Resp(bodies[len(sent) - 1])

    monkeypatch.setattr(analyze.requests, "post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    media = [{"type": "video_url", "video_url": {"url": "data:video/mp4;base64,AAAA"}}]
    call, result = analyze._validated_call(
        "prompt", media, "m", 100, "analyze.video", None,
        VOCAB, analyze.load_schema(vocabulary=VOCAB),
    )

    assert len(sent) == 2, "exactly one retry — not zero, not two"
    assert call.attempts == 2
    assert result.beats[0].function == "hook"

    second = sent[1]["messages"]
    # The first user message (with the media) is carried forward unchanged...
    assert second[0]["content"][1] == media[0]
    # ...and the media appears exactly once across the whole conversation.
    media_parts = [p for m in second for p in (m["content"] if isinstance(m["content"], list) else [])
                   if isinstance(p, dict) and p.get("type") == "video_url"]
    assert len(media_parts) == 1, "the media must not be re-sent on the retry"

    # The follow-up turns: the model's own invalid output, then the validator's
    # own error text QUOTED — not paraphrased.
    assert second[1]["role"] == "assistant" and "demonstration" in second[1]["content"]
    assert second[2]["role"] == "user"
    assert "demonstration" in second[2]["content"]
    assert "is not one of" in second[2]["content"]


def test_two_failures_produce_a_quarantine_with_both_attempts(monkeypatch):
    """FR-019: retained with raw text, BOTH attempts' errors, and its cost."""
    bad = json.dumps(_valid_payload(beats=[
        {"position": 1, "function": "demonstration", "description": "a"}]))
    monkeypatch.setattr(analyze.requests, "post", lambda *a, **kw: _Resp(_body(bad)))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    with pytest.raises(ExtractionInvalid) as e:
        analyze._validated_call(
            "prompt", [], "m", 100, "analyze.video", None,
            VOCAB, analyze.load_schema(vocabulary=VOCAB),
        )
    assert len(e.value.attempts) == 2
    assert e.value.raw
    # Tokens were spent on both. A quarantine that looks free would bias the
    # measured baseline low.
    assert e.value.call.usage["cost_usd"] == 0.001


def test_analyze_item_returns_a_quarantine_envelope_not_a_raise(monkeypatch, tmp_path):
    """A quarantine rides inside a normal result; the harvest must keep going."""
    img = tmp_path / "1.jpg"
    img.write_bytes(b"img")
    bad = json.dumps({"nonsense": True})
    monkeypatch.setattr(analyze.requests, "post", lambda *a, **kw: _Resp(_body(bad)))
    monkeypatch.setattr(analyze.shutil, "which", lambda name: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    out = analyze.analyze_item([str(img)], "image")
    assert out["status"] == "quarantined"
    assert out["failure_kind"] == "schema_invalid"
    assert out["raw_output"] == bad
    assert len(out["attempts"]) == 2
    assert out["beats"] == []
    assert out["provenance"]["media_path"] == "image"
    assert out["provenance"]["vocabulary_version"] == VOCAB["version"]
    assert out["usage"]["cost_usd"] == 0.001


# ---------------------------------------------------------------------------
# The deletions must be DELETIONS (SC-006)
# ---------------------------------------------------------------------------

SOURCE = (Path(__file__).resolve().parents[1] / "analyze.py").read_text(encoding="utf-8")


def test_the_regex_salvage_fallback_is_absent_from_the_module():
    """Not merely uncalled — ABSENT.

    A test that only stops calling it leaves SC-006 ('zero extractions originate
    from a response that was cut short or pattern-salvaged') unverifiable the
    moment someone reintroduces the path.

    Asserted against the PARSED MODULE, not against its text. A substring check
    over the source cannot tell code from the comment explaining why the code is
    gone — and that comment must stay, or the next reader re-adds the fallback
    for the same reason it existed the first time.
    """
    import ast

    tree = ast.parse(SOURCE)

    imports_re = any(
        (isinstance(n, ast.Import) and any(a.name == "re" for a in n.names))
        or (isinstance(n, ast.ImportFrom) and n.module == "re")
        for n in ast.walk(tree)
    )
    assert not imports_re, "analyze.py no longer needs `re`; a reimport suggests the salvage path is back"

    regex_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "re"
    ]
    assert not regex_calls, f"no regex calls may remain in analyze.py; found {len(regex_calls)}"


def test_the_list_coercing_to_text_helper_is_absent_from_the_module():
    """`_to_text` newline-joined arrays, silently repairing a schema violation."""
    assert "def _to_text" not in SOURCE


def test_render_beats_is_deterministic_and_makes_no_model_call():
    """FR-041's readable rendering is a rule, so Constitution XI forbids a model call."""
    beats = [
        {"position": 2, "function": "main_point", "description": "second"},
        {"position": 1, "function": "hook", "description": "first"},
    ]
    once = analyze.render_beats(beats)
    assert once == analyze.render_beats(beats)
    assert once.splitlines()[0].startswith("1. [hook]")
