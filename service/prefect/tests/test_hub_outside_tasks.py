"""
The outside-system tasks spec 009 adds (Jira comment/property/fields/search/changelog,
Sheets write-cells, Drive upload-doc), the `hub.internal.call` JSON body, and the
converter's explicit Registry ids. Every HTTP client is replaced; no network.
"""
import logging
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tasks import google_tasks, hub_tasks, jira_tasks, utility_tasks


# --------------------------------------------------------------------------------------------
# Jira
# --------------------------------------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = b"x" if payload is not None else b""
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture
def jira(monkeypatch):
    calls = []
    replies = []

    class Creds:
        def get_client(self):
            return SimpleNamespace(jira_url="https://noktah.atlassian.net/", jira_username="bot@x",
                                   jira_token="t")

    async def fake_load(name):
        return Creds()

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        calls.append({"method": method, "url": url, "params": params, "json": json, "headers": headers})
        return replies.pop(0) if replies else _Resp(200, {})

    import requests
    monkeypatch.setattr(jira_tasks.JiraCredentials, "load_or_env", staticmethod(fake_load))
    monkeypatch.setattr(requests, "request", fake_request)
    return SimpleNamespace(calls=calls, replies=replies)


@pytest.mark.asyncio
async def test_comment_posts_an_adf_body(jira):
    jira.replies.append(_Resp(201, {"id": "777"}))
    out = await jira_tasks.jira_issue_comment.fn("ESKL-1", "Baris 2 berubah:\nTopik: A -> B\n\nCek lagi.")
    assert out == {"id": "777"}
    call = jira.calls[0]
    assert (call["method"], call["url"]) == ("POST", "https://noktah.atlassian.net/rest/api/3/issue/ESKL-1/comment")
    assert call["headers"]["Authorization"].startswith("Basic ")
    body = call["json"]["body"]
    assert body["type"] == "doc" and len(body["content"]) == 2
    assert body["content"][0]["content"][1] == {"type": "hardBreak"}


@pytest.mark.asyncio
async def test_get_property_returns_its_value(jira):
    jira.replies.append(_Resp(200, {"key": "noktah.plan-row", "value": {"row_number": 5}}))
    assert await jira_tasks.jira_issue_get_property.fn("ESKL-2", "noktah.plan-row") == {"row_number": 5}
    assert jira.calls[0]["url"].endswith("/rest/api/3/issue/ESKL-2/properties/noktah.plan-row")


@pytest.mark.asyncio
async def test_a_jira_error_raises_with_its_text(jira):
    jira.replies.append(_Resp(404, {"errorMessages": ["The property was not found"]}))
    with pytest.raises(RuntimeError, match="HTTP 404.*not found"):
        await jira_tasks.jira_issue_get_property.fn("ESKL-2", "noktah.plan-row")


@pytest.mark.asyncio
async def test_fields_map_is_id_to_name(jira):
    jira.replies.append(_Resp(200, [{"id": "summary", "name": "Summary"},
                                    {"id": "customfield_10042", "name": "Field Associate"}]))
    assert await jira_tasks.jira_fields_map.fn() == {"summary": "Summary", "customfield_10042": "Field Associate"}


@pytest.mark.asyncio
async def test_search_page_body(jira):
    jira.replies.append(_Resp(200, {"issues": [], "isLast": True}))
    await jira_tasks.jira_search_page.fn("project = ESKL", next_page_token="tok")
    call = jira.calls[0]
    assert (call["method"], call["url"]) == ("POST", "https://noktah.atlassian.net/rest/api/3/search/jql")
    assert call["json"] == {"jql": "project = ESKL", "fields": ["*all"], "expand": "changelog",
                            "maxResults": 50, "nextPageToken": "tok"}


@pytest.mark.asyncio
async def test_changelog_reads_every_page(jira):
    jira.replies.extend([_Resp(200, {"values": [{"id": "1"}, {"id": "2"}], "total": 3, "isLast": False}),
                         _Resp(200, {"values": [{"id": "3"}], "total": 3, "isLast": True})])
    out = await jira_tasks.jira_issue_changelog.fn("ESKL-3", page_size=2)
    assert [h["id"] for h in out] == ["1", "2", "3"]
    assert [c["params"]["startAt"] for c in jira.calls] == [0, 2]


# --------------------------------------------------------------------------------------------
# Google
# --------------------------------------------------------------------------------------------

@pytest.fixture
def google(monkeypatch):
    seen = {}

    class Exec:
        def __init__(self, result):
            self.result = result

        def execute(self):
            return self.result

    class Values:
        def batchUpdate(self, spreadsheetId, body):
            seen["batch"] = (spreadsheetId, body)
            return Exec({"totalUpdatedCells": len(body["data"])})

    class Sheets:
        def spreadsheets(self):
            return SimpleNamespace(values=lambda: Values())

    class Files:
        def create(self, body, media_body, fields, supportsAllDrives):
            seen["create"] = (body, media_body, fields, supportsAllDrives)
            return Exec({"id": "doc-1"})

    client = SimpleNamespace(sheets_service=Sheets(), get_drive_service=lambda: SimpleNamespace(files=lambda: Files()))

    class Creds:
        def get_client(self):
            return client

    async def fake_load(name):
        return Creds()

    monkeypatch.setattr(google_tasks.GoogleCredentials, "load_or_env", staticmethod(fake_load))
    return seen


