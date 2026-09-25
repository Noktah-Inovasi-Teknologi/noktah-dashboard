"""
Which model a case (image, video) uses right now, from the accepted list in
config/ai/models.yaml, mounted read-only at /app/config/ai.

Each case starts on its first (best value) model. `rotate_after` consecutive
failures on the model in use move the case to the next one, wrapping round; a
success resets the count. After `back_to_first_after_minutes` on a fallback the
case tries its first model again. State is in memory, per process: a restart
starts every case on its first model.

hub-api (service/api/app/ai/rotation.py) and Prefect (service/prefect/tasks/model_rotation.py)
have the same rules (separate services, no shared package); keep the three in step.
"""
import threading
import time
from pathlib import Path
from typing import Callable

import yaml

_HERE = Path(__file__).resolve().parent
# Container: /app/config/ai. Host: <repo>/config/ai (a slice, not an index, for the
# same reason as analyze._CONFIG_CANDIDATES: in the container `parents` is short).
_CANDIDATES = (_HERE / "config" / "ai" / "models.yaml",
               *(p / "config" / "ai" / "models.yaml" for p in _HERE.parents[1:2]))


class Rotation:
    def __init__(self, case: str, models: list[str], rotate_after: int = 3, back_to_first_after_s: float = 3600,
                 clock: Callable[[], float] = time.monotonic):
        if not models:
            raise ValueError(f"no models listed for case {case!r}")
        self.case, self.models = case, list(models)
        self.rotate_after, self.back_after = max(1, rotate_after), back_to_first_after_s
        self._clock = clock
        # roach serves requests from a thread pool: several analyses can report at once.
        self._lock = threading.Lock()
        self._index = 0
        self._failures = 0
        self._rotated_at: float | None = None

    def current(self) -> str:
        with self._lock:
            if self._index and self._rotated_at is not None and self._clock() - self._rotated_at >= self.back_after:
                print(f"[models] {self.case}: back to the first model {self.models[0]}", flush=True)
                self._index, self._failures, self._rotated_at = 0, 0, None
            return self.models[self._index]

    def success(self, model: str) -> None:
        with self._lock:
            if model == self.models[self._index]:
                self._failures = 0

    def failure(self, model: str) -> None:
        """Count a failure of `model`; a late report about a model already rotated away from is ignored."""
        with self._lock:
            if model != self.models[self._index]:
                return
            self._failures += 1
            if self._failures >= self.rotate_after and len(self.models) > 1:
                self._index = (self._index + 1) % len(self.models)
                self._failures, self._rotated_at = 0, self._clock()
                print(f"[models] {self.case}: {model} failed {self.rotate_after} times in a row; "
                      f"now using {self.models[self._index]}", flush=True)

    def state(self) -> dict:
        with self._lock:
            return {"accepted": list(self.models), "current": self.models[self._index],
                    "consecutive_failures": self._failures}


def models_file() -> Path:
    return next((c for c in _CANDIDATES if c.exists()), _CANDIDATES[0])


def load(path: Path | None = None) -> dict[str, Rotation]:
    doc = yaml.safe_load((path or models_file()).read_text(encoding="utf-8"))
    after, back = int(doc.get("rotate_after", 3)), float(doc.get("back_to_first_after_minutes", 60)) * 60
    return {case: Rotation(case, models, after, back) for case, models in (doc.get("cases") or {}).items()}
