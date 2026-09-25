"""
Schema and constraint tests for migration 009 (feature 007-structured-extraction).

Runs against a REAL disposable PostgreSQL database, per
`.claude/rules/backend/schema.md`: composite foreign keys, partial unique
indexes, and CHECK behaviour around NULL are exactly the class of bug that passes
against a mock and fails against Postgres.

What is asserted here is the half of the feature the database enforces on its
own. FR-016 requires that no code path storing unvalidated output can exist —
which means the validator is not the only line of defence, and these constraints
are what remain true even if a task is bypassed entirely.
"""
import uuid

import asyncpg
import pytest

# Both markers module-wide: `schema` gates on a reachable database (conftest
# auto-skips when there is none), `asyncio` is what pytest-asyncio needs in its
# default strict mode. Existing suites apply the latter per test; at this file's
# size that is 25 identical decorators.
pytestmark = [pytest.mark.schema, pytest.mark.asyncio]


VOCAB = [
    ("v1", "beat_function", "hook", False, 1, "opens the content"),
    ("v1", "beat_function", "setup", False, 2, "establishes context"),
    ("v1", "beat_function", "main_point", False, 3, "the substantive content"),
    ("v1", "beat_function", "call_to_action", False, 4, "asks the viewer to act"),
    ("v1", "beat_function", "unclassified", True, 5, "structure not otherwise describable"),
]


async def _seed_vocabulary(db, version="v1"):
    for v, dim, term, residual, ordinal, desc in VOCAB:
        await db.execute(
            "INSERT INTO extraction_vocabulary_terms "
            "(version, dimension, term, is_residual, ordinal, description) "
            "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
            version, dim, term, residual, ordinal, desc,
        )


async def _insert_extraction(db, content_id="c1", **overrides):
    row = {
        "platform": "instagram", "content_id": content_id, "purpose": "production",
        "content_type": "video", "media_path": "video", "subtitle": "hello",
        "subtitle_absence": None, "summary": "a summary",
        "model_requested": "m/req", "model_served": "m/served", "prompt_version": "v1",
        "schema_version": "v1", "vocabulary_version": "v1", "content_hash": "sha256:abc",
    }
    row.update(overrides)
    return await db.fetchval(
        """
        INSERT INTO content_extractions
          (platform, content_id, purpose, content_type, media_path, subtitle,
           subtitle_absence, summary, model_requested, model_served,
           prompt_version, schema_version, vocabulary_version, content_hash)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) RETURNING id
        """,
        row["platform"], row["content_id"], row["purpose"], row["content_type"],
        row["media_path"], row["subtitle"], row["subtitle_absence"], row["summary"],
        row["model_requested"], row["model_served"], row["prompt_version"],
        row["schema_version"], row["vocabulary_version"], row["content_hash"],
    )


async def _insert_beat(db, extraction_id, position, function, version="v1", description="d"):
    return await db.execute(
        "INSERT INTO extraction_beats "
        "(extraction_id, vocabulary_version, position, function, description) "
        "VALUES ($1,$2,$3,$4,$5)",
        extraction_id, version, position, function, description,
    )


# ---------------------------------------------------------------------------
# The vocabulary is closed at the database boundary, not only in the validator
# ---------------------------------------------------------------------------

async def test_out_of_vocabulary_beat_function_is_rejected(spine_db):
    """FR-006: an invented term is invalid and MUST NOT be coerced to the residual.

    Coercion is the tempting fix — 'demonstration' is obviously a beat function
    and 'unclassified' is right there. But writing it as unclassified would hide
    vocabulary drift behind a plausible-looking value, and FR-005's residual-share
    measurement (the entire reason v1 is deliberately minimal) would then be
    measuring our own coercion rather than the model's difficulty.
    """
    await _seed_vocabulary(spine_db)
    ex = await _insert_extraction(spine_db)

    await _insert_beat(spine_db, ex, 1, "hook")  # in vocabulary: fine

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _insert_beat(spine_db, ex, 2, "demonstration")


