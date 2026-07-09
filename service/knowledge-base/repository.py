"""Data-access layer for the Client Knowledge Base.

Implements normalization, atomic upsert-with-supersession, fuzzy client
matching, current-record retrieval, and historical retrieval, per
specs/001-client-knowledge-base/data-model.md.
"""
import logging
import re
import uuid
from datetime import date
from typing import Optional

import asyncpg

from db import get_pool
from models import (
    ClientSuggestion,
    FindClientResult,
    HistoryRecord,
    KnowledgeRecord,
    QueryCurrentResult,
    QueryHistoryResult,
    UpsertRecordResult,
    ValidationError,
)

logger = logging.getLogger(__name__)

# Close-match threshold for pg_trgm similarity (addresses analysis finding A1)
CLIENT_SIMILARITY_THRESHOLD = 0.4
# Fuzzy subject-filter threshold for query_current/query_history. Empirically,
# similarity('services offered', 'services') = 0.53, vs 'service' = 0.39,
# vs 'services provided' = 0.4, vs an unrelated subject = 0.0 — 0.3 leaves a
# comfortable margin above unrelated subjects while catching genuine
# near-matches, so a caller guessing "services" still finds "services offered".
SUBJECT_MATCH_THRESHOLD = 0.3
MAX_INFORMATION_LENGTH = 4000


def normalize_key(value: str) -> str:
    """Normalize a name for matching: lowercase, trimmed, whitespace-collapsed."""
    return re.sub(r"\s+", " ", value.strip()).lower()


def _validate_record_fields(client_name: str, subject: str, information: str) -> None:
    if not client_name.strip():
        raise ValidationError("client_name must not be empty")
    if not subject.strip():
        raise ValidationError("subject must not be empty")
    if not information.strip():
        raise ValidationError("information must not be empty")
    if len(information) > MAX_INFORMATION_LENGTH:
        raise ValidationError(
            f"information exceeds max length of {MAX_INFORMATION_LENGTH} characters; "
            "split into multiple subjects"
        )


def _row_to_knowledge_record(row: asyncpg.Record) -> KnowledgeRecord:
    return KnowledgeRecord(
        id=str(row["id"]),
        client_name=row["client_name"],
        subject=row["subject"],
        information=row["information"],
        timestamp=row["timestamp"],
        source_type=row["source_type"],
        source_reference=row["source_reference"],
    )


async def find_client(name: str) -> FindClientResult:
    """Resolve a possibly-inconsistent client name to existing clients (FR-003b)."""
    if not name.strip():
        raise ValidationError("name must not be empty")

    key = normalize_key(name)
    pool = await get_pool()

    async with pool.acquire() as conn:
        exact_row = await conn.fetchrow(
            """
            SELECT client_name FROM knowledge_records
            WHERE client_key = $1
            ORDER BY "timestamp" DESC LIMIT 1
            """,
            key,
        )
        exact_match = exact_row["client_name"] if exact_row else None

        rows = await conn.fetch(
            """
            SELECT client_name, client_key,
                   similarity(client_key, $1) AS sim,
                   count(*) OVER (PARTITION BY client_key) AS record_count
            FROM knowledge_records
            WHERE client_key <> $1 AND similarity(client_key, $1) >= $2
            ORDER BY sim DESC
            LIMIT 5
            """,
            key,
            CLIENT_SIMILARITY_THRESHOLD,
        )

    seen: set[str] = set()
    suggestions: list[ClientSuggestion] = []
    for row in rows:
        if row["client_key"] in seen:
            continue
        seen.add(row["client_key"])
        suggestions.append(
            ClientSuggestion(
                client_name=row["client_name"],
                client_key=row["client_key"],
                similarity=float(row["sim"]),
                record_count=row["record_count"],
            )
        )

    return FindClientResult(query=name, exact_match=exact_match, suggestions=suggestions)


