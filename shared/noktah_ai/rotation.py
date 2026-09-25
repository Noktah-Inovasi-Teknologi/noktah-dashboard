"""
Which model an AI case uses right now, from the accepted list in models.yaml next
to this file. The one copy of these rules: hub-api, roach and Prefect all import it.

Each case starts on its first (best value) model. `rotate_after` consecutive
failures on the model in use move the case to the next one, wrapping round; a
success resets the count. After `back_to_first_after_minutes` on a fallback the
case tries its first model again. State is in memory, per process: a restart
starts every case on its first model. A Prefect flow run is its own process, so
there rotation lasts one run.

    from noktah_ai import rotation
    models = rotation.for_case("summary")
    model = models.current()
    ... call ...; models.success(model)  or  models.failure(model)
"""
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml

MODELS_FILE = Path(__file__).resolve().with_name("models.yaml")


class Rotation:
    def __init__(self, case: str, models: List[str], rotate_after: int = 3, back_to_first_after_s: float = 3600,
                 clock: Callable[[], float] = time.monotonic):
        if not models:
            raise ValueError(f"no models listed for case {case!r}")
        self.case, self.models = case, list(models)
        self.rotate_after, self.back_after = max(1, rotate_after), back_to_first_after_s
        self._clock = clock
        # roach answers requests from a thread pool: several calls can report at once.
        self._lock = threading.Lock()
        self._index = 0
        self._failures = 0
        self._rotated_at: Optional[float] = None

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

    def state(self) -> Dict[str, object]:
        with self._lock:
            return {"accepted": list(self.models), "current": self.models[self._index],
                    "consecutive_failures": self._failures}


def read(path: Optional[Path] = None) -> dict:
    return yaml.safe_load((path or MODELS_FILE).read_text(encoding="utf-8"))


def cases(path: Optional[Path] = None) -> Dict[str, dict]:
    """Each case's label, used_by, description and models, for display (the Hub's Biaya AI page)."""
    return {name: {"label": c.get("label", name), "used_by": c.get("used_by", ""),
                   "description": c.get("description", ""), "models": list(c.get("models") or [])}
            for name, c in (read(path).get("cases") or {}).items()}


def load(path: Optional[Path] = None) -> Dict[str, Rotation]:
    doc = read(path)
    after, back = int(doc.get("rotate_after", 3)), float(doc.get("back_to_first_after_minutes", 60)) * 60
    return {name: Rotation(name, c.get("models") or [], after, back) for name, c in (doc.get("cases") or {}).items()}


_rotations: Dict[str, Rotation] = {}
_lock = threading.Lock()


def for_case(case: str) -> Rotation:
    """This process's rotation for `case`, loaded from models.yaml on first use."""
    with _lock:
        if not _rotations:
            _rotations.update(load())
        return _rotations[case]


def reset(rotations: Optional[Dict[str, Rotation]] = None) -> Dict[str, Rotation]:
    """Replace this process's rotations (tests): a fresh load, or the given ones."""
    with _lock:
        _rotations.clear()
        _rotations.update(rotations if rotations is not None else load())
        return _rotations
