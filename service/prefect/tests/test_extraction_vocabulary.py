"""
Vocabulary sync tests (feature 007-structured-extraction).

The source-parsing half needs no database and is the part most likely to be
wrong in a way that only shows up when S-05 lands — so it runs everywhere.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "service/prefect"))

from tasks.extraction_vocabulary_tasks import (  # noqa: E402
    VocabularySourceError,
    load_vocabulary,
)

BEAT_BLOCK = """
version: v1
dimensions:
  - dimension: beat_function
    terms:
      - term: hook
        ordinal: 1
        description: opens the content
      - term: unclassified
        ordinal: 2
        is_residual: true
        description: not otherwise describable
"""


def _write(tmp_path, text) -> str:
    path = tmp_path / "vocabulary_v1.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_the_shipped_vocabulary_parses_and_is_deliberately_minimal():
    """research.md R11: four functions plus a residual, and nothing else.

    Pre-loading eight plausible terms would pre-empt the very finding the
    residual-share measurement exists to produce (FR-005).
    """
    version, rows = load_vocabulary()
    assert version == "v1"
    beat_terms = {r["term"] for r in rows if r["dimension"] == "beat_function"}
    assert beat_terms == {"hook", "setup", "main_point", "call_to_action", "unclassified"}
    residuals = [r for r in rows if r["is_residual"]]
    assert len(residuals) == 1 and residuals[0]["term"] == "unclassified"


def test_a_beat_function_dimension_without_a_residual_is_rejected(tmp_path):
    """FR-003 is unsatisfiable without one: `beats` has minItems 1, so the model
    MUST emit a beat and needs a legal way to say 'no discernible structure'."""
    src = _write(tmp_path, """
version: v1
dimensions:
  - dimension: beat_function
    terms:
      - term: hook
        description: opens the content
""")
    with pytest.raises(VocabularySourceError, match="no residual term"):
        load_vocabulary(src)


def test_an_attribute_dimension_without_a_residual_is_ALLOWED(tmp_path):
    """The asymmetry is deliberate, and it is what keeps FR-043a's promise.

    `attributes` may be empty and holds at most one value per dimension, so 'this
    does not apply here' is expressed by OMITTING the dimension. Requiring a
    residual on every dimension would impose a term on S-05 that its vocabulary
    may have no use for — a constraint this feature has no standing to add.
    """
    src = _write(tmp_path, BEAT_BLOCK + """
  - dimension: tone
    terms:
      - term: playful
        description: light and informal
      - term: serious
        description: measured and factual
""")
    version, rows = load_vocabulary(src)
    tone = {r["term"] for r in rows if r["dimension"] == "tone"}
    assert tone == {"playful", "serious"}
    assert not any(r["is_residual"] for r in rows if r["dimension"] == "tone")


def test_two_residuals_in_one_dimension_are_rejected(tmp_path):
    src = _write(tmp_path, """
version: v1
dimensions:
  - dimension: beat_function
    terms:
      - term: hook
        description: opens
        is_residual: true
      - term: unclassified
        description: residual
        is_residual: true
""")
    with pytest.raises(VocabularySourceError, match="2 residual terms"):
        load_vocabulary(src)


def test_two_dimensions_may_share_a_term_string(tmp_path):
    """FR-043a again, from the parser's side.

    An earlier schema draft carried UNIQUE (version, term) and would have made
    this impossible to STORE; nothing should make it impossible to DECLARE either.
    """
    src = _write(tmp_path, BEAT_BLOCK + """
  - dimension: tone
    terms:
      - term: hook
        description: a hooky tone
""")
    _version, rows = load_vocabulary(src)
    assert sum(1 for r in rows if r["term"] == "hook") == 2


def test_a_term_without_a_description_is_rejected(tmp_path):
    """The description is what the prompt is built from; a blank one would
    silently weaken the instruction the model is given."""
    src = _write(tmp_path, """
version: v1
dimensions:
  - dimension: beat_function
    terms:
      - term: hook
        description: ""
      - term: unclassified
        description: residual
        is_residual: true
""")
    with pytest.raises(VocabularySourceError, match="no description"):
        load_vocabulary(src)


def test_a_duplicate_term_is_rejected(tmp_path):
    src = _write(tmp_path, """
version: v1
dimensions:
  - dimension: beat_function
    terms:
      - term: hook
        description: opens
      - term: hook
        description: opens again
      - term: unclassified
        description: residual
        is_residual: true
""")
    with pytest.raises(VocabularySourceError, match="duplicate term"):
        load_vocabulary(src)


def test_a_missing_version_is_rejected(tmp_path):
    src = _write(tmp_path, """
dimensions:
  - dimension: beat_function
    terms:
      - term: unclassified
        description: residual
        is_residual: true
""")
    with pytest.raises(VocabularySourceError, match="missing top-level 'version'"):
        load_vocabulary(src)
