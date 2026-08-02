"""
Field availability determination tests (feature 005-signal-field-coverage).

Two layers, both load-bearing:

  * Source validation — the YAML is the authoritative record of what is
    knowable. A malformed one must be rejected WHOLE, because importing the
    valid subset lets a determination silently revert to a stale value that
    still reads as authoritative to every consumer.
  * Database constraints — the status vocabulary is closed (FR-002a) and is
    enforced at the database boundary, not only in the sync task. This is
    reference data other systems read, so a sixth value must be impossible to
    store even if the task is bypassed.

Requires a real disposable PostgreSQL database (see tests/conftest.py). Skipped
automatically when SPINE_TEST_DATABASE_URL is unreachable.
"""
import textwrap
from datetime import date
from pathlib import Path

import asyncpg
import pytest

from tasks.availability_tasks import DeterminationSourceError, load_determinations
from tests.conftest import pool_from_connection  # noqa: E402

pytestmark = pytest.mark.schema


VALID_ENTRY = """\
version: 1
determinations:
  - platform: instagram
    content_type: carousel
    field: views
    status: unavailable_platform_limit
    reason: Instagram publishes no post-level play count for non-video content.
    evidence: 0/291 instagram carousel rows carry views (measured 2026-08-02).
    determined_on: 2026-08-02
"""


def _write(tmp_path: Path, body: str) -> str:
    p = tmp_path / "field_availability.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


# ==========================================================================
# T012 — source validation
# ==========================================================================

def test_valid_source_parses(tmp_path):
    dets, version = load_determinations(_write(tmp_path, VALID_ENTRY))
    assert len(dets) == 1
    d = dets[0]
    assert d["platform"] == "instagram"
    assert d["field_name"] == "views"
    assert d["status"] == "unavailable_platform_limit"
    assert d["determined_on"] == date(2026, 8, 2)
    assert version, "a content hash must be derived so a row traces to its source revision"


def test_unknown_status_rejected(tmp_path):
    """FR-002a: the vocabulary is closed."""
    bad = VALID_ENTRY.replace("unavailable_platform_limit", "probably_fine")
    with pytest.raises(DeterminationSourceError, match="unknown status"):
        load_determinations(_write(tmp_path, bad))


def test_empty_reason_rejected(tmp_path):
    """A status without a reason is an assertion, not a determination."""
    bad = VALID_ENTRY.replace(
        "reason: Instagram publishes no post-level play count for non-video content.",
        'reason: "   "',
    )
    with pytest.raises(DeterminationSourceError, match="reason"):
        load_determinations(_write(tmp_path, bad))


def test_missing_required_field_rejected(tmp_path):
    bad = "\n".join(
        line for line in VALID_ENTRY.splitlines() if not line.strip().startswith("determined_on")
    )
    with pytest.raises(DeterminationSourceError, match="determined_on"):
        load_determinations(_write(tmp_path, bad))


def test_duplicate_triple_rejected(tmp_path):
    dup = VALID_ENTRY + textwrap.dedent("""\
      - platform: instagram
        content_type: carousel
        field: views
        status: available
        reason: Contradicts the entry above.
        evidence: none
        determined_on: 2026-08-02
    """)
    with pytest.raises(DeterminationSourceError, match="duplicate"):
        load_determinations(_write(tmp_path, dup))


def test_evidence_required_unless_undetermined(tmp_path):
    """
    Only `undetermined` may omit evidence — it is the one status meaning "we
    have not looked", and so has nothing to cite. Every other status is a claim.
    """
    no_evidence = "\n".join(
        line for line in VALID_ENTRY.splitlines() if not line.strip().startswith("evidence")
    )
    with pytest.raises(DeterminationSourceError, match="evidence"):
        load_determinations(_write(tmp_path, no_evidence))

    undetermined = no_evidence.replace("unavailable_platform_limit", "undetermined")
    dets, _ = load_determinations(_write(tmp_path, undetermined))
    assert dets[0]["status"] == "undetermined"
    assert dets[0]["evidence"] is None


def test_unknown_platform_rejected(tmp_path):
    bad = VALID_ENTRY.replace("platform: instagram", "platform: threads")
    with pytest.raises(DeterminationSourceError, match="unknown platform"):
        load_determinations(_write(tmp_path, bad))