async def test_beat_cannot_cite_a_different_vocabulary_version_than_its_extraction(spine_db):
    """The beat's version is held equal to its parent's by fk_beat_extraction_vocab.

    Without this a beat could be validated against v2's membership while its
    extraction claimed v1, making FR-030 ('interpretable against the version it
    recorded') false for that row while every constraint still passed.
    """
    await _seed_vocabulary(spine_db, "v1")
    await _seed_vocabulary(spine_db, "v2")
    ex = await _insert_extraction(spine_db)  # records v1

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _insert_beat(spine_db, ex, 1, "hook", version="v2")


async def test_two_dimensions_may_share_a_term_string_in_one_version(spine_db):
    """FR-043a: S-05 must be able to populate this mechanism without a second one.

    An earlier design carried UNIQUE (version, term) to support a shorter beats
    FK. It would have rejected this insert — and a future attribute dimension
    publishing a value like 'hook' is entirely plausible. The beats FK targets the
    full primary key instead, so uniqueness is scoped per dimension.
    """
    await _seed_vocabulary(spine_db)
    await spine_db.execute(
        "INSERT INTO extraction_vocabulary_terms "
        "(version, dimension, term, is_residual, ordinal, description) "
        "VALUES ('v1', 'tone', 'hook', false, 1, 'a hooky tone')"
    )
    n = await spine_db.fetchval(
        "SELECT count(*) FROM extraction_vocabulary_terms WHERE version='v1' AND term='hook'")
    assert n == 2


async def test_at_most_one_residual_per_dimension_per_version(spine_db):
    await _seed_vocabulary(spine_db)
    with pytest.raises(asyncpg.UniqueViolationError):
        await spine_db.execute(
            "INSERT INTO extraction_vocabulary_terms "
            "(version, dimension, term, is_residual, ordinal, description) "
            "VALUES ('v1', 'beat_function', 'other', true, 6, 'a second residual')"
        )


# ---------------------------------------------------------------------------
# Beat ordering
# ---------------------------------------------------------------------------

async def test_duplicate_beat_position_is_rejected(spine_db):
    await _seed_vocabulary(spine_db)
    ex = await _insert_extraction(spine_db)
    await _insert_beat(spine_db, ex, 1, "hook")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_beat(spine_db, ex, 1, "setup")


async def test_zero_and_negative_positions_are_rejected(spine_db):
    await _seed_vocabulary(spine_db)
    ex = await _insert_extraction(spine_db)
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_beat(spine_db, ex, 0, "hook")


async def test_contiguity_is_not_a_row_constraint_and_needs_the_invariant_query(spine_db):
    """A GAP looks like perfectly valid data to every per-row check.

    This test documents the limit deliberately rather than pretending the schema
    covers it: positions [1, 3] insert cleanly. Contiguity is enforced in the
    validator and asserted over the whole table by the invariant query below,
    which is the only thing that catches it after the fact.
    """
    await _seed_vocabulary(spine_db)
    ex = await _insert_extraction(spine_db)
    await _insert_beat(spine_db, ex, 1, "hook")
    await _insert_beat(spine_db, ex, 3, "setup")  # inserts fine — the gap is invisible here

    offenders = await spine_db.fetch(
        "SELECT extraction_id FROM extraction_beats GROUP BY extraction_id "
        "HAVING count(*) <> max(position) OR min(position) <> 1"
    )
    assert len(offenders) == 1, "the invariant query is what catches a gap; it must"


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------

async def test_second_value_for_one_dimension_is_rejected(spine_db):
    """FR-012 enforced at the database boundary, not merely in the validator."""
    await _seed_vocabulary(spine_db)
    await spine_db.execute(
        "INSERT INTO extraction_vocabulary_terms "
        "(version, dimension, term, is_residual, ordinal, description) VALUES "
        "('v1','tone','playful',false,1,'playful'),"
        "('v1','tone','serious',false,2,'serious')"
    )
    ex = await _insert_extraction(spine_db)

    await spine_db.execute(
        "INSERT INTO extraction_attributes (extraction_id, dimension, value, confidence, vocabulary_version) "
        "VALUES ($1,'tone','playful','high','v1')", ex)
    with pytest.raises(asyncpg.UniqueViolationError):
        await spine_db.execute(
            "INSERT INTO extraction_attributes (extraction_id, dimension, value, confidence, vocabulary_version) "
            "VALUES ($1,'tone','serious','low','v1')", ex)


