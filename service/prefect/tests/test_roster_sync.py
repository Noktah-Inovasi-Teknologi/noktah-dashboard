"""
Tests for the roster reconciliation engine (feature 004-relational-spine),
flows/common/roster.py, per specs/004-relational-spine/contracts/roster-sync-report.md
and research.md R1/R2.

Sheets are mocked at the data layer: `reconcile_roster` takes already-parsed rows
(the shape `roster_sync.py` would have read from Google Sheets / hashmap.py), so
these tests exercise the reconciliation logic — union of two roster sources, alias
seeding, conflict handling, idempotency, report shape — without any network I/O.
Runs against a real disposable Postgres because the conflict/uniqueness behaviour
is enforced by the schema (see test_spine_schema.py), not by application code.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

from flows.common import roster as roster_engine  # noqa: E402

pytestmark = pytest.mark.schema


def _clients_sheet_rows():
    """Mirrors research R1's measured Clients worksheet: 20 rows, no Eskala,
    'Nirwana Coffee Shop Sumenep' (not 'Space')."""
    return [
        {"Name": "Ecky Dental Center", "Instagram": "https://www.instagram.com/eckydentalcenter/", "TikTok": ""},
        {"Name": "Nirwana Coffee Space Pamekasan", "Instagram": "https://www.instagram.com/nirwanacoffee_pmks/", "TikTok": ""},
        {"Name": "Nirwana Coffee Shop Sumenep", "Instagram": "https://www.instagram.com/nirwanacoffee_smnp/", "TikTok": ""},
        {"Name": "Breko", "Instagram": "", "TikTok": ""},
    ]


def _components_block():
    """Mirrors research R1: 21 entries, includes Eskala (absent from Clients sheet),
    and 'Nirwana Coffee Space Sumenep' (not 'Shop')."""
    return {
        "Ecky Dental Center": "10000",
        "Nirwana Coffee Space Pamekasan": "10002",
        "Nirwana Coffee Space Sumenep": "10001",
        "Breko": "10306",
        "Eskala": "10206",
    }


def _client_social_block():
    return {
        "Ecky Dental Center": {
            "own": [], "competitors": ["sebayadental"],
            "competitor_profiles": [{"name": "Sebaya Dental", "url": "https://www.instagram.com/sebayadental/", "handle": "sebayadental"}],
        },
    }


def _knowledge_base_names():
    """The subset of research R1's alias-seed table relevant to these fixtures."""
    return ["Nirwana Pamekasan", "Nirwana Coffee Space", "Nirwana Sumenep"]


@pytest.mark.asyncio
async def test_union_of_two_roster_sources(spine_db):
    report = await roster_engine.reconcile_roster(
        spine_db, _clients_sheet_rows(), _components_block(), _client_social_block(), _knowledge_base_names(),
    )
    assert report["summary"]["roster_sources"]["clients_sheet"] == 4
    assert report["summary"]["roster_sources"]["components_block"] == 5
    assert report["summary"]["roster_sources"]["union"] == 5  # Eskala adds one


@pytest.mark.asyncio
async def test_eskala_surfaces_as_source_disagreement(spine_db):
    report = await roster_engine.reconcile_roster(
        spine_db, _clients_sheet_rows(), _components_block(), _client_social_block(), _knowledge_base_names(),
    )
    kinds = [d["kind"] for d in report["summary"]["source_disagreements"]]
    names = [d.get("name") for d in report["summary"]["source_disagreements"] if d["kind"] == "missing_from_clients_sheet"]
    assert "missing_from_clients_sheet" in kinds
    assert "Eskala" in names


@pytest.mark.asyncio
async def test_eskala_created_as_client_with_zero_accounts(spine_db):
    await roster_engine.reconcile_roster(
        spine_db, _clients_sheet_rows(), _components_block(), _client_social_block(), _knowledge_base_names(),
    )
    row = await spine_db.fetchrow("SELECT id FROM clients WHERE display_name = 'Eskala'")
    assert row is not None
    accounts = await spine_db.fetchval(
        "SELECT count(*) FROM client_account_roles WHERE client_id = $1", row["id"]
    )
    assert accounts == 0