def test_malformed_yaml_rejected(tmp_path):
    with pytest.raises(DeterminationSourceError):
        load_determinations(_write(tmp_path, "determinations: [oops\n  - broken"))


def test_missing_determinations_key_rejected(tmp_path):
    with pytest.raises(DeterminationSourceError, match="determinations"):
        load_determinations(_write(tmp_path, "version: 1\n"))


def test_shipped_source_file_is_valid():
    """
    The real config/field_availability.yaml must always parse. Without this the
    seed could rot silently and only fail at deploy time.
    """
    root = Path(__file__).resolve().parents[3]
    dets, _ = load_determinations(str(root / "config" / "field_availability.yaml"))
    assert len(dets) > 0

    by_key = {(d["platform"], d["content_type"], d["field_name"]): d for d in dets}

    # The case the original brief missed: stories expose nothing at all.
    assert by_key[("instagram", "story", "likes")]["status"] == "unavailable_platform_limit"

    # Constitution VIII: never claim availability from an unexercised code path.
    assert by_key[("tiktok", "carousel", "shares")]["status"] == "undetermined"

    # FR-023: a policy choice must not masquerade as a platform limit.
    assert by_key[("instagram", "video", "comment_text")]["status"] == "not_collected_by_decision"


# ==========================================================================
# T009 — database-level constraint enforcement
# ==========================================================================

async def _insert_determination(conn, **kw):
    params = {
        "platform": "instagram",
        "content_type": "carousel",
        "field_name": "views",
        "status": "available",
        "reason": "because",
        "evidence": None,
        "determined_on": date(2026, 8, 2),
        "source_version": "abc123",
        **kw,
    }
    return await conn.fetchval(
        """
        INSERT INTO field_availability
            (platform, content_type, field_name, status, reason, evidence, determined_on, source_version)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id
        """,
        params["platform"], params["content_type"], params["field_name"], params["status"],
        params["reason"], params["evidence"], params["determined_on"], params["source_version"],
    )


@pytest.mark.asyncio
async def test_db_rejects_status_outside_closed_vocabulary(spine_db):
    """
    FR-002a enforced where it cannot be bypassed. If this test passes but the
    CHECK is absent, the task-level validation is the only guard and any direct
    SQL write can poison the reference data.
    """
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_determination(spine_db, status="probably_fine")


@pytest.mark.asyncio
async def test_db_accepts_every_member_of_the_vocabulary(spine_db):
    for i, status in enumerate([
        "available", "unavailable_platform_limit",
        "not_collected_by_decision", "inconclusive", "undetermined",
    ]):
        assert await _insert_determination(spine_db, status=status, field_name=f"f{i}")


@pytest.mark.asyncio
async def test_db_rejects_empty_reason(spine_db):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_determination(spine_db, reason="   ")


@pytest.mark.asyncio
async def test_triple_is_unique(spine_db):
    await _insert_determination(spine_db)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_determination(spine_db, status="undetermined", reason="conflicting")


@pytest.mark.asyncio
async def test_determination_has_no_fk_to_observations(spine_db):
    """
    FR-007: updating a determination must never touch a stored observation. The
    structural guarantee is that field_availability holds no foreign key into
    any observation table — so a determination change cannot cascade.
    """
    fks = await spine_db.fetch(
        """
        SELECT conname, pg_get_constraintdef(oid) AS def
        FROM pg_constraint
        WHERE conrelid = 'field_availability'::regclass AND contype = 'f'
        """
    )
    assert fks == [], f"field_availability must hold no foreign keys, found: {fks}"


# ==========================================================================
# T066 — sync idempotence (FR-001b)
# ==========================================================================