async def test_confidence_is_a_closed_ordered_scale(spine_db):
    """A self-reported float would imply a calibration the model does not have."""
    await _seed_vocabulary(spine_db)
    await spine_db.execute(
        "INSERT INTO extraction_vocabulary_terms "
        "(version, dimension, term, is_residual, ordinal, description) "
        "VALUES ('v1','tone','playful',false,1,'playful')")
    ex = await _insert_extraction(spine_db)
    with pytest.raises(asyncpg.CheckViolationError):
        await spine_db.execute(
            "INSERT INTO extraction_attributes (extraction_id, dimension, value, confidence, vocabulary_version) "
            "VALUES ($1,'tone','playful','0.83','v1')", ex)


# ---------------------------------------------------------------------------
# Versioning and idempotence
# ---------------------------------------------------------------------------

async def test_duplicate_extraction_at_the_same_version_is_rejected(spine_db):
    """uq_extraction_version IS FR-036's idempotence.

    A re-run conflicts BEFORE any model call, so a bug in the flow's skip logic
    cannot cause a double charge — the database refuses the write regardless.
    """
    await _seed_vocabulary(spine_db)
    await _insert_extraction(spine_db, content_id="dup")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_extraction(spine_db, content_id="dup")


async def test_a_new_version_does_not_collide_with_the_old_one(spine_db):
    """FR-029: introducing a version must not invalidate or displace prior rows."""
    await _seed_vocabulary(spine_db, "v1")
    await _seed_vocabulary(spine_db, "v2")
    await _insert_extraction(spine_db, content_id="same")
    await _insert_extraction(spine_db, content_id="same", schema_version="v2", vocabulary_version="v2")

    rows = await spine_db.fetch(
        "SELECT schema_version FROM content_extractions WHERE content_id='same' ORDER BY schema_version")
    assert [r["schema_version"] for r in rows] == ["v1", "v2"]


async def test_calibration_and_production_coexist_for_one_item(spine_db):
    """Every calibration item is extracted twice; `purpose` is what keeps SC-018 true."""
    await _seed_vocabulary(spine_db)
    await _insert_extraction(spine_db, content_id="cal")
    await _insert_extraction(spine_db, content_id="cal", purpose="calibration",
                             model_requested="other/model")
    assert await spine_db.fetchval(
        "SELECT count(*) FROM content_extractions WHERE content_id='cal' AND purpose='production'") == 1


# ---------------------------------------------------------------------------
# Absence
# ---------------------------------------------------------------------------

async def test_null_subtitle_with_null_reason_is_rejected(spine_db):
    """FR-010's teeth: an empty value alone may no longer stand for three facts.

    Note both terms of the CHECK are IS NOT NULL predicates, which never evaluate
    to NULL — so this genuinely rejects, rather than passing on NULL the way a
    naively-written CHECK does (the trap feature 005 hit with
    chk_capture_outcomes_reason).
    """
    await _seed_vocabulary(spine_db)
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_extraction(spine_db, content_id="noabs", subtitle=None, subtitle_absence=None)


async def test_null_subtitle_with_a_reason_is_accepted(spine_db):
    await _seed_vocabulary(spine_db)
    ex = await _insert_extraction(
        spine_db, content_id="abs", media_path="image", content_type="image",
        subtitle=None, subtitle_absence="not_applicable_no_audio")
    assert ex is not None


async def test_absence_vocabulary_is_closed(spine_db):
    await _seed_vocabulary(spine_db)
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_extraction(spine_db, content_id="badabs", subtitle=None,
                                 subtitle_absence="dunno")


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------

