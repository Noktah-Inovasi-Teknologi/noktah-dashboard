"""
Dedicated alias-reconciliation tests (feature 004-relational-spine, User Story 2).

test_roster_sync.py already exercises alias creation as part of the full
roster-sync pipeline; this file isolates the alias-specific guarantees FR-002
through FR-004 and SC-004/SC-009 make on their own — conflict rejection,
exact/case-insensitive lookup with no similarity computation, the full
alias-seed table from data-model.md, and that a manual correction survives a
subsequent sync (the regression T033/T035's create-only design exists to
prevent).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

from flows.common import roster as roster_engine  # noqa: E402
from tasks.roster_tasks import _alias_reassign_impl, _upsert_alias_impl  # noqa: E402

pytestmark = pytest.mark.schema


async def _client(conn, key, name):
    return await conn.fetchval(
        "INSERT INTO clients (client_key, display_name) VALUES ($1, $2) RETURNING id", key, name
    )


# ---------------------------------------------------------------------------
# FR-003: an alias resolves to at most one client
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_alias_rejects_conflict_without_aborting(spine_db):
    c1 = await _client(spine_db, "client-a", "Client A")
    c2 = await _client(spine_db, "client-b", "Client B")
    first = await _upsert_alias_impl(spine_db, c1, "Shared Name", "manual")
    assert first["action"] == "created"

    second = await _upsert_alias_impl(spine_db, c2, "Shared Name", "manual")
    assert second["action"] == "conflict"
    assert second["already_owned_by_client_id"] == str(c1)

    # The rejection did not disturb the original alias.
    owner = await spine_db.fetchval(
        "SELECT client_id FROM client_aliases WHERE alias_key = 'shared name'"
    )
    assert owner == c1


# ---------------------------------------------------------------------------
# FR-004: exact, case-insensitive lookup — no similarity computation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alias_lookup_is_case_insensitive_and_exact(spine_db):
    client_id = await _client(spine_db, "client-a", "Client A")
    await _upsert_alias_impl(spine_db, client_id, "LASIK Asyik by SMEC Tebet", "clients_sheet")

    exact_ci = await spine_db.fetchval(
        "SELECT client_id FROM client_aliases WHERE alias_key = lower('lasik asyik by smec tebet')"
    )
    assert exact_ci == client_id

    # A near-miss (not an exact normalized match) must NOT resolve — there is
    # no similarity/token-overlap fallback in the lookup itself (FR-004).
    near_miss = await spine_db.fetchval(
        "SELECT client_id FROM client_aliases WHERE alias_key = lower('lasik asyik')"
    )
    assert near_miss is None


# ---------------------------------------------------------------------------
# SC-004: the full alias-seed table (data-model.md) resolves correctly
# ---------------------------------------------------------------------------

def _full_clients_sheet_rows():
    return [
        {"Name": "Ecky Dental Center", "Instagram": "", "TikTok": ""},
        {"Name": "Nirwana Coffee Space Pamekasan", "Instagram": "", "TikTok": ""},
        {"Name": "Nirwana Coffee Shop Sumenep", "Instagram": "", "TikTok": ""},
        {"Name": "LASIK Asyik by SMEC Tebet", "Instagram": "", "TikTok": ""},
        {"Name": "Klinik Utama Gasa", "Instagram": "", "TikTok": ""},
        {"Name": "RS Mata SMEC Medan", "Instagram": "", "TikTok": ""},
        {"Name": "Pelita Delapan", "Instagram": "", "TikTok": ""},
    ]


def _full_components_block():
    return {
        "Ecky Dental Center": "10000",
        "Nirwana Coffee Space Pamekasan": "10002",
        "Nirwana Coffee Space Sumenep": "10001",
        "LASIK Asyik by SMEC Tebet": "10009",
        "Klinik Utama Gasa": "10139",
        "RS Mata SMEC Medan": "10102",
        "Pelita Delapan": "10100",
    }


def _full_knowledge_base_names():
    """The exact names from data-model.md's Alias seed table."""
    return [
        "Lasik Asyik", "Klinik Utama GASA", "RS Mata SMEC",
        "Toko Bakmi dan Kopitiam Pelita Delapan",
        "Nirwana Pamekasan", "Nirwana Coffee Space", "Nirwana Sumenep",
    ]


