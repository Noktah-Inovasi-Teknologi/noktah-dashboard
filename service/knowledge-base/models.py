"""Pydantic models for the Client Knowledge Base MCP tool contracts.

Shapes mirror specs/001-client-knowledge-base/contracts/mcp-tools.md exactly.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

SourceType = Literal["plain_text", "google_doc", "google_sheet"]
UpsertResult = Literal["created", "superseded", "unchanged"]
ErrorCode = Literal["validation", "not_found", "source_fetch", "internal"]


class KnowledgeRecord(BaseModel):
    id: str
    client_name: str
    subject: str
    information: str
    timestamp: datetime
    source_type: Optional[SourceType] = None
    source_reference: Optional[str] = None


class ClientSuggestion(BaseModel):
    client_name: str
    client_key: str
    similarity: float
    record_count: int


class FindClientResult(BaseModel):
    ok: bool = True
    query: str
    exact_match: Optional[str] = None
    suggestions: list[ClientSuggestion] = Field(default_factory=list)


class UpsertRecordArgs(BaseModel):
    client_name: str
    subject: str
    information: str
    source_type: SourceType
    source_reference: Optional[str] = None
    created_by: Optional[str] = None


class UpsertRecordResult(BaseModel):
    ok: bool = True
    result: UpsertResult
    record: KnowledgeRecord
    superseded_record_id: Optional[str] = None


class QueryCurrentResult(BaseModel):
    ok: bool = True
    client_name: str
    found: bool
    records: list[KnowledgeRecord] = Field(default_factory=list)


class HistoryRecord(BaseModel):
    id: str
    subject: str
    information: str
    timestamp: datetime
    status: Literal["current", "superseded"]
    superseded_by: Optional[str] = None
    superseded_by_timestamp: Optional[datetime] = None


class QueryHistoryResult(BaseModel):
    ok: bool = True
    client_name: str
    records: list[HistoryRecord] = Field(default_factory=list)


class ErrorResult(BaseModel):
    ok: bool = False
    error: str
    code: ErrorCode


class ValidationError(ValueError):
    """Raised for tool-argument validation failures; mapped to code='validation'."""


class SourceFetchError(RuntimeError):
    """Raised when a Google Doc/Sheet cannot be retrieved; mapped to code='source_fetch'."""