@pytest.mark.asyncio
async def test_sync_is_idempotent(spine_db, tmp_path, monkeypatch):
    """
    FR-001b: re-running against an unchanged file must report every row
    `unchanged` and write nothing. Without this, a scheduled sync would rewrite
    `synced_at` on every determination forever, making "when did this actually
    change?" unanswerable from the table.
    """
    import tasks.availability_tasks as av

    monkeypatch.setattr(av, "_db_pool", pool_from_connection(spine_db))
    src = _write(tmp_path, VALID_ENTRY)

    first = await av.availability_determination_sync.fn(path=src)
    assert (first["inserted"], first["updated"], first["unchanged"]) == (1, 0, 0)

    second = await av.availability_determination_sync.fn(path=src)
    assert (second["inserted"], second["updated"], second["unchanged"]) == (0, 0, 1)

    third = await av.availability_determination_sync.fn(path=src)
    assert third["unchanged"] == 1


@pytest.mark.asyncio
async def test_sync_updates_when_determination_changes(spine_db, tmp_path, monkeypatch):
    import tasks.availability_tasks as av

    monkeypatch.setattr(av, "_db_pool", pool_from_connection(spine_db))

    await av.availability_determination_sync.fn(path=_write(tmp_path, VALID_ENTRY))

    changed = VALID_ENTRY.replace("unavailable_platform_limit", "available")
    result = await av.availability_determination_sync.fn(path=_write(tmp_path, changed))
    assert (result["inserted"], result["updated"], result["unchanged"]) == (0, 1, 0)

    status = await spine_db.fetchval(
        "SELECT status FROM field_availability WHERE field_name = 'views'"
    )
    assert status == "available"


@pytest.mark.asyncio
async def test_sync_does_not_delete_rows_absent_from_source(spine_db, tmp_path, monkeypatch):
    """
    A truncate-and-reload would erase a determination outright if the file were
    ever trimmed by accident. Rows absent from the source are left alone.
    """
    import tasks.availability_tasks as av

    monkeypatch.setattr(av, "_db_pool", pool_from_connection(spine_db))
    await _insert_determination(spine_db, field_name="likes", status="available")

    await av.availability_determination_sync.fn(path=_write(tmp_path, VALID_ENTRY))

    assert await spine_db.fetchval(
        "SELECT count(*) FROM field_availability WHERE field_name = 'likes'"
    ) == 1


# ==========================================================================
# T013 — a rejected source must leave the table untouched
# ==========================================================================

@pytest.mark.asyncio
async def test_parse_failure_leaves_existing_rows_untouched(spine_db, tmp_path, monkeypatch):
    """
    A determination silently reverting to a stale value is worse than a visibly
    failed sync, because the stale value still reads as authoritative. Validation
    therefore happens BEFORE the first write, not per row.
    """
    import tasks.availability_tasks as av

    monkeypatch.setattr(av, "_db_pool", pool_from_connection(spine_db))
    await av.availability_determination_sync.fn(path=_write(tmp_path, VALID_ENTRY))
    before = await spine_db.fetchrow(
        "SELECT status, reason, synced_at FROM field_availability WHERE field_name = 'views'"
    )

    # A file whose FIRST entry is valid and second is not — proving rejection is
    # whole-file, not "everything up to the bad row".
    poisoned = VALID_ENTRY.replace(
        "status: unavailable_platform_limit", "status: available"
    ) + textwrap.dedent("""\
      - platform: instagram
        content_type: image
        field: comments
        status: nonsense_value
        reason: should never be written
        evidence: none
        determined_on: 2026-08-02
    """)

    with pytest.raises(DeterminationSourceError):
        await av.availability_determination_sync.fn(path=_write(tmp_path, poisoned))

    after = await spine_db.fetchrow(
        "SELECT status, reason, synced_at FROM field_availability WHERE field_name = 'views'"
    )
    assert after["status"] == before["status"], "the valid first entry must NOT have been applied"
    assert after["synced_at"] == before["synced_at"]
    assert await spine_db.fetchval(
        "SELECT count(*) FROM field_availability WHERE field_name = 'comments'"
    ) == 0


@pytest.mark.asyncio
async def test_validate_only_writes_nothing(spine_db, tmp_path, monkeypatch):
    import tasks.availability_tasks as av

    monkeypatch.setattr(av, "_db_pool", pool_from_connection(spine_db))
    result = await av.availability_determination_sync.fn(
        path=_write(tmp_path, VALID_ENTRY), validate_only=True
    )
    assert result["inserted"] == 1, "the report must count what WOULD change"
    assert await spine_db.fetchval("SELECT count(*) FROM field_availability") == 0