@pytest.mark.asyncio
async def test_full_alias_seed_table_resolves_as_documented(spine_db):
    await roster_engine.reconcile_roster(
        spine_db, _full_clients_sheet_rows(), _full_components_block(), {}, _full_knowledge_base_names(),
    )

    expected = {
        "lasik asyik": "LASIK Asyik by SMEC Tebet",
        "klinik utama gasa": "Klinik Utama Gasa",
        "rs mata smec": "RS Mata SMEC Medan",
        "toko bakmi dan kopitiam pelita delapan": "Pelita Delapan",
        "nirwana pamekasan": "Nirwana Coffee Space Pamekasan",
        "nirwana coffee space": "Nirwana Coffee Space Pamekasan",
        "nirwana sumenep": "Nirwana Coffee Space Sumenep",
    }
    for alias_key, expected_client in expected.items():
        row = await spine_db.fetchrow(
            "SELECT c.display_name FROM client_aliases ca JOIN clients c ON c.id = ca.client_id "
            "WHERE ca.alias_key = $1",
            alias_key,
        )
        assert row is not None, f"alias '{alias_key}' did not resolve"
        assert row["display_name"] == expected_client, f"alias '{alias_key}' resolved to the wrong client"

    # No alias appears twice.
    dup = await spine_db.fetchval(
        "SELECT alias_key FROM client_aliases GROUP BY alias_key HAVING count(*) > 1 LIMIT 1"
    )
    assert dup is None


# ---------------------------------------------------------------------------
# SC-009: a manual correction survives a subsequent roster-sync run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reassigned_alias_survives_subsequent_roster_sync(spine_db):
    """The regression T035's create-only seeding exists to prevent: without
    it, the next daily sync would silently revert a maintainer's correction
    back to the original (possibly wrong) mapping."""
    rows = _full_clients_sheet_rows()
    components = _full_components_block()
    kb_names = _full_knowledge_base_names()

    await roster_engine.reconcile_roster(spine_db, rows, components, {}, kb_names)

    wrong_client = await spine_db.fetchval(
        "SELECT client_id FROM client_aliases WHERE alias_key = 'nirwana coffee space'"
    )
    correct_client = await spine_db.fetchval(
        "SELECT id FROM clients WHERE display_name = 'Nirwana Coffee Space Sumenep'"
    )
    assert wrong_client != correct_client  # sanity: seeded to Pamekasan, not Sumenep

    # A maintainer decides the seed was wrong and corrects it.
    reassignment = await _alias_reassign_impl(spine_db, "nirwana coffee space", correct_client)
    assert reassignment["alias_key"] == "nirwana coffee space"

    corrected = await spine_db.fetchval(
        "SELECT client_id FROM client_aliases WHERE alias_key = 'nirwana coffee space'"
    )
    assert corrected == correct_client

    # The next daily roster-sync run must NOT revert the correction.
    await roster_engine.reconcile_roster(spine_db, rows, components, {}, kb_names)

    still_correct = await spine_db.fetchval(
        "SELECT client_id FROM client_aliases WHERE alias_key = 'nirwana coffee space'"
    )
    assert still_correct == correct_client


@pytest.mark.asyncio
async def test_alias_reassign_relinks_knowledge_records(spine_db):
    """SC-009's other half: correcting a mapping must not orphan knowledge
    already bound to the old (wrong) client through that alias."""
    old_client = await _client(spine_db, "old-client", "Old Client")
    new_client = await _client(spine_db, "new-client", "New Client")
    await _upsert_alias_impl(spine_db, old_client, "Ambiguous Name", "knowledge_base")
    await spine_db.execute(
        """
        INSERT INTO knowledge_records (client_name, client_key, subject, subject_key, information, source_type, client_id)
        VALUES ('Ambiguous Name', 'ambiguous name', 'profile', 'profile', 'some facts', 'plain_text', $1)
        """,
        old_client,
    )

    result = await _alias_reassign_impl(spine_db, "ambiguous name", new_client)
    assert result["knowledge_records_relinked"] == 1

    linked = await spine_db.fetchval("SELECT client_id FROM knowledge_records WHERE client_name = 'Ambiguous Name'")
    assert linked == new_client
