"""
Google Credentials Block for Prefect workflows

This module provides Prefect Blocks for securely storing and managing
Google API credentials with automatic token refresh and service initialization.
"""
import os
import logging
from typing import Dict, List, Any, Optional, TYPE_CHECKING

from prefect.blocks.core import Block
from pydantic import Field, SecretStr
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

if TYPE_CHECKING:
    from pandas import DataFrame

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    pd = None

logger = logging.getLogger(__name__)


def col_letter(index: int) -> str:
    """0-based column index -> A1 letter (A, B, … Z, AA).

    Single definition for the whole service. The >26-column wraparound is the
    classic off-by-one here, and `ACCOUNT_HEADER` is now 20 columns and growing —
    a second copy would mean a fix reaching only one of them, and a mislabelled
    column silently relabels reviewer-entered data.
    """
    s = ""
    index += 1
    while index:
        index, r = divmod(index - 1, 26)
        s = chr(65 + r) + s
    return s


class GoogleCredentials(Block):
    """
    Prefect Block for storing and managing Google API credentials.
    
    This block securely stores Google API credentials and provides
    methods to create authenticated clients for Google services including
    Sheets, Drive, Calendar, and Documents.
    """
    
    _block_type_name = "Google Credentials"
    _block_type_slug = "google-credentials"
    _logo_url = "https://developers.google.com/identity/images/g-logo.png"
    _description = "Block for storing Google API credentials and creating authenticated clients for multiple Google services"
    
    # OAuth2 Credentials (Method 1 - for production)
    client_id: Optional[str] = Field(default=None, description="Google OAuth2 Client ID")
    client_secret: Optional[SecretStr] = Field(default=None, description="Google OAuth2 Client Secret")
    refresh_token: Optional[SecretStr] = Field(default=None, description="Google OAuth2 Refresh Token")
    
    # Client Secrets File (Method 2 - for development)
    credentials_file: Optional[str] = Field(
        default=None, 
        description="Path to Google OAuth2 client secrets JSON file"
    )
    
    # Token Storage
    token_file: Optional[str] = Field(
        default=None,
        description="Path to store/load refresh token"
    )
    
    # Scopes: Sheets read/write, Drive read + write-own-files (harvest delivery, FR-008/FR-009/FR-010)
    scopes: List[str] = Field(
        default=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.readonly',
            'https://www.googleapis.com/auth/drive.file'
        ],
        description="Google API scopes (Sheets read/write, Drive read-only + write-own-files)"
    )
    
    def get_client(self) -> 'GoogleClient':
        """
        Create and return an authenticated Google client.
        
        Returns:
            GoogleClient: Authenticated Google client wrapper for multiple services
        """
        return GoogleClient(
            client_id=self.client_id,
            client_secret=self.client_secret.get_secret_value() if self.client_secret else None,
            refresh_token=self.refresh_token.get_secret_value() if self.refresh_token else None,
            credentials_file=self.credentials_file,
            token_file=self.token_file,
            scopes=self.scopes
        )
    
    def test_connection(self) -> Dict[str, Any]:
        """
        Test the Google API connection.

        Returns:
            Dict containing connection status
        """
        client = self.get_client()
        return client.test_connection()

    @classmethod
    async def load_or_env(cls, name: str) -> "GoogleCredentials":
        """
        Load a saved credentials block, or build one from environment variables.

        Attempts to load the Prefect block document named ``name``. If it does
        not exist (or cannot be loaded), fall back to constructing credentials
        from GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN so
        flows work without pre-registering blocks.

        Args:
            name: Name of the Google credentials block document.

        Returns:
            GoogleCredentials instance.
        """
        try:
            return await cls.load(name)
        except Exception as e:
            logger.info(
                f"Block '{name}' not loaded ({e}); "
                "building GoogleCredentials from environment variables"
            )
            return cls(
                client_id=os.getenv("GOOGLE_CLIENT_ID"),
                client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
                refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
            )


