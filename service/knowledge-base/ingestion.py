"""Google Docs/Sheets ingestion helpers.

Reuses the OAuth2 refresh-token pattern from
service/prefect/blocks/google_credentials.py (method 2: refresh token from
environment variables), scoped down to what this service needs: read-only
access to Docs and Sheets content for chatbot-driven extraction (R5).
"""
import logging
import os
import re
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from models import SourceFetchError

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

_DOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")
_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")

_credentials: Optional[Credentials] = None


def _get_credentials() -> Credentials:
    global _credentials

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise SourceFetchError(
            "Google OAuth credentials are not configured "
            "(GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET/GOOGLE_REFRESH_TOKEN)"
        )

    if _credentials is None or _credentials.expired:
        _credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            id_token=None,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        try:
            _credentials.refresh(Request())
        except Exception as exc:
            raise SourceFetchError(f"Failed to refresh Google credentials: {exc}") from exc

    return _credentials


def _extract_doc_id(url: str) -> Optional[str]:
    match = _DOC_ID_RE.search(url)
    return match.group(1) if match else None


def _extract_sheet_id(url: str) -> Optional[str]:
    match = _SHEET_ID_RE.search(url)
    return match.group(1) if match else None


def _extract_doc_text(document: dict) -> str:
    """Flatten a Google Docs API document body into plain text."""
    parts: list[str] = []
    for element in document.get("body", {}).get("content", []):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        for run in paragraph.get("elements", []):
            text_run = run.get("textRun")
            if text_run and text_run.get("content"):
                parts.append(text_run["content"])
    return "".join(parts).strip()


async def fetch_google_source(url: str) -> dict:
    """Fetch a Google Doc or Sheet's content by URL.

    Returns:
        {"ok": True, "source_type": "google_doc"|"google_sheet",
         "text": "..."} for Docs, or
        {"ok": True, "source_type": "google_sheet",
         "tabs": [{"title": ..., "rows": [[...]]}]} for Sheets, so the LLM
        can decide how to map tabs/rows to subjects (R6).
    """
    credentials = _get_credentials()

    doc_id = _extract_doc_id(url)
    if doc_id:
        try:
            service = build("docs", "v1", credentials=credentials)
            document = service.documents().get(documentId=doc_id).execute()
            text = _extract_doc_text(document)
            return {"ok": True, "source_type": "google_doc", "text": text}
        except HttpError as exc:
            raise SourceFetchError(f"Failed to fetch Google Doc: {exc}") from exc

    sheet_id = _extract_sheet_id(url)
    if sheet_id:
        try:
            service = build("sheets", "v4", credentials=credentials)
            metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
            tabs = []
            for sheet in metadata.get("sheets", []):
                title = sheet.get("properties", {}).get("title", "")
                values_result = (
                    service.spreadsheets()
                    .values()
                    .get(spreadsheetId=sheet_id, range=title)
                    .execute()
                )
                tabs.append({"title": title, "rows": values_result.get("values", [])})
            return {"ok": True, "source_type": "google_sheet", "tabs": tabs}
        except HttpError as exc:
            raise SourceFetchError(f"Failed to fetch Google Sheet: {exc}") from exc

    raise SourceFetchError(
        f"URL does not look like a Google Docs or Sheets link: {url}"
    )