async def _insert_quarantine(db, **overrides):
    row = {
        "platform": "instagram", "content_id": "q1", "purpose": "production",
        "failure_kind": "schema_invalid", "raw_output": "{bad",
        "model_requested": "m", "prompt_version": "v1",
        "schema_version": "v1", "vocabulary_version": "v1",
    }
    row.update(overrides)
    return await db.execute(
        "INSERT INTO extraction_quarantine "
        "(platform, content_id, purpose, failure_kind, raw_output, model_requested, "
        " prompt_version, schema_version, vocabulary_version) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        row["platform"], row["content_id"], row["purpose"], row["failure_kind"],
        row["raw_output"], row["model_requested"], row["prompt_version"],
        row["schema_version"], row["vocabulary_version"],
    )


async def test_failure_kind_vocabulary_is_closed_with_no_escape_hatch(spine_db):
    """There is deliberately no 'other'.

    An escape hatch would quietly absorb exactly the novel failures worth
    noticing, and this column is what FR-020's counts are grouped by. A tenth
    kind must be added by migration, on purpose.
    """
    await _insert_quarantine(spine_db)  # a known kind is fine
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_quarantine(spine_db, content_id="q2", failure_kind="other")


async def test_no_call_outcomes_are_recordable_without_model_output(spine_db):
    """media_unavailable / spend_ceiling_reached burned no tokens and produced no text.

    They still get a row. A skipped extraction leaving no record anywhere is the
    silent drop Constitution V forbids, and would make SC-010's 'exactly one
    outcome per item' unverifiable.
    """
    await _insert_quarantine(spine_db, content_id="gone", failure_kind="media_unavailable",
                             raw_output=None)
    await _insert_quarantine(spine_db, content_id="broke", failure_kind="spend_ceiling_reached",
                             raw_output=None)
    assert await spine_db.fetchval("SELECT count(*) FROM extraction_quarantine") == 2


async def test_quarantine_counts_group_by_the_four_required_axes(spine_db):
    """FR-020: countable by model, prompt version, schema version, and failure kind."""
    await _insert_quarantine(spine_db, content_id="a", failure_kind="truncated")
    await _insert_quarantine(spine_db, content_id="b", failure_kind="truncated")
    await _insert_quarantine(spine_db, content_id="c", failure_kind="out_of_vocabulary")
    rows = await spine_db.fetch(
        "SELECT failure_kind, model_requested, prompt_version, schema_version, count(*) AS n "
        "FROM extraction_quarantine GROUP BY 1,2,3,4 ORDER BY n DESC")
    assert rows[0]["failure_kind"] == "truncated" and rows[0]["n"] == 2


# ---------------------------------------------------------------------------
# Batch runs
# ---------------------------------------------------------------------------

async def _insert_run(db, kind="extraction", flow_name="roach-extract-backfill"):
    return await db.fetchval(
        "INSERT INTO runs (kind, flow_name) VALUES ($1,$2) RETURNING id", kind, flow_name)


async def test_runs_kind_accepts_extraction_and_still_accepts_the_original_two(spine_db):
    """The widening must be a SUPERSET. A widening that drops a value is a tightening."""
    for kind in ("collection", "generation", "extraction"):
        assert await _insert_run(spine_db, kind=kind) is not None
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_run(spine_db, kind="nonsense")


async def test_a_measured_projection_must_carry_a_measurement(spine_db):
    """Constitution VI: an estimate may not wear a measurement's form.

    projection_basis='measured' with no figure and no sample size is the exact
    shape of a fabricated number, so the database refuses it.
    """
    run_id = await _insert_run(spine_db)
    with pytest.raises(asyncpg.CheckViolationError):
        await spine_db.execute(
            "INSERT INTO extraction_batch_runs "
            "(run_id, batch_kind, mode, scope_description, projection_basis) "
            "VALUES ($1,'backfill','dry_run','everything','measured')", run_id)


