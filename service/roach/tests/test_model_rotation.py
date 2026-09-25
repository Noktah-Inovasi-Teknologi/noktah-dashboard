"""Model rotation: the accepted list per case, and moving on after repeated failures."""
import pytest

import analyze
import model_rotation
from extraction_models import ExtractionInvalid
from model_rotation import Rotation


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_rotates_after_n_consecutive_failures_and_wraps():
    r = Rotation("video", ["a", "b"], rotate_after=3)
    for _ in range(2):
        r.failure("a")
    assert r.current() == "a", "two failures are not enough"
    r.failure("a")
    assert r.current() == "b"
    for _ in range(3):
        r.failure("b")
    assert r.current() == "a", "wraps round at the end of the list"


def test_a_success_resets_the_count():
    r = Rotation("image", ["a", "b"], rotate_after=3)
    r.failure("a")
    r.failure("a")
    r.success("a")
    r.failure("a")
    r.failure("a")
    assert r.current() == "a"


def test_a_late_failure_of_the_previous_model_is_ignored():
    r = Rotation("image", ["a", "b", "c"], rotate_after=1)
    r.failure("a")
    r.failure("a")  # a call that started on "a" before the rotation
    assert r.current() == "b"


def test_goes_back_to_the_first_model_after_a_while():
    clock = Clock()
    r = Rotation("video", ["a", "b"], rotate_after=1, back_to_first_after_s=3600, clock=clock)
    r.failure("a")
    clock.now = 3599
    assert r.current() == "b"
    clock.now = 3600
    assert r.current() == "a"


def test_the_repo_list_is_what_roach_loads():
    cases = model_rotation.load()
    assert set(cases) >= {"image", "video", "summary"}
    assert "qwen/qwen3.7-flash" not in cases["video"].models, "video needs audio input"
    assert cases["video"].rotate_after >= 1


def _fail_with(exc):
    def call(prompt, parts, model, *args, **kwargs):
        seen.append(model)
        raise exc
    seen: list[str] = []
    return call, seen


def test_analysis_failures_rotate_the_case_and_an_override_never_does(monkeypatch, tmp_path):
    monkeypatch.setattr(analyze, "IMAGE_MODELS", Rotation("image", ["first", "second"], rotate_after=2))
    call, seen = _fail_with(ExtractionInvalid("schema_invalid", ["bad"], raw="{}"))
    monkeypatch.setattr(analyze, "_validated_call", call)
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(analyze, "_compress_image", lambda p: (p, False))

    for _ in range(2):
        out = analyze.analyze_item([str(image)], "image", client="t")
        assert out["status"] == "quarantined" and out["provenance"]["model_requested"] == "first"
    out = analyze.analyze_item([str(image)], "image", client="t")
    assert seen == ["first", "first", "second"]

    analyze.analyze_item([str(image)], "image", client="t", model_override="calibration-model")
    assert analyze.IMAGE_MODELS.state()["consecutive_failures"] == 1, "an override is not counted"


def test_a_provider_error_is_reported_with_the_model_it_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(analyze, "IMAGE_MODELS", Rotation("image", ["first", "second"], rotate_after=1))
    call, _ = _fail_with(RuntimeError("429 rate limited on attempt 4"))
    monkeypatch.setattr(analyze, "_validated_call", call)
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(analyze, "_compress_image", lambda p: (p, False))
    out = analyze.analyze_item([str(image)], "image", client="t")
    assert out["status"] == "failed" and out["provenance"]["model_requested"] == "first"
    assert analyze.IMAGE_MODELS.current() == "second"


def test_a_bug_in_roach_is_not_counted_against_the_model(monkeypatch, tmp_path):
    monkeypatch.setattr(analyze, "IMAGE_MODELS", Rotation("image", ["first", "second"], rotate_after=1))
    call, _ = _fail_with(TypeError("signature drift"))
    monkeypatch.setattr(analyze, "_validated_call", call)
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(analyze, "_compress_image", lambda p: (p, False))
    with pytest.raises(TypeError):
        analyze.analyze_item([str(image)], "image", client="t")
    assert analyze.IMAGE_MODELS.current() == "first"