@pytest.mark.asyncio
async def test_write_cells_is_one_user_entered_batch(google):
    cells = {"'Sheet1'!E2": '=HYPERLINK("u","ESKL-1")', "'Sheet1'!E3": '=HYPERLINK("v","ESKL-2")'}
    out = await google_tasks.sheets_write_cells.fn("sheet-1", cells)
    assert out == {"totalUpdatedCells": 2}
    spreadsheet_id, body = google["batch"]
    assert spreadsheet_id == "sheet-1" and body["valueInputOption"] == "USER_ENTERED"
    assert body["data"] == [{"range": "'Sheet1'!E2", "values": [['=HYPERLINK("u","ESKL-1")']]},
                            {"range": "'Sheet1'!E3", "values": [['=HYPERLINK("v","ESKL-2")']]}]


@pytest.mark.asyncio
async def test_write_cells_with_nothing_calls_nothing(google):
    assert await google_tasks.sheets_write_cells.fn("sheet-1", {}) == {"totalUpdatedCells": 0}
    assert "batch" not in google


@pytest.mark.asyncio
async def test_upload_doc_converts_html_to_a_google_doc(google):
    file_id = await google_tasks.drive_upload_doc.fn("SP 001", "<p>Surat</p>", "folder-sp")
    assert file_id == "doc-1"
    body, media, fields, all_drives = google["create"]
    assert body == {"name": "SP 001", "mimeType": "application/vnd.google-apps.document", "parents": ["folder-sp"]}
    assert media.mimetype() == "text/html" and all_drives is True and fields == "id"


# --------------------------------------------------------------------------------------------
# hub.internal.call
# --------------------------------------------------------------------------------------------

def test_hub_internal_call_sends_a_json_body(monkeypatch):
    seen = {}

    def fake_post(url, params=None, json=None, headers=None, timeout=None):
        seen.update(url=url, params=params, json=json, headers=headers)
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True}, text="")

    monkeypatch.setenv("HUB_API_URL", "http://api:8000")
    monkeypatch.setenv("HUB_API_INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(hub_tasks.httpx, "post", fake_post)
    assert hub_tasks.hub_internal_call.fn("jira-batches/claim", json={"batch_id": "b"}) == {"ok": True}
    assert seen["url"] == "http://api:8000/internal/jira-batches/claim"
    assert seen["json"] == {"batch_id": "b"} and seen["params"] == {}
    # the spec 008 callers (query params, no body) are unchanged
    hub_tasks.hub_internal_call.fn("sheet-sync", params={"check": "true"})
    assert seen["params"] == {"check": "true"} and seen["json"] is None


def test_hub_internal_call_raises_with_the_api_text(monkeypatch):
    monkeypatch.setenv("HUB_API_INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(hub_tasks.httpx, "post", lambda *a, **k: SimpleNamespace(
        status_code=422, text='{"detail":"Pilih minimal satu"}', json=lambda: {}))
    with pytest.raises(RuntimeError, match="HTTP 422.*Pilih"):
        hub_tasks.hub_internal_call.fn("jira-batches/claim", json={})


# --------------------------------------------------------------------------------------------
# Converter: explicit Registry ids win; the hashmap stays the old CLI's fallback
# --------------------------------------------------------------------------------------------

ROW = {"Topik": "Promo", "Tanggal": "2026-10-04", "Bentuk": "Post"}


@pytest.fixture
def conv(monkeypatch):
    monkeypatch.setattr(utility_tasks, "get_run_logger", lambda: logging.getLogger("test"))
    monkeypatch.setattr(utility_tasks, "COMPONENTS", {"Klinik": "comp-sheet"})
    monkeypatch.setattr(utility_tasks, "FIELD_ASSOCIATE", {"Klinik": "Rina"})
    monkeypatch.setattr(utility_tasks, "CONTENT_EDITOR", {"Klinik": "Budi"})
    monkeypatch.setattr(utility_tasks, "WORKERS", {"Rina": "acc-rina", "Budi": "acc-budi", "Noktah": "acc-nok"})
    return utility_tasks.convert_content_plan_row_to_jira_issue.fn


def test_old_cli_still_reads_the_hashmaps(conv):
    f = conv(row=ROW, client_name="Klinik")["fields"]
    assert f["components"] == [{"id": "comp-sheet"}]
    assert f["customfield_10042"] == {"accountId": "acc-rina"}
    assert f["customfield_10043"] == {"accountId": "acc-budi"}
    assert f["reporter"] == {"accountId": "acc-nok"}


def test_explicit_ids_win(conv):
    issue = conv(row=ROW, client_name="Klinik", component_id="c-reg", field_associate_account="a-fa",
                 content_editor_account="a-ce", reporter_account="a-rep")
    f = issue["fields"]
    assert f["components"] == [{"id": "c-reg"}]
    assert f["customfield_10042"] == {"accountId": "a-fa"} and f["assignee"] == {"accountId": "a-fa"}
    assert f["customfield_10043"] == {"accountId": "a-ce"}
    assert f["reporter"] == {"accountId": "a-rep"}


def test_registry_only_never_falls_back(conv):
    f = conv(row=ROW, client_name="Klinik", registry_only=True)["fields"]
    assert f["components"] == [] and f["customfield_10042"] is None and f["reporter"] is None


def test_hub_issue_payload_drops_metadata_and_adds_the_property(conv):
    issue = conv(row=ROW, client_name="Klinik")
    assert "metadata" in issue
    payload = utility_tasks.hub_issue_payload(issue, "p1", 7, "b1")
    assert "metadata" not in payload and payload["fields"] == issue["fields"]
    assert payload["properties"] == [{"key": "noktah.plan-row",
                                      "value": {"plan_id": "p1", "row_number": 7, "batch_id": "b1"}}]