async def test_an_uncalibrated_projection_may_carry_no_figure(spine_db):
    """The honest first output: no baseline exists, so no number is asserted."""
    run_id = await _insert_run(spine_db)
    await spine_db.execute(
        "INSERT INTO extraction_batch_runs "
        "(run_id, batch_kind, mode, scope_description, projection_basis, items_in_scope) "
        "VALUES ($1,'backfill','dry_run','everything present at start','uncalibrated',819)", run_id)
    row = await spine_db.fetchrow("SELECT * FROM extraction_batch_runs WHERE run_id=$1", run_id)
    assert row["projection_usd"] is None and row["items_in_scope"] == 819


async def test_unclassified_outcomes_are_recordable_not_rejected(spine_db):
    """SC-010 wants them COUNTED.

    A CHECK forbidding items_unclassified > 0 would make the run fail to write
    its own summary rather than report the defect — the failure would be hidden
    by the very constraint meant to prevent it.
    """
    run_id = await _insert_run(spine_db)
    await spine_db.execute(
        "INSERT INTO extraction_batch_runs "
        "(run_id, batch_kind, mode, scope_description, items_in_scope, items_unclassified) "
        "VALUES ($1,'backfill','real','all',10,3)", run_id)
    assert await spine_db.fetchval(
        "SELECT items_unclassified FROM extraction_batch_runs WHERE run_id=$1", run_id) == 3


# ---------------------------------------------------------------------------
# The status view
# ---------------------------------------------------------------------------

async def test_status_view_universe_is_the_union_not_extractions_alone(spine_db):
    """An item with no extraction must still appear, with a reason.

    Driven by extractions alone it would vanish, having neither a result nor a
    reason — precisely what SC-010 forbids. Feature 006 found 2 of 819 rows in
    exactly that position, and only against real data.
    """
    await _seed_vocabulary(spine_db)
    await spine_db.execute(
        "INSERT INTO harvested_signals (platform, content_id, profile_key, content_type) "
        "VALUES ('instagram','orphan','acct','video')")
    rows = await spine_db.fetch(
        "SELECT reason FROM extraction_status WHERE content_id = 'orphan'")
    assert len(rows) == 1 and rows[0]["reason"] == "never_attempted"


async def test_status_view_never_returns_unresolved(spine_db):
    """An UNRESOLVED row is a bug: an item fell through every branch."""
    await _seed_vocabulary(spine_db)
    await spine_db.execute(
        "INSERT INTO harvested_signals (platform, content_id, profile_key, content_type) VALUES "
        "('instagram','s1','a','video'), ('instagram','s2','a','image')")
    ex = await _insert_extraction(spine_db, content_id="s1")
    await _insert_beat(spine_db, ex, 1, "hook")
    await _insert_quarantine(spine_db, content_id="s2", failure_kind="truncated")
    await _insert_extraction(spine_db, content_id="calonly", purpose="calibration")

    unresolved = await spine_db.fetchval(
        "SELECT count(*) FROM extraction_status WHERE reason = 'UNRESOLVED'")
    assert unresolved == 0

    reasons = {r["content_id"]: r["reason"] for r in
               await spine_db.fetch("SELECT content_id, reason FROM extraction_status")}
    assert reasons["s1"] == "extracted"
    assert reasons["s2"] == "quarantined"
    assert reasons["calonly"] == "calibration_only"


async def test_never_attempted_is_tested_before_the_version_branches(spine_db):
    """Branch order is load-bearing.

    An item with zero rows must classify as never_attempted, not fall through
    into superseded_version_only — the same ordering trap feature 006 hit with
    never_observed vs observed_once.
    """
    await _seed_vocabulary(spine_db, "v1")
    await _seed_vocabulary(spine_db, "v2")   # current version is now v2
    await spine_db.execute(
        "INSERT INTO harvested_signals (platform, content_id, profile_key, content_type) VALUES "
        "('instagram','none','a','video'), ('instagram','old','a','video')")
    await _insert_extraction(spine_db, content_id="old")  # recorded at v1, superseded

    reasons = {r["content_id"]: r["reason"] for r in
               await spine_db.fetch("SELECT content_id, reason FROM extraction_status")}
    assert reasons["none"] == "never_attempted"
    assert reasons["old"] == "superseded_version_only"
