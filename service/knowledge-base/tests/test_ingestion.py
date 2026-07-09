"""Google source fetch/normalization tests (mocked Google API). Covers T016."""
import os
from unittest.mock import MagicMock, patch

import pytest

import ingestion
from models import SourceFetchError


@pytest.fixture(autouse=True)
def _set_google_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "test-refresh-token")
    ingestion._credentials = None
    yield
    ingestion._credentials = None


def test_extract_doc_id_from_url():
    url = "https://docs.google.com/document/d/abc123XYZ/edit"
    assert ingestion._extract_doc_id(url) == "abc123XYZ"


def test_extract_sheet_id_from_url():
    url = "https://docs.google.com/spreadsheets/d/sheet789/edit#gid=0"
    assert ingestion._extract_sheet_id(url) == "sheet789"


def test_extract_doc_text_flattens_paragraphs():
    document = {
        "body": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": "Hello "}}]}},
                {"paragraph": {"elements": [{"textRun": {"content": "world.\n"}}]}},
            ]
        }
    }
    assert ingestion._extract_doc_text(document) == "Hello world."


async def test_fetch_google_source_unrecognized_url_raises_source_fetch_error():
    with patch("ingestion.Credentials.refresh"):
        with pytest.raises(SourceFetchError):
            await ingestion.fetch_google_source("https://example.com/not-a-google-link")


async def test_fetch_google_source_doc(monkeypatch):
    mock_service = MagicMock()
    mock_service.documents().get().execute.return_value = {
        "body": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": "Net-30 terms."}}]}},
            ]
        }
    }

    with patch("ingestion.Credentials.refresh"), patch(
        "ingestion.build", return_value=mock_service
    ):
        result = await ingestion.fetch_google_source(
            "https://docs.google.com/document/d/abc123/edit"
        )

    assert result["ok"] is True
    assert result["source_type"] == "google_doc"
    assert "Net-30" in result["text"]


async def test_fetch_google_source_sheet_returns_all_tabs(monkeypatch):
    mock_service = MagicMock()
    mock_service.spreadsheets().get().execute.return_value = {
        "sheets": [
            {"properties": {"title": "Clients"}},
            {"properties": {"title": "Notes"}},
        ]
    }
    mock_service.spreadsheets().values().get().execute.return_value = {
        "values": [["Client", "Subject"], ["Acme", "Billing"]]
    }

    with patch("ingestion.Credentials.refresh"), patch(
        "ingestion.build", return_value=mock_service
    ):
        result = await ingestion.fetch_google_source(
            "https://docs.google.com/spreadsheets/d/sheet789/edit"
        )

    assert result["ok"] is True
    assert result["source_type"] == "google_sheet"
    assert len(result["tabs"]) == 2
    assert {tab["title"] for tab in result["tabs"]} == {"Clients", "Notes"}


async def test_fetch_google_source_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN", raising=False)
    with pytest.raises(SourceFetchError):
        await ingestion.fetch_google_source(
            "https://docs.google.com/document/d/abc123/edit"
        )
