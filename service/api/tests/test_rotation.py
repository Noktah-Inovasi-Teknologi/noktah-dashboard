"""The shared model rotation (shared/noktah_ai): rules, and the list every service loads."""
from noktah_ai import rotation
from noktah_ai.rotation import Rotation


def test_rotates_after_n_failures_resets_on_success_and_returns_to_first():
    now = [0.0]
    r = Rotation("summary", ["a", "b"], rotate_after=2, back_to_first_after_s=60, clock=lambda: now[0])
    r.failure("a")
    r.success("a")
    r.failure("a")
    assert r.current() == "a", "a success in between resets the count"
    r.failure("a")
    assert r.current() == "b"
    r.failure("a")  # a late report about the model already left
    assert r.state()["consecutive_failures"] == 0
    now[0] = 60
    assert r.current() == "a"


def test_a_late_failure_of_the_previous_model_is_ignored_and_the_list_wraps():
    r = Rotation("image", ["a", "b"], rotate_after=1)
    r.failure("a")
    r.failure("a")
    assert r.current() == "b"
    r.failure("b")
    assert r.current() == "a"


def test_every_case_has_models_a_label_and_a_description():
    cases = rotation.cases()
    assert set(cases) == {"summary", "intake", "intake_image", "generation", "image", "video"}
    for name, c in cases.items():
        assert c["models"] and c["label"] and c["description"] and c["used_by"], name
    assert "qwen/qwen3.7-flash" not in cases["video"]["models"], "video needs audio input"
    assert rotation.load()["summary"].models == cases["summary"]["models"]
