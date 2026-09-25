"""
Google access for hub-api: the same OAuth refresh-token pattern as the Prefect
service (GOOGLE_CLIENT_ID / _SECRET / _REFRESH_TOKEN). Blocking client calls;
callers run them with asyncio.to_thread.
"""
from typing import Any, Dict, List

from .settings import get_settings

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
DOCS_READONLY_SCOPE = "https://www.googleapis.com/auth/documents.readonly"


class GoogleNotConfigured(RuntimeError):
    pass


def credentials(scopes: List[str]):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    s = get_settings()
    if not (s.google_client_id and s.google_client_secret and s.google_refresh_token):
        raise GoogleNotConfigured("Akses Google belum diatur di server (GOOGLE_*).")
    creds = Credentials(token=None, refresh_token=s.google_refresh_token, token_uri="https://oauth2.googleapis.com/token",
                        client_id=s.google_client_id, client_secret=s.google_client_secret, scopes=scopes)
    creds.refresh(Request())
    return creds


def service(name: str, version: str, scopes: List[str]):
    from googleapiclient.discovery import build
    return build(name, version, credentials=credentials(scopes), cache_discovery=False)


def read_ranges(spreadsheet_id: str, ranges: List[str]) -> Dict[str, List[List[Any]]]:
    """One batchGet; {range: rows}. Rows are formatted strings, ragged (trailing blanks cut)."""
    sheets = service("sheets", "v4", [SHEETS_SCOPE])
    result = sheets.spreadsheets().values().batchGet(spreadsheetId=spreadsheet_id, ranges=ranges).execute()
    return {req: vr.get("values", []) for req, vr in zip(ranges, result.get("valueRanges", []))}


def write_cells(spreadsheet_id: str, cells: Dict[str, str]) -> int:
    """Write {A1: value} in one values.batchUpdate (RAW, so an ID is never turned into a number)."""
    if not cells:
        return 0
    sheets = service("sheets", "v4", [SHEETS_SCOPE])
    body = {"valueInputOption": "RAW", "data": [{"range": a1, "values": [[v]]} for a1, v in cells.items()]}
    result = sheets.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
    return int(result.get("totalUpdatedCells", 0))


def set_header_notes(spreadsheet_id: str, tabs: List[str], note: str) -> None:
    """Put `note` on cell A1 of each tab (the "maintained by the Hub" marker, FR-016)."""
    sheets = service("sheets", "v4", [SHEETS_SCOPE])
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id,
                                     fields="sheets(properties(title,sheetId))").execute()
    ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    requests = [{"updateCells": {"range": {"sheetId": ids[t], "startRowIndex": 0, "endRowIndex": 1,
                                           "startColumnIndex": 0, "endColumnIndex": 1},
                                 "rows": [{"values": [{"note": note}]}], "fields": "note"}}
                for t in tabs if t in ids]
    if requests:
        sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
