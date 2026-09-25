"""Model rotation (config/ai/models.yaml): the same rules roach tests in test_model_rotation.py."""
from app.ai.rotation import Rotation, load, models_file


def test_rotates_after_n_failures_resets_on_success_and_returns_to_first():
    now = [0.0]
    r = Rotation("summary", ["a", "b"], rotate_after=2, back_to_first_after_s=60, clock=lambda: now[0])
    r.failure("a")
    r.success("a")
    r.failure("a")
    assert r.current() == "a", "a success in between resets the count"
    r.failure("a")
    assert r.current() == "b"
    r.failure("a")  # late report about the model already left
    assert r.state()["consecutive_failures"] == 0
    now[0] = 60
    assert r.current() == "a"


def test_the_repo_list_loads_outside_docker():
    cases = load(models_file("/app/config/ai/models.yaml-not-here"))
    assert cases["summary"].models[0] == "deepseek/deepseek-v4-flash"
    assert {"summary", "image", "video"} <= set(cases)
