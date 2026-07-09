"""Repository tests: normalization, create/dedupe/supersession atomicity,
current-record retrieval, and historical retrieval.

Covers tasks T014, T023, T027, T031.
"""
import asyncio
from datetime import date, timedelta

import pytest

import repository
from models import ValidationError

# --- T014: normalization + create ---------------------------------------


def test_normalize_key_collapses_whitespace_and_case():
    assert repository.normalize_key("  Acme   Corp  ") == "acme corp"
    assert repository.normalize_key("ACME") == repository.normalize_key("acme")


async def test_upsert_creates_new_record():
    result = await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30, invoiced monthly.",
        source_type="plain_text",
    )
    assert result.result == "created"
    assert result.superseded_record_id is None
    assert result.record.client_name == "Acme Corp"


async def test_upsert_rejects_empty_fields():
    with pytest.raises(ValidationError):
        await repository.upsert_record(
            client_name="",
            subject="Billing",
            information="x",
            source_type="plain_text",
        )


async def test_upsert_requires_source_reference_for_google_sources():
    with pytest.raises(ValidationError):
        await repository.upsert_record(
            client_name="Acme",
            subject="Billing",
            information="x",
            source_type="google_doc",
        )


# --- T031: dedupe + supersession atomicity + invariant -------------------


async def test_upsert_identical_information_is_unchanged_noop():
    await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30, invoiced monthly.",
        source_type="plain_text",
    )
    result = await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30, invoiced monthly.",
        source_type="plain_text",
    )
    assert result.result == "unchanged"
    assert result.superseded_record_id is None


async def test_upsert_different_information_supersedes_prior_record():
    first = await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30, invoiced monthly.",
        source_type="plain_text",
    )
    second = await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-15, invoiced weekly.",
        source_type="plain_text",
    )
    assert second.result == "superseded"
    assert second.superseded_record_id == first.record.id


async def test_upsert_whitespace_variant_subject_still_supersedes():
    await repository.upsert_record(
        client_name="Acme Corp",
        subject="billing terms",
        information="Net-30.",
        source_type="plain_text",
    )
    result = await repository.upsert_record(
        client_name="acme corp",
        subject="  Billing   Terms ",
        information="Net-15.",
        source_type="plain_text",
    )
    assert result.result == "superseded"


async def test_single_current_record_invariant_holds_under_concurrent_upserts():
    """Concurrent upserts for the same client+subject must not both become current."""

    async def do_upsert(info: str):
        return await repository.upsert_record(
            client_name="Acme Corp",
            subject="Billing Terms",
            information=info,
            source_type="plain_text",
        )

    results = await asyncio.gather(
        do_upsert("Version A"), do_upsert("Version B"), do_upsert("Version C")
    )
    assert {r.result for r in results} == {"created", "superseded", "superseded"} or {
        r.result for r in results
    } <= {"created", "superseded"}

    current = await repository.query_current(client_name="Acme Corp")
    assert len(current.records) == 1


# --- T023: query_current ranking + exclusion ------------------------------


async def test_query_current_excludes_superseded_records():
    await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30.",
        source_type="plain_text",
    )
    await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-15.",
        source_type="plain_text",
    )
    result = await repository.query_current(client_name="Acme Corp")
    assert result.found is True
    assert len(result.records) == 1
    assert result.records[0].information == "Net-15."


async def test_query_current_returns_not_found_for_unknown_client():
    result = await repository.query_current(client_name="Nonexistent Ltd")
    assert result.found is False
    assert result.records == []


async def test_query_current_ranks_by_subject_similarity():
    await repository.upsert_record(
        client_name="Acme Corp",
        subject="billing terms",
        information="Net-30.",
        source_type="plain_text",
    )
    await repository.upsert_record(
        client_name="Acme Corp",
        subject="primary contact",
        information="Jane Doe, jane@acme.com",
        source_type="plain_text",
    )
    result = await repository.query_current(
        client_name="Acme Corp", question="what are the billing terms"
    )
    assert result.records[0].subject == "billing terms"


async def test_query_current_subject_filter_is_fuzzy_not_exact():
    """Regression test: a caller guessing 'services' must still find a record
    saved under 'services offered' — an exact-match filter previously caused
    a false 'not found' when the guessed wording didn't match verbatim."""
    await repository.upsert_record(
        client_name="Klinik Mata Boyolali",
        subject="services offered",
        information="Delivers ophthalmology services.",
        source_type="plain_text",
    )
    result = await repository.query_current(
        client_name="Klinik Mata Boyolali", subject="services"
    )
    assert result.found is True
    assert result.records[0].subject == "services offered"


async def test_query_current_subject_filter_excludes_unrelated_subjects():
    await repository.upsert_record(
        client_name="Acme Corp",
        subject="billing terms",
        information="Net-30.",
        source_type="plain_text",
    )
    result = await repository.query_current(client_name="Acme Corp", subject="services offered")
    assert result.found is False


# --- T027: query_history status labels + time range ----------------------


async def test_query_history_labels_superseded_and_current():
    first = await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30.",
        source_type="plain_text",
    )
    second = await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-15.",
        source_type="plain_text",
    )
    history = await repository.query_history(client_name="Acme Corp")
    by_id = {r.id: r for r in history.records}

    assert by_id[first.record.id].status == "superseded"
    assert by_id[first.record.id].superseded_by == second.record.id
    assert by_id[second.record.id].status == "current"


async def test_query_history_filters_by_date_range():
    await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30.",
        source_type="plain_text",
    )
    far_future = date.today() + timedelta(days=3650)
    history = await repository.query_history(client_name="Acme Corp", since=far_future)
    assert history.records == []


async def test_query_history_subject_filter_is_fuzzy_not_exact():
    """Regression test for the same false-negative observed in query_current:
    a guessed subject ('services') must still surface history saved under a
    more specific stored subject ('services offered')."""
    first = await repository.upsert_record(
        client_name="Klinik Mata Boyolali",
        subject="services offered",
        information="Only delivers ophthalmology services.",
        source_type="plain_text",
    )
    await repository.upsert_record(
        client_name="Klinik Mata Boyolali",
        subject="services offered",
        information="Delivers ophthalmology services and now also child ophthalmology.",
        source_type="plain_text",
    )
    history = await repository.query_history(
        client_name="Klinik Mata Boyolali", subject="services"
    )
    ids = {r.id for r in history.records}
    assert first.record.id in ids


# --- FR-003b: find_client fuzzy matching ---------------------------------


async def test_find_client_suggests_close_match():
    await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30.",
        source_type="plain_text",
    )
    result = await repository.find_client("acme")
    assert any(s.client_name == "Acme Corp" for s in result.suggestions)


async def test_find_client_exact_match():
    await repository.upsert_record(
        client_name="Acme Corp",
        subject="Billing Terms",
        information="Net-30.",
        source_type="plain_text",
    )
    result = await repository.find_client("Acme Corp")
    assert result.exact_match == "Acme Corp"
