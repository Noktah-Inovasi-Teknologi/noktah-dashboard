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

if TYPE_CHECKING:
    from pandas import DataFrame

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    pd = None

logger = logging.getLogger(__name__)


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
    
    # Scopes (only Sheets and Drive are used in this project)
    scopes: List[str] = Field(
        default=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.readonly'
        ],
        description="Google API scopes (Sheets read/write, Drive read-only)"
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
            'https://www.googleapis.com/auth/drive.readonly'
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