"""
Which model a case uses right now, from the accepted list in config/ai/models.yaml.

Each case starts on its first (best value) model. `rotate_after` consecutive
failures on the model in use move the case to the next one, wrapping round; a
success resets the count. After `back_to_first_after_minutes` on a fallback the
case tries its first model again. State is in memory, per process: a restart
starts every case on its first model.

roach has the same rules in service/roach/model_rotation.py (separate service,
no shared package); keep the two in step.
"""
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml

log = logging.getLogger("hub.ai.rotation")


class Rotation:
    def __init__(self, case: str, models: List[str], rotate_after: int = 3, back_to_first_after_s: float = 3600,
                 clock: Callable[[], float] = time.monotonic):
        if not models:
            raise ValueError(f"no models listed for case {case!r}")
        self.case, self.models = case, list(models)
        self.rotate_after, self.back_after = max(1, rotate_after), back_to_first_after_s
        self._clock = clock
        self._lock = threading.Lock()
        self._index = 0
        self._failures = 0
        self._rotated_at: Optional[float] = None

    def current(self) -> str:
        with self._lock:
            if self._index and self._rotated_at is not None and self._clock() - self._rotated_at >= self.back_after:
                log.info("%s: back to the first model %s", self.case, self.models[0])
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
                log.warning("%s: %s failed %d times in a row; now using %s",
                            self.case, model, self.rotate_after, self.models[self._index])

    def state(self) -> Dict[str, object]:
        with self._lock:
            return {"accepted": list(self.models), "current": self.models[self._index],
                    "consecutive_failures": self._failures}


_rotations: Dict[str, Rotation] = {}


def models_file(configured: str) -> Path:
    """The configured path (the container mount), or the repo's copy when running outside Docker."""
    path = Path(configured)
    if path.exists():
        return path
    # <repo>/service/api/app/ai/rotation.py → <repo>. A slice, not an index: in the
    # container this file is /app/app/ai/rotation.py and parents[4] doesn't exist.
    repo = [p / "config" / "ai" / "models.yaml" for p in Path(__file__).resolve().parents[4:5]]
    return next((c for c in repo if c.exists()), path)


def load(path: Path) -> Dict[str, Rotation]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    after, back = int(doc.get("rotate_after", 3)), float(doc.get("back_to_first_after_minutes", 60)) * 60
    return {case: Rotation(case, models, after, back) for case, models in (doc.get("cases") or {}).items()}


def for_case(case: str) -> Rotation:
    if not _rotations:
        from ..settings import get_settings
        _rotations.update(load(models_file(get_settings().ai_models_file)))
    return _rotations[case]
