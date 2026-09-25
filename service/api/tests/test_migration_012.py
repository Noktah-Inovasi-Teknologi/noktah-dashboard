"""Migration 012 on data shaped like the live Hub before it: old role keys, an Owner
with no Unit, Sales & Marketing in a brand, and team members holding no role.

Builds its own database up to 011, seeds that shape, then applies 012 twice
(idempotency), its down script, and 012 again.
"""
import asyncpg
import pytest

from tests.conftest import CHAIN, MIGRATIONS, TEST_DSN, _admin_dsn

pytestmark = pytest.mark.schema
NAME = "hub_test_012"
DSN = TEST_DSN.rsplit("/", 1)[0] + "/" + NAME


async def _fresh() -> asyncpg.Connection:
    admin = await asyncpg.connect(_admin_dsn())
    await admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()", NAME)
    await admin.execute(f'DROP DATABASE IF EXISTS "{NAME}"')
    await admin.execute(f'CREATE DATABASE "{NAME}"')
    await admin.close()
    conn = await asyncpg.connect(DSN)
    await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    for m in CHAIN[:CHAIN.index("012_hub_units_roles_permissions")]:
        if m != "006_enforce_account_link":
            await conn.execute((MIGRATIONS / f"{m}.sql").read_text(encoding="utf-8"))
    return conn


async def _apply(conn, name):
    await conn.execute((MIGRATIONS / f"{name}.sql").read_text(encoding="utf-8"))


async def _seed_old_shape(conn):
    eskala = await conn.fetchval("SELECT id FROM noktah_brands WHERE brand_key = 'eskala'")
    venyu = await conn.fetchval("SELECT id FROM noktah_brands WHERE brand_key = 'venyu'")
    ids = {}
    for name in ("PM", "QC", "Sales", "Editor", "AE Venyu"):
        ids[name] = await conn.fetchval("INSERT INTO people (display_name) VALUES ($1) RETURNING id", name)
    await conn.execute("INSERT INTO person_roles (person_id, role, noktah_brand_id) VALUES "
                       "($1, 'project_manager', $4), ($2, 'qc', $4), ($3, 'sales_marketing', $4), "
                       "($3, 'sales_marketing', $5), ($6, 'account_executive', $5)",
                       ids["PM"], ids["QC"], ids["Sales"], eskala, venyu, ids["AE Venyu"])
    client = await conn.fetchval(
        "INSERT INTO clients (client_key, display_name, noktah_brand_id, status) "
        "VALUES ('klinik', 'Klinik', $1, 'active') RETURNING id", eskala)
    await conn.execute("INSERT INTO client_team_assignments (client_id, team_role, person_id) VALUES "
                       "($1, 'content_editor', $2), ($1, 'qc', $3)", client, ids["Editor"], ids["QC"])
    return ids


async def _active_roles(conn):
    return {(r["display_name"], r["role"], r["brand_key"]) for r in await conn.fetch(
        """SELECT p.display_name, r.role, b.brand_key FROM person_roles r JOIN people p ON p.id = r.person_id
           LEFT JOIN noktah_brands b ON b.id = r.noktah_brand_id WHERE r.valid_to IS NULL""")}


async def test_012_moves_old_data_into_units_roles_and_permissions():
    conn = await _fresh()
    try:
        await _seed_old_shape(conn)
        await _apply(conn, "012_hub_units_roles_permissions")
        await _apply(conn, "012_hub_units_roles_permissions")  # idempotent

        assert await _active_roles(conn) == {
            ("Noktah (core)", "owner", "noktah"), ("Bagas", "brand_manager", "venyu"),
            ("Defila Priana Falarima", "brand_manager", "eskala"),
            ("PM", "production_manager", "eskala"), ("QC", "quality_assurance", "eskala"),
            ("Sales", "sales_marketing", "noktah"),  # one role in the group, the duplicate ended
            ("Editor", "content_editor", "eskala"),  # granted because they sit on a team
        }, "AE in Venyu is not in Venyu's catalog, so it ended"
        assert await conn.fetchval(
            "SELECT team_role FROM client_team_assignments t JOIN people p ON p.id = t.person_id "
            "WHERE p.display_name = 'QC'") == "quality_assurance"

        perms = {(r["display_name"], r["permission"]) for r in await conn.fetch(
            "SELECT p.display_name, x.permission FROM person_permissions x JOIN people p ON p.id = x.person_id")}
        assert ("Defila Priana Falarima", "approve_guideline") in perms
        assert ("PM", "run_intake") in perms and ("PM", "approve_guideline") not in perms
        assert ("Sales", "hub_access") in perms and ("Sales", "edit_clients") not in perms
        assert not any(name in ("QC", "Editor") for name, _ in perms), "staff roles start with no permissions"

        units = {(r["display_name"], r["brand_key"]) for r in await conn.fetch(
            "SELECT p.display_name, b.brand_key FROM person_units u JOIN people p ON p.id = u.person_id "
            "JOIN noktah_brands b ON b.id = u.noktah_brand_id")}
        assert ("Noktah (core)", "noktah") in units and ("Editor", "eskala") in units
        assert await conn.fetchval(
            "SELECT count(*) FROM registry_changes WHERE entity = 'person_role' AND person_id IS NULL") == 2, \
            "the backfilled grant and the ended off-catalog role are both logged"

        await _apply(conn, "012_hub_units_roles_permissions.down")
        assert ("Noktah (core)", "owner", None) in await _active_roles(conn)
        assert ("PM", "project_manager", "eskala") in await _active_roles(conn)
        await _apply(conn, "012_hub_units_roles_permissions")
        assert ("Noktah (core)", "owner", "noktah") in await _active_roles(conn)
    finally:
        await conn.close()
