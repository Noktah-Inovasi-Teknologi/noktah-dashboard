"""
Guards that must run EVERYWHERE, with or without a database.

These live apart from test_extraction_store.py deliberately. That module is
marked `schema`, so it is skipped wholesale when no PostgreSQL is reachable —
which is the right behaviour for constraint tests and the wrong behaviour for
these two. The static import guard's whole value is that it fails the moment the
wrong line is written, including on a developer machine with no database and in
any CI job that skips the schema suite.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXTRACTION_TASKS = REPO / "service/prefect/tasks/extraction_tasks.py"


def test_extraction_tasks_cannot_reach_the_signal_write_path():
    """Invariant 7, layer one: 'this CANNOT BE WRITTEN'.

    Reusing `social.signal.record` from a backfill is the natural-looking
    mistake — it is the function that already knows how to store an extraction
    result. It also overwrites `subtitle` unconditionally, and
    `harvested_signals` holds exactly ONE row per item, so that overwrite is
    unrecoverable: a re-extraction cannot restore the transcript it replaced,
    only produce a third different one, at cost (research.md R10, FR-009).

    `.claude/rules/backend/songbird.md` documents this exact bug class for the
    metric-refresh path, where writing through the wrong task blanked stored
    analysis "silently, and this feature cannot regenerate it".

    The behavioural snapshot in test_extraction_store.py says "this did not
    happen". This says "this cannot be written", and it is the cheaper of the two
    because a snapshot only fails once someone has ALREADY written the wrong line
    and a test happens to exercise that path.
    """
    src = EXTRACTION_TASKS.read_text(encoding="utf-8")
    assert "social_signal_record" not in src
    assert "from tasks.social_tasks import" not in src
    assert "from ..tasks.social_tasks import" not in src


def test_the_store_task_does_not_touch_harvested_signals():
    """Narrower than the import guard: the write path itself names no signal table."""
    src = EXTRACTION_TASKS.read_text(encoding="utf-8")
    store_body = src.split("async def extraction_record_store")[1].split("\n@task")[0]
    assert "harvested_signals" not in store_body


def test_render_is_deterministic_and_ordered():
    """FR-041's readable rendering is a RULE, so Constitution XI forbids a model
    call for it. Deterministic, orderable, and database-free."""
    import sys

    sys.path.insert(0, str(REPO / "service/prefect"))
    from tasks.extraction_tasks import render_beats

    beats = [
        {"position": 2, "function": "main_point", "description": "second"},
        {"position": 1, "function": "hook", "description": "first"},
    ]
    out = render_beats(beats)
    assert out == render_beats(beats)
    assert out == "1. [hook] first\n2. [main_point] second"


def test_the_forward_harvest_path_is_gated_by_the_ceiling():
    """FR-037a says the ceiling is checked before ANY costed run.

    The forward harvest is the CONTINUOUS spender. Wiring the ceiling into only
    the batch flows would leave the largest recurring spend ungated while the
    plan's Constitution XI check claimed PASS on the strength of FR-037a — a
    documented guarantee that the code did not keep.

    Asserted structurally rather than behaviourally because the alternative is
    running a harvest, which costs money and needs a platform request.
    """
    src = (REPO / "service/prefect/flows/common/social_harvest.py").read_text(encoding="utf-8")
    assert "_extraction_ceiling_reached" in src, "the forward path must consult the ceiling"
    assert "extraction_cost_ceiling_check" in src

    # And the gate must sit BEFORE the model call, not after it — a check that
    # runs afterwards has already spent the money it was meant to prevent.
    gate = src.index("_extraction_ceiling_reached(run_logger")
    analyze_call = src.index("await social_item_analyze(content_id")
    assert gate < analyze_call, "the ceiling check must precede the analysis call"


def test_reaching_the_ceiling_defers_extraction_but_not_collection():
    """Collection must survive a spend stop (FR-022's reasoning, applied to cost).

    The media is irreplaceable — a story is gone in 24h and counts are only
    observable now — whereas an extraction can be backfilled next month for a
    fraction of a cent. Halting collection to save extraction spend trades the
    unrecoverable thing for the recoverable one.
    """
    src = (REPO / "service/prefect/flows/common/social_harvest.py").read_text(encoding="utf-8")
    gate = src.index("_extraction_ceiling_reached(run_logger")
    # The upload and signal-record calls must both come AFTER the gate, i.e. the
    # gate does not `continue` past them.
    assert src.index("drive_file_upload(local_path", gate) > gate
    assert src.index("social_signal_record(", gate) > gate
    assert "spend_ceiling_reached" in src


def test_the_failure_vocabulary_has_no_escape_hatch():
    """FR-021. An 'other' bucket would absorb exactly the novel failures worth
    noticing, and this is the axis FR-020's counts group by."""
    import sys

    sys.path.insert(0, str(REPO / "service/prefect"))
    from tasks.extraction_tasks import FAILURE_KINDS

    assert "other" not in FAILURE_KINDS
    assert "unknown" not in FAILURE_KINDS
    assert "misc" not in FAILURE_KINDS
    # The closed set, mirrored by chk_quarantine_failure_kind in migration 009.
    assert FAILURE_KINDS == {
        "schema_invalid", "truncated", "empty_content", "out_of_vocabulary",
        "provider_error", "rate_limited", "media_unavailable", "unsupported_media",
        "spend_ceiling_reached",
    }
