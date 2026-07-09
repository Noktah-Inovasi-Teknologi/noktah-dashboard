"""MCP tool contract tests: each tool's argument validation and return shape
against specs/001-client-knowledge-base/contracts/mcp-tools.md.

Covers tasks T015, T024, T028, T032.
"""
import pytest

from server import kb_find_client, kb_query_current, kb_query_history, kb_upsert_record


def _call(tool):
    return tool


# --- T015: kb_find_client / kb_upsert_record (create path) ---------------


async def test_kb_upsert_record_create_path_returns_created():
    result = await _call(kb_upsert_record)(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30, invoiced monthly.",
        source_type="plain_text",
    )
    assert result["ok"] is True
    assert result["result"] == "created"
    assert result["superseded_record_id"] is None
    assert result["record"]["client_name"] == "Acme Corp"


async def test_kb_upsert_record_validation_error_uses_error_contract():
    result = await _call(kb_upsert_record)(
        client_name="",
        subject="Billing",
        information="x",
        source_type="plain_text",
    )
    assert result["ok"] is False
    assert result["code"] == "validation"


async def test_kb_upsert_record_missing_source_reference_is_validation_error():
    result = await _call(kb_upsert_record)(
        client_name="Acme",
        subject="Billing",
        information="x",
        source_type="google_doc",
    )
    assert result["ok"] is False
    assert result["code"] == "validation"


async def test_kb_find_client_returns_suggestions_shape():
    await _call(kb_upsert_record)(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30.",
        source_type="plain_text",
    )
    result = await _call(kb_find_client)(name="acme")
    assert result["ok"] is True
    assert result["query"] == "acme"
    assert isinstance(result["suggestions"], list)
    assert result["suggestions"][0]["client_name"] == "Acme Corp"


# --- T024: kb_query_current -----------------------------------------------


async def test_kb_query_current_found_false_for_unknown_client():
    result = await _call(kb_query_current)(client_name="Nonexistent Ltd")
    assert result["ok"] is True
    assert result["found"] is False
    assert result["records"] == []


async def test_kb_query_current_returns_only_matched_records():
    await _call(kb_upsert_record)(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30.",
        source_type="plain_text",
    )
    result = await _call(kb_query_current)(client_name="Acme Corp")
    assert result["found"] is True
    assert len(result["records"]) == 1


# --- T028: kb_query_history ------------------------------------------------


async def test_kb_query_history_returns_status_labels():
    await _call(kb_upsert_record)(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30.",
        source_type="plain_text",
    )
    await _call(kb_upsert_record)(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-15.",
        source_type="plain_text",
    )
    result = await _call(kb_query_history)(client_name="Acme Corp")
    assert result["ok"] is True
    statuses = {r["status"] for r in result["records"]}
    assert statuses == {"current", "superseded"}


# --- T032: kb_upsert_record superseded / unchanged return contract --------


async def test_kb_upsert_record_returns_superseded_with_pointer():
    first = await _call(kb_upsert_record)(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30.",
        source_type="plain_text",
    )
    second = await _call(kb_upsert_record)(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-15.",
        source_type="plain_text",
    )
    assert second["result"] == "superseded"
    assert second["superseded_record_id"] == first["record"]["id"]


async def test_kb_upsert_record_returns_unchanged_for_duplicate():
    await _call(kb_upsert_record)(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-15.",
        source_type="plain_text",
    )
    result = await _call(kb_upsert_record)(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-15.",
        source_type="plain_text",
    )
    assert result["result"] == "unchanged"
    assert result["superseded_record_id"] is None
