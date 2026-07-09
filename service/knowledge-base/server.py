"""FastMCP server exposing the Client Knowledge Base tools to AnythingLLM.

Tool contracts: specs/001-client-knowledge-base/contracts/mcp-tools.md
"""
import logging
from datetime import date
from typing import Optional

from mcp.server.fastmcp import FastMCP

import ingestion
import repository
from models import ErrorResult, SourceFetchError, ValidationError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("knowledge_base")

mcp = FastMCP("knowledge-base")


def _error(error: Exception) -> dict:
    """Map an exception to the standard {ok, error, code} error contract."""
    if isinstance(error, ValidationError):
        logger.warning("Validation error: %s", error)
        return ErrorResult(error=str(error), code="validation").model_dump()
    if isinstance(error, SourceFetchError):
        logger.warning("Source fetch error: %s", error)
        return ErrorResult(error=str(error), code="source_fetch").model_dump()
    logger.error("Internal error: %s", error, exc_info=True)
    return ErrorResult(error="An internal error occurred", code="internal").model_dump()


@mcp.tool()
async def kb_find_client(name: str) -> dict:
    """Resolve a possibly-inconsistent client name to existing normalized clients.

    Use this before saving new information to check whether the client already
    exists under a slightly different spelling. If `suggestions` is non-empty,
    ask the user to confirm whether one of them is the same client before
    calling kb_upsert_record.
    """
    try:
        result = await repository.find_client(name)
        logger.info("kb_find_client query=%r suggestions=%d", name, len(result.suggestions))
        return result.model_dump()
    except Exception as exc:  # noqa: BLE001 - tool boundary, never raise to the LLM
        return _error(exc)


@mcp.tool()
async def kb_upsert_record(
    client_name: str,
    subject: str,
    information: str,
    source_type: str,
    source_reference: Optional[str] = None,
    created_by: Optional[str] = None,
) -> dict:
    """Create or update a knowledge record for a client.

    `subject` should be a moderate-granularity topic (e.g. "billing terms"),
    not a per-sentence label and not a single catch-all per client. If a
    current record already exists for this client+subject, it will
    automatically be marked superseded and this call becomes the new current
    record. Submitting information identical to the current record is a
    no-op ("unchanged"). `source_reference` is required when source_type is
    google_doc or google_sheet.
    """
    try:
        result = await repository.upsert_record(
            client_name=client_name,
            subject=subject,
            information=information,
            source_type=source_type,
            source_reference=source_reference,
            created_by=created_by,
        )
        logger.info(
            "kb_upsert_record client=%r subject=%r result=%s",
            client_name,
            subject,
            result.result,
        )
        return result.model_dump()
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool()
async def kb_query_current(
    client_name: str,
    question: Optional[str] = None,
    subject: Optional[str] = None,
    limit: int = 5,
) -> dict:
    """Retrieve current (non-superseded) knowledge for a client.

    Only current records are returned — superseded records are never
    surfaced here. If `found` is false, state clearly that no information is
    available rather than guessing.
    """
    try:
        result = await repository.query_current(
            client_name=client_name, question=question, subject=subject, limit=limit
        )
        logger.info(
            "kb_query_current client=%r found=%s records=%d",
            client_name,
            result.found,
            len(result.records),
        )
        return result.model_dump()
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool()
async def kb_query_history(
    client_name: str,
    subject: Optional[str] = None,
    since: Optional[date] = None,
    until: Optional[date] = None,
    limit: int = 10,
) -> dict:
    """Retrieve historical (including superseded) knowledge for a client.

    Each record is labelled with `status` (current/superseded) and, when
    superseded, a pointer to the record that replaced it plus that record's
    timestamp — always state the time period of what you found.
    """
    try:
        result = await repository.query_history(
            client_name=client_name, subject=subject, since=since, until=until, limit=limit
        )
        logger.info(
            "kb_query_history client=%r records=%d", client_name, len(result.records)
        )
        return result.model_dump()
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool()
async def kb_fetch_google_source(url: str) -> dict:
    """Fetch a Google Docs or Google Sheets document's text content for extraction.

    Returns raw text (and, for Sheets, per-tab content) for the LLM to parse
    into one or more kb_upsert_record calls.
    """
    try:
        result = await ingestion.fetch_google_source(url)
        logger.info("kb_fetch_google_source url=%r ok", url)
        return result
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