@pytest.mark.asyncio
async def test_sumenep_name_mismatch_resolves_to_one_client(spine_db):
    """Both spellings ('Shop' from Clients sheet, 'Space' from COMPONENTS) must
    resolve to a single client, not two."""
    await roster_engine.reconcile_roster(
        spine_db, _clients_sheet_rows(), _components_block(), _client_social_block(), _knowledge_base_names(),
    )
    count = await spine_db.fetchval(
        "SELECT count(DISTINCT display_name) FROM clients WHERE display_name ILIKE 'nirwana%sumenep%'"
    )
    assert count == 1


@pytest.mark.asyncio
async def test_nirwana_coffee_space_alias_resolves_to_pamekasan_only(spine_db):
    """The exact ambiguity the token-subset rule could not separate (spec Context)."""
    await roster_engine.reconcile_roster(
        spine_db, _clients_sheet_rows(), _components_block(), _client_social_block(), _knowledge_base_names(),
    )
    row = await spine_db.fetchrow(
        """
        SELECT c.display_name FROM client_aliases ca JOIN clients c ON c.id = ca.client_id
        WHERE ca.alias_key = 'nirwana coffee space'
        """
    )
    assert row["display_name"] == "Nirwana Coffee Space Pamekasan"


@pytest.mark.asyncio
async def test_owned_and_competitor_roles_created(spine_db):
    await roster_engine.reconcile_roster(
        spine_db, _clients_sheet_rows(), _components_block(), _client_social_block(), _knowledge_base_names(),
    )
    owned = await spine_db.fetchval("SELECT count(*) FROM client_account_roles WHERE role = 'owned'")
    competitor = await spine_db.fetchval("SELECT count(*) FROM client_account_roles WHERE role = 'competitor'")
    assert owned == 3  # Ecky, Pamekasan, Sumenep each have one Instagram handle
    assert competitor == 1  # sebayadental


@pytest.mark.asyncio
async def test_rerun_is_idempotent(spine_db):
    """SC-008: re-running produces no duplicates; every counter falls to `unchanged`."""
    await roster_engine.reconcile_roster(
        spine_db, _clients_sheet_rows(), _components_block(), _client_social_block(), _knowledge_base_names(),
    )
    clients_before = await spine_db.fetchval("SELECT count(*) FROM clients")
    accounts_before = await spine_db.fetchval("SELECT count(*) FROM accounts")
    roles_before = await spine_db.fetchval("SELECT count(*) FROM client_account_roles")

    second = await roster_engine.reconcile_roster(
        spine_db, _clients_sheet_rows(), _components_block(), _client_social_block(), _knowledge_base_names(),
    )

    clients_after = await spine_db.fetchval("SELECT count(*) FROM clients")
    accounts_after = await spine_db.fetchval("SELECT count(*) FROM accounts")
    roles_after = await spine_db.fetchval("SELECT count(*) FROM client_account_roles")

    assert (clients_before, accounts_before, roles_before) == (clients_after, accounts_after, roles_after)
    assert second["summary"]["clients"]["created"] == 0
    assert second["summary"]["accounts"]["created"] == 0
    assert second["summary"]["roles"]["created"] == 0


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(spine_db):
    """T063: --validate-only must report without mutating."""
    report = await roster_engine.reconcile_roster(
        spine_db, _clients_sheet_rows(), _components_block(), _client_social_block(), _knowledge_base_names(),
        dry_run=True,
    )
    assert report["summary"]["clients"]["created"] > 0  # reports what WOULD happen
    clients = await spine_db.fetchval("SELECT count(*) FROM clients")
    assert clients == 0  # but nothing was written


@pytest.mark.asyncio
async def test_departure_deactivates_not_deletes(spine_db):
    """FR-024: a client absent from a later sync is marked inactive, never deleted."""
    await roster_engine.reconcile_roster(
        spine_db, _clients_sheet_rows(), _components_block(), _client_social_block(), _knowledge_base_names(),
    )
    reduced_components = {k: v for k, v in _components_block().items() if k != "Breko"}
    reduced_clients = [r for r in _clients_sheet_rows() if r["Name"] != "Breko"]

    report = await roster_engine.reconcile_roster(
        spine_db, reduced_clients, reduced_components, _client_social_block(), _knowledge_base_names(),
    )

    row = await spine_db.fetchrow("SELECT is_active FROM clients WHERE display_name = 'Breko'")
    assert row is not None  # still present
    assert row["is_active"] is False
    assert report["summary"]["clients"]["deactivated"] == 1