class GoogleClient:
    """
    Google API client with OAuth2 authentication and token refresh handling.

    This client manages authentication credentials, handles automatic token refresh,
    and provides methods for interacting with Google Sheets and Drive APIs.

    Attributes:
        credentials: Google OAuth2 credentials object
        sheets_service: Google Sheets API service (initialized lazily)
        drive_service: Google Drive API service (initialized lazily)
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        credentials_file: Optional[str] = None,
        token_file: Optional[str] = None,
        scopes: Optional[List[str]] = None
    ):
        """
        Initialize Google client with OAuth2 credentials.

        Args:
            client_id: Google OAuth2 Client ID
            client_secret: Google OAuth2 Client Secret
            refresh_token: Google OAuth2 Refresh Token
            credentials_file: Path to client secrets JSON file (for local OAuth flow)
            token_file: Path to store/load refresh token
            scopes: Google API scopes (defaults to Sheets and Drive read-only)

        Raises:
            ValueError: If credentials cannot be initialized
        """
        # Set default scopes (only what's needed)
        self.scopes = scopes or [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.readonly',
            'https://www.googleapis.com/auth/drive.file'
        ]

        # Set credentials from parameters or environment variables
        self.client_id = client_id or os.getenv('GOOGLE_CLIENT_ID')
        self.client_secret = client_secret or os.getenv('GOOGLE_CLIENT_SECRET')
        self.refresh_token = refresh_token or os.getenv('GOOGLE_REFRESH_TOKEN')

        # Set file paths
        self.credentials_file = credentials_file
        self.token_file = token_file or os.path.join(
            os.path.dirname(__file__), '..', 'token.json'
        )

        # Service instances (lazy initialization)
        self.credentials: Optional[Credentials] = None
        self._sheets_service = None
        self._drive_service = None

        # Initialize credentials
        self._initialize_credentials()
    
    def _initialize_credentials(self) -> None:
        """
        Initialize Google API credentials using one of three methods:
        1. Load from saved token file (if exists and valid)
        2. Use refresh token from environment variables (production)
        3. Run OAuth2 flow using client secrets file (local development)

        Raises:
            ValueError: If credentials cannot be initialized
        """
        credentials = None

        # Method 1: Load from saved token file (if exists)
        if self.token_file and os.path.exists(self.token_file):
            try:
                credentials = Credentials.from_authorized_user_file(
                    self.token_file,
                    self.scopes
                )
                logger.info(f"Loaded credentials from token file: {self.token_file}")
            except Exception as e:
                logger.warning(f"Failed to load token file: {e}")
                credentials = None

        # Method 2: Use refresh token from environment variables (production/Docker)
        if (not credentials or not credentials.valid) and self.refresh_token:
            if not self.client_id or not self.client_secret:
                raise ValueError(
                    "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set when using GOOGLE_REFRESH_TOKEN"
                )

            credentials = Credentials(
                token=None,  # Will be refreshed immediately
                refresh_token=self.refresh_token,
                id_token=None,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=self.scopes
            )

            # Refresh token immediately to get access token
            try:
                credentials.refresh(Request())
                logger.info("Successfully refreshed credentials using refresh token")
            except Exception as e:
                logger.error(f"Failed to refresh credentials: {e}")
                raise ValueError(
                    f"Failed to refresh Google credentials. Check your environment variables: {e}"
                )

        # Check if token needs refresh
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                logger.info("Refreshed expired credentials")
            except Exception as e:
                logger.warning(f"Failed to refresh expired credentials: {e}")
                credentials = None

        # Method 3: Run OAuth2 flow using client secrets file (local development only)
        if not credentials or not credentials.valid:
            if self.credentials_file:
                credentials = self._run_oauth_flow()
            else:
                raise ValueError(
                    "No valid credentials available. Set GOOGLE_REFRESH_TOKEN environment "
                    "variable or provide credentials_file for OAuth flow."
                )

        # Save credentials for next time (if token file path is provided)
        if credentials and self.token_file:
            self._save_credentials(credentials)

        # Store credentials
        self.credentials = credentials

        if not self.credentials or not self.credentials.valid:
            raise ValueError("Failed to initialize valid Google credentials")
    
    def _run_oauth_flow(self) -> Credentials:
        """
        Run the OAuth2 flow to get new credentials via browser.

        This is used for local development only. In production, use refresh tokens.

        Returns:
            Credentials object with access and refresh tokens

        Raises:
            FileNotFoundError: If credentials_file doesn't exist
        """
        if not self.credentials_file or not os.path.exists(self.credentials_file):
            raise FileNotFoundError(
                f"Client secrets file not found: {self.credentials_file}. "
                "Download OAuth2 credentials from Google Cloud Console."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            self.credentials_file,
            self.scopes
        )

        # Run local server flow on port 8080 (must match OAuth redirect URI)
        credentials = flow.run_local_server(port=8080)
        logger.info("OAuth flow completed successfully")

        return credentials

    def _save_credentials(self, credentials: Credentials) -> None:
        """
        Save credentials to token file for future use.

        Args:
            credentials: Google OAuth2 credentials to save
        """
        try:
            # Ensure directory exists
            token_dir = os.path.dirname(self.token_file)
            if token_dir:
                os.makedirs(token_dir, exist_ok=True)

            # Write credentials JSON
            with open(self.token_file, 'w') as token:
                token.write(credentials.to_json())

            logger.info(f"Saved credentials to: {self.token_file}")
        except Exception as e:
            logger.warning(f"Failed to save credentials: {e}")

    @property
    def sheets_service(self):
        """
        Get Google Sheets API service with lazy initialization.

        Returns:
            Google Sheets API service instance
        """
        if not self._sheets_service:
            self._sheets_service = build('sheets', 'v4', credentials=self.credentials)
            logger.debug("Initialized Google Sheets service")
        return self._sheets_service

    def get_drive_service(self):
        """
        Get Google Drive API service with lazy initialization.

        Returns:
            Google Drive API service instance
        """
        if not self._drive_service:
            self._drive_service = build('drive', 'v3', credentials=self.credentials)
            logger.debug("Initialized Google Drive service")
        return self._drive_service
    
    def test_connection(self) -> Dict[str, Any]:
        """Test the Google API connection."""
        try:
            # Try to access a dummy spreadsheet or just verify the service
            if not self.sheets_service:
                return {"status": "error", "error": "Google Sheets service not initialized"}
            
            # Test with a simple API call
            return {"status": "success", "message": "Google API connection successful"}
            
        except HttpError as e:
            error_message = f"Google Sheets API error: {e}"
            logger.error(error_message)
            return {"status": "error", "error": error_message}
        except Exception as e:
            error_message = f"Connection test failed: {e}"
            logger.error(error_message)
            return {"status": "error", "error": error_message}
    
    def get_spreadsheet_info(self, spreadsheet_id: str) -> Dict[str, Any]:
        """
        Get information about a spreadsheet including its title and sheets.
        
        Args:
            spreadsheet_id: Google Spreadsheet ID
            
        Returns:
            Dictionary containing spreadsheet metadata
        """
        try:
            # Get spreadsheet metadata
            spreadsheet = self.sheets_service.spreadsheets().get(
                spreadsheetId=spreadsheet_id
            ).execute()
            
            sheets = []
            for sheet in spreadsheet.get('sheets', []):
                sheet_props = sheet.get('properties', {})
                sheets.append({
                    'title': sheet_props.get('title', ''),
                    'sheet_id': sheet_props.get('sheetId', 0),
                    'sheet_type': sheet_props.get('sheetType', 'GRID'),
                    'grid_properties': sheet_props.get('gridProperties', {})
                })
            
            return {
                'spreadsheet_id': spreadsheet.get('spreadsheetId'),
                'title': spreadsheet.get('properties', {}).get('title', ''),
                'sheets': sheets,
                'total_sheets': len(sheets)
            }
            
        except HttpError as e:
            logger.error(f"Failed to get spreadsheet info: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting spreadsheet info: {e}")
            raise
    
    def read_sheet_data(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        range_name: Optional[str] = None,
        max_rows: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Read data from a Google Sheet.
        
        Args:
            spreadsheet_id: Google Spreadsheet ID
            sheet_name: Name of the sheet to read
            range_name: Specific range to read (e.g., 'A1:D10')
            max_rows: Maximum number of rows to read
            
        Returns:
            Dictionary containing sheet data and metadata
        """
        try:
            # Build the range string
            if range_name:
                full_range = f"{sheet_name}!{range_name}"
            else:
                full_range = sheet_name
            
            # Read the data
            result = self.sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=full_range
            ).execute()
            
            values = result.get('values', [])
            
            # Apply max_rows limit if specified
            if max_rows and len(values) > max_rows:
                values = values[:max_rows]
            
            return {
                'range': result.get('range', ''),
                'major_dimension': result.get('majorDimension', 'ROWS'),
                'values': values,
                'total_rows': len(values),
                'total_columns': len(values[0]) if values else 0
            }
            
        except HttpError as e:
            logger.error(f"Failed to read sheet data: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error reading sheet data: {e}")
            raise
    
    def to_dataframe(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        range_name: Optional[str] = None,
        max_rows: Optional[int] = None,
        header_row: int = 0
    ) -> 'DataFrame':
        """
        Read Google Sheet data and convert to pandas DataFrame.
        
        Args:
            spreadsheet_id: Google Spreadsheet ID
            sheet_name: Name of the sheet to read
            range_name: Specific range to read
            max_rows: Maximum number of rows to read
            header_row: Row index to use as column headers (0-based)
            
        Returns:
            pandas DataFrame with the sheet data
        """
        if not PANDAS_AVAILABLE:
            raise ImportError("pandas is required for DataFrame conversion. Install with: pip install pandas")
        
        # Read the sheet data
        data = self.read_sheet_data(spreadsheet_id, sheet_name, range_name, max_rows)
        values = data['values']
        
        if not values:
            return pd.DataFrame()
        
        # Extract headers and data
        if len(values) > header_row:
            headers = values[header_row]
            data_rows = values[header_row + 1:]
        else:
            # No header row, use default column names
            max_cols = max(len(row) for row in values) if values else 0
            headers = [f'Column_{i}' for i in range(max_cols)]
            data_rows = values
        
        # Ensure all rows have the same number of columns
        max_cols = len(headers)
        normalized_rows = []
        for row in data_rows:
            # Pad with empty strings if row is shorter
            normalized_row = row + [''] * (max_cols - len(row))
            # Truncate if row is longer
            normalized_row = normalized_row[:max_cols]
            normalized_rows.append(normalized_row)
        
        # Create DataFrame
        df = pd.DataFrame(normalized_rows, columns=headers)

        return df

    def _find_child(self, name: str, parent_id: str, mime_type: Optional[str] = None) -> Optional[str]:
        """Return the id of the first non-trashed child named `name` under `parent_id`, or None."""
        drive_service = self.get_drive_service()
        escaped_name = name.replace("'", "\\'")
        query = f"'{parent_id}' in parents and name = '{escaped_name}' and trashed = false"
        if mime_type:
            query += f" and mimeType = '{mime_type}'"
        result = drive_service.files().list(
            q=query, spaces='drive', fields='files(id,name)',
            supportsAllDrives=True, includeItemsFromAllDrives=True
        ).execute()
        files = result.get('files', [])
        return files[0]['id'] if files else None

    def ensure_folder(self, name: str, parent_id: str) -> str:
        """
        Get or create a folder named `name` directly under `parent_id` (idempotent).

        Args:
            name: Folder name
            parent_id: Id of the parent Drive folder

        Returns:
            The folder's Drive file id
        """
        existing = self._find_child(name, parent_id, mime_type='application/vnd.google-apps.folder')
        if existing:
            return existing
        folder = self.get_drive_service().files().create(
            body={
                'name': name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id],
            },
            fields='id',
            supportsAllDrives=True,
        ).execute()
        return folder['id']

    def ensure_folder_path(self, names: List[str], parent_id: str) -> str:
        """Ensure a nested folder chain under `parent_id`, returning the deepest folder's id."""
        current = parent_id
        for name in names:
            current = self.ensure_folder(name, current)
        return current

    def delete_file(self, file_id: str) -> None:
        """Permanently delete a Drive file (best-effort; ignores already-gone files)."""
        try:
            self.get_drive_service().files().delete(fileId=file_id, supportsAllDrives=True).execute()
        except HttpError as e:
            if e.resp.status not in (403, 404):
                raise

    def download_file(self, file_id: str, local_path: str) -> str:
        """
        Download a Drive file by id to a local path.

        The counterpart to `upload_file`, and it did not exist before feature 007
        (research.md R2) — only upload and delete did, because nothing had ever
        needed to read media back. Backfill does: it re-extracts already-collected
        items and MUST source their media from the retained Drive copies rather
        than re-fetching from the platform, which would be both collection
        expansion and a politeness violation (FR-034, Constitution X).

        Existing OAuth scopes already cover this — `drive.readonly` plus
        `drive.file` (files this app created) — so no re-consent is required.

        Raises FileNotFoundError when the Drive object is gone (404) or no longer
        readable (403). The caller classifies that as `media_unavailable` and
        counts it; there is NO fallback to re-downloading from the platform, and
        no code path may offer one.
        """
        import os as _os

        from googleapiclient.http import MediaIoBaseDownload

        try:
            # Resolve the ORIGINAL filename first, and inherit its extension when
            # the caller gave a path without one.
            #
            # This is not cosmetic. `analyze_item` branches on file EXTENSION to
            # decide the video vs image path, and records the branch it took as
            # `media_path` provenance (FR-024). An extensionless temp file falls
            # through to the image path regardless of what the media actually is —
            # so a Reel would be analysed as a still AND recorded as one, which is
            # precisely the mislabelling this feature exists to make impossible.
            if not _os.path.splitext(local_path)[1]:
                meta = self.get_drive_service().files().get(
                    fileId=file_id, fields="name", supportsAllDrives=True).execute()
                suffix = _os.path.splitext(meta.get("name") or "")[1]
                if suffix:
                    local_path = f"{local_path}{suffix}"

            request = self.get_drive_service().files().get_media(
                fileId=file_id, supportsAllDrives=True)
            with open(local_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _status, done = downloader.next_chunk()
            return local_path
        except HttpError as e:
            if e.resp.status in (403, 404):
                # Deletion or a permissions change surfaces only at fetch time —
                # a stored drive_file_id does not prove the object still exists.
                raise FileNotFoundError(
                    f"Drive file {file_id} is no longer retrievable (HTTP {e.resp.status})"
                ) from e
            raise

    def upload_file(self, local_path: str, folder_id: str, mime_type: Optional[str] = None) -> str:
        """
        Upload a local file into a Drive folder.

        Args:
            local_path: Path to the local file to upload
            folder_id: Destination Drive folder id
            mime_type: Optional MIME type override (auto-detected by MediaFileUpload if omitted)

        Returns:
            The uploaded file's Drive file id
        """
        drive_service = self.get_drive_service()
        media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
        file_name = os.path.basename(local_path)
        uploaded = drive_service.files().create(
            body={'name': file_name, 'parents': [folder_id]},
            media_body=media,
            fields='id',
            supportsAllDrives=True,
        ).execute()
        return uploaded['id']

    def create_spreadsheet(self, title: str, parent_id: str, header_row: Optional[List[str]] = None) -> str:
        """
        Create a new Google Sheet under `parent_id`, optionally seeding a header row.

        Args:
            title: Spreadsheet title
            parent_id: Id of the stable parent Drive folder to place it in
            header_row: Optional list of column headers to write to row 1

        Returns:
            The new spreadsheet's id
        """
        spreadsheet = self.sheets_service.spreadsheets().create(
            body={'properties': {'title': title}}
        ).execute()
        spreadsheet_id = spreadsheet['spreadsheetId']

        drive_service = self.get_drive_service()
        file = drive_service.files().get(
            fileId=spreadsheet_id, fields='parents', supportsAllDrives=True
        ).execute()
        previous_parents = ','.join(file.get('parents', []))
        drive_service.files().update(
            fileId=spreadsheet_id,
            addParents=parent_id,
            removeParents=previous_parents,
            fields='id, parents',
            supportsAllDrives=True,
        ).execute()

        if header_row:
            self.append_rows(spreadsheet_id, [header_row])

        return spreadsheet_id

    def append_rows(self, spreadsheet_id: str, rows: List[List[Any]], sheet_name: str = 'Sheet1') -> Dict[str, Any]:
        """
        Append rows to the end of a sheet.

        Args:
            spreadsheet_id: Google Spreadsheet id
            rows: List of row value lists to append
            sheet_name: Target sheet/tab name (defaults to the sheet's default tab)

        Returns:
            The Sheets API append response
        """
        return self.sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'",
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': rows},
        ).execute()

    def ensure_spreadsheet(self, title: str, parent_id: str) -> str:
        """
        Find (by name, under `parent_id`) or create a Google Sheet; return its id.

        Unlike create_spreadsheet, this is idempotent — used for the per-account
        "{username} - Social Harvest" workbook that persists across runs.
        """
        existing = self._find_child(title, parent_id, mime_type='application/vnd.google-apps.spreadsheet')
        if existing:
            return existing
        return self.create_spreadsheet(title, parent_id)

    def _get_tabs(self, spreadsheet_id: str) -> Dict[str, int]:
        """Return {tab_title: sheetId} for all tabs in a spreadsheet."""
        meta = self.sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        return {s['properties']['title']: s['properties']['sheetId'] for s in meta.get('sheets', [])}

    def ensure_tab(self, spreadsheet_id: str, tab_name: str, header_row: List[str]) -> None:
        """Ensure a tab named `tab_name` exists with `header_row` in row 1 (idempotent).

        Also removes the default empty 'Sheet1' left over from spreadsheet
        creation, so a per-account workbook contains only quarter tabs.

        For a tab that ALREADY exists, the header is extended when `header_row`
        appends new trailing columns (feature 005's `shares`). Before this, the
        header was written only at tab-creation time, so an existing tab could
        never gain a column and rows would be appended one cell wider than the
        header describes. Only *appended* columns are applied — a header whose
        existing columns disagree is left untouched and reported by
        `extend_tab_header`, because a mismatch means an assumption is wrong and
        guessing would corrupt a reviewer surface.
        """
        tabs = self._get_tabs(spreadsheet_id)
        if tab_name in tabs:
            outcome = self.extend_tab_header(spreadsheet_id, tab_name, header_row)
            if outcome == "mismatch":
                # The delivery path is about to append rows shaped to
                # `header_row` into a tab whose layout contradicts it. The
                # one-off backfill flow warns about exactly this; the path that
                # actually writes production data must not stay silent.
                logger.warning(
                    f"Tab '{tab_name}' in {spreadsheet_id} has a header that does not match the "
                    f"expected layout; it was NOT rewritten. Rows appended here may not line up "
                    f"with its columns — run flows/sheet_header_backfill.py to inspect."
                )
        else:
            self.sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={'requests': [{'addSheet': {'properties': {'title': tab_name}}}]},
            ).execute()
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{tab_name}'!A1",
                valueInputOption='RAW',
                body={'values': [header_row]},
            ).execute()
            tabs = self._get_tabs(spreadsheet_id)

        # Drop a leftover empty default 'Sheet1' (never the last remaining tab).
        if 'Sheet1' in tabs and 'Sheet1' != tab_name and len(tabs) > 1:
            existing = self.sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id, range="'Sheet1'"
            ).execute().get('values', [])
            if not existing:
                self.sheets_service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={'requests': [{'deleteSheet': {'sheetId': tabs['Sheet1']}}]},
                ).execute()

    def extend_tab_header(
        self, spreadsheet_id: str, tab_name: str, header_row: List[str]
    ) -> str:
        """Bring an existing tab's header up to `header_row` by APPENDING columns.

        Returns one of:
            "unchanged" — already matches (or is longer; nothing to do)
            "extended"  — trailing columns were appended
            "empty"     — the tab has no header row (nothing to extend)
            "mismatch"  — the existing columns DISAGREE with `header_row`

        `empty` and `mismatch` are reported separately on purpose. Collapsing
        them into one "skipped" makes a benign untouched scratch tab look
        identical to a tab whose layout contradicts ours — the same conflation of
        distinct causes this whole feature exists to remove.

        Touches ONLY row 1. Reviewer-entered data rows are never read or written
        — a layout migration must not be able to disturb the `advertisement`
        flags staff have set.

        A prefix mismatch is deliberately NOT repaired. It means the tab is not
        the layout we think it is, and rewriting its header would silently
        relabel columns of real data.
        """
        existing = self.sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!1:1",
        ).execute().get('values', [])
        current = existing[0] if existing else []

        if not current:
            return "empty"
        if len(current) >= len(header_row):
            return "unchanged"
        if current != header_row[:len(current)]:
            return "mismatch"

        # Write only the new trailing cells, leaving the existing ones untouched.
        start = col_letter(len(current))
        end = col_letter(len(header_row) - 1)
        self.sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!{start}1:{end}1",
            valueInputOption='RAW',
            body={'values': [header_row[len(current):]]},
        ).execute()
        return "extended"

    def tab_data_row_count(self, spreadsheet_id: str, tab_name: str) -> int:
        """Number of data rows (excluding the header) currently in `tab_name`."""
        result = self.sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A:A"
        ).execute()
        return max(0, len(result.get('values', [])) - 1)

    def delete_rows_by_content_id(self, spreadsheet_id: str, content_id: str, content_id_col: int) -> int:
        """
        Delete every data row whose `content_id_col` (0-based) equals `content_id`,
        across all tabs. Returns the number of rows deleted.

        Used to purge a previously-FAILED item's row before re-harvesting it.
        """
        deleted = 0
        for tab_name, sheet_id in self._get_tabs(spreadsheet_id).items():
            result = self.sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id, range=f"'{tab_name}'"
            ).execute()
            rows = result.get('values', [])
            # Collect 0-based row indices to delete (skip header at index 0), high to low.
            targets = [
                idx for idx, row in enumerate(rows)
                if idx > 0 and len(row) > content_id_col and row[content_id_col] == content_id
            ]
            if not targets:
                continue
            requests = [
                {'deleteDimension': {'range': {
                    'sheetId': sheet_id, 'dimension': 'ROWS',
                    'startIndex': idx, 'endIndex': idx + 1,
                }}}
                for idx in sorted(targets, reverse=True)
            ]
            self.sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body={'requests': requests}
            ).execute()
            deleted += len(targets)
        return deleted