async def upsert_record(
    client_name: str,
    subject: str,
    information: str,
    source_type: str,
    source_reference: Optional[str] = None,
    created_by: Optional[str] = None,
) -> UpsertRecordResult:
    """Create a record, applying dedupe + supersession atomically (FR-003, FR-010, R6).

    Behavior:
    - No current record for (client_key, subject_key): insert as CURRENT -> "created".
    - Current record exists with identical `information`: no-op -> "unchanged".
    - Current record exists with different `information`: insert new CURRENT row and
      set the prior row's superseded_by to the new row's id, atomically -> "superseded".
    """
    _validate_record_fields(client_name, subject, information)

    if source_type in ("google_doc", "google_sheet") and not source_reference:
        raise ValidationError(
            f"source_reference is required when source_type is '{source_type}'"
        )

    client_key = normalize_key(client_name)
    subject_key = normalize_key(subject)
    pool = await get_pool()

    # Retry on a unique-violation race: `SELECT ... FOR UPDATE` only locks an
    # *existing* current row, so when no current row exists yet, two concurrent
    # first-inserts can both see `current is None` and both attempt to insert a
    # new current row. The partial unique index correctly rejects the loser;
    # retrying re-reads state and converts that attempt into the appropriate
    # "unchanged" or "superseded" outcome instead of surfacing a raw DB error.
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    current = await conn.fetchrow(
                        """
                        SELECT id, information FROM knowledge_records
                        WHERE client_key = $1 AND subject_key = $2 AND superseded_by IS NULL
                        FOR UPDATE
                        """,
                        client_key,
                        subject_key,
                    )

                    if current is not None and current["information"] == information:
                        row = await conn.fetchrow(
                            "SELECT * FROM knowledge_records WHERE id = $1", current["id"]
                        )
                        return UpsertRecordResult(
                            result="unchanged",
                            record=_row_to_knowledge_record(row),
                            superseded_record_id=None,
                        )

                    # Pre-generate the new row's id so the prior current row can be
                    # marked superseded *before* the new row is inserted. This
                    # ordering matters: the partial unique index (client_key,
                    # subject_key) WHERE superseded_by IS NULL is checked
                    # immediately (non-deferred), so if we inserted the new
                    # current row first, both rows would briefly satisfy the
                    # index and raise a UniqueViolationError. Updating the old
                    # row first removes it from that partial index immediately;
                    # the FK on superseded_by is DEFERRABLE INITIALLY DEFERRED so
                    # referencing the not-yet-inserted new id is legal until
                    # COMMIT.
                    new_id = uuid.uuid4()

                    superseded_id: Optional[str] = None
                    if current is not None:
                        await conn.execute(
                            "UPDATE knowledge_records SET superseded_by = $1 WHERE id = $2",
                            new_id,
                            current["id"],
                        )
                        superseded_id = str(current["id"])

                    new_row = await conn.fetchrow(
                        """
                        INSERT INTO knowledge_records
                            (id, client_name, client_key, subject, subject_key, information,
                             source_type, source_reference, created_by)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        RETURNING *
                        """,
                        new_id,
                        client_name,
                        client_key,
                        subject,
                        subject_key,
                        information,
                        source_type,
                        source_reference,
                        created_by,
                    )

                    result = "superseded" if superseded_id else "created"
                    return UpsertRecordResult(
                        result=result,
                        record=_row_to_knowledge_record(new_row),
                        superseded_record_id=superseded_id,
                    )
        except asyncpg.exceptions.UniqueViolationError:
            if attempt == max_attempts - 1:
                raise
            logger.warning(
                "Concurrent upsert race for %s/%s, retrying (attempt %d)",
                client_key,
                subject_key,
                attempt + 1,
            )
            continue

    raise RuntimeError("unreachable")  # pragma: no cover


async def query_current(
    client_name: str,
    question: Optional[str] = None,
    subject: Optional[str] = None,
    limit: int = 5,
) -> QueryCurrentResult:
    """Retrieve current (non-superseded) knowledge for a client (FR-005/006/007/011)."""
    if not client_name.strip():
        raise ValidationError("client_name must not be empty")

    client_key = normalize_key(client_name)
    pool = await get_pool()

    conditions = ["client_key = $1", "superseded_by IS NULL"]
    params: list = [client_key]

    if subject:
        # Fuzzy match, not exact: a caller (LLM-guessed or user-typed) rarely
        # phrases a subject identically to how it was originally saved
        # (e.g. "services" vs. the stored "services offered"). Falling back to
        # an exact subject_key match previously caused false "not found"
        # results whenever the wording didn't match verbatim.
        params.append(normalize_key(subject))
        subject_idx = len(params)
        params.append(SUBJECT_MATCH_THRESHOLD)
        threshold_idx = len(params)
        conditions.append(f"similarity(subject_key, ${subject_idx}) >= ${threshold_idx}")

    rank_term = question or subject
    order_clause = '"timestamp" DESC'
    if rank_term:
        params.append(rank_term)
        rank_idx = len(params)
        order_clause = f"similarity(subject || ' ' || information, ${rank_idx}) DESC"

    params.append(limit)
    limit_idx = len(params)

    query = f"""
        SELECT * FROM knowledge_records
        WHERE {' AND '.join(conditions)}
        ORDER BY {order_clause}
        LIMIT ${limit_idx}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    records = [_row_to_knowledge_record(row) for row in rows]
    return QueryCurrentResult(client_name=client_name, found=len(records) > 0, records=records)


async def query_history(
    client_name: str,
    subject: Optional[str] = None,
    since: Optional[date] = None,
    until: Optional[date] = None,
    limit: int = 10,
) -> QueryHistoryResult:
    """Retrieve historical/superseded knowledge with time + supersession context (FR-008/009)."""
    if not client_name.strip():
        raise ValidationError("client_name must not be empty")

    client_key = normalize_key(client_name)
    pool = await get_pool()

    conditions = ["r.client_key = $1"]
    params: list = [client_key]

    if subject:
        # Fuzzy match, not exact — see query_current for rationale.
        params.append(normalize_key(subject))
        subject_idx = len(params)
        params.append(SUBJECT_MATCH_THRESHOLD)
        threshold_idx = len(params)
        conditions.append(f"similarity(r.subject_key, ${subject_idx}) >= ${threshold_idx}")
    if since:
        params.append(since)
        conditions.append(f'r."timestamp" >= ${len(params)}')
    if until:
        params.append(until)
        conditions.append(f'r."timestamp" <= ${len(params)}')

    params.append(limit)
    limit_idx = len(params)

    query = f"""
        SELECT r.id, r.subject, r.information, r."timestamp",
               r.superseded_by, s."timestamp" AS superseded_by_timestamp
        FROM knowledge_records r
        LEFT JOIN knowledge_records s ON s.id = r.superseded_by
        WHERE {' AND '.join(conditions)}
        ORDER BY r."timestamp" DESC
        LIMIT ${limit_idx}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    records = [
        HistoryRecord(
            id=str(row["id"]),
            subject=row["subject"],
            information=row["information"],
            timestamp=row["timestamp"],
            status="superseded" if row["superseded_by"] else "current",
            superseded_by=str(row["superseded_by"]) if row["superseded_by"] else None,
            superseded_by_timestamp=row["superseded_by_timestamp"],
        )
        for row in rows
    ]
    return QueryHistoryResult(client_name=client_name, records=records)
