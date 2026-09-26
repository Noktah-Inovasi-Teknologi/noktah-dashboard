"""
Google API Tasks for Prefect workflows

This module contains reusable tasks for interacting with Google APIs
including Sheets, Drive, Calendar, and Documents.
"""
import logging
from typing import Dict, List, Any, Optional
from prefect import task
from prefect.logging import get_run_logger

try:
    from ..blocks.google_credentials import GoogleCredentials
except ImportError:
    # For running as standalone script
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from blocks.google_credentials import GoogleCredentials

logger = logging.getLogger(__name__)


@task(name="google-test-connection", retries=2, retry_delay_seconds=30)
async def google_test_connection(credentials_block_name: str = "google-creds") -> Dict[str, Any]:
    """
    Test Google API connection using credentials block.
    
    Args:
        credentials_block_name: Name of the Google credentials block
        
    Returns:
        Dict containing connection test results
    """
    try:
        # Load credentials from block
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        result = google_creds.test_connection()
        
        if result["status"] != "success":
            logger.error(f"Google API connection failed: {result.get('error', 'Unknown error')}")
            
        return result
    except Exception as e:
        logger.error(f"Connection test failed: {str(e)}")
        return {"status": "error", "error": str(e)}


@task(name="google-read-spreadsheet-info")
async def google_read_spreadsheet_info(
    spreadsheet_id: str, 
    credentials_block_name: str = "google-creds"
) -> Dict[str, Any]:
    """
    Get information about a Google Spreadsheet including its title and sheets.
    
    Args:
        spreadsheet_id: Google Spreadsheet ID
        credentials_block_name: Name of the Google credentials block
        
    Returns:
        Dict containing spreadsheet metadata
    """
    try:
        # Load credentials from block
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        return client.get_spreadsheet_info(spreadsheet_id)
    except Exception as e:
        logger.error(f"Failed to get spreadsheet info: {str(e)}")
        raise


@task(name="google-read-sheet-data")
async def google_read_sheet_data(
    spreadsheet_id: str, 
    sheet_name: str,
    credentials_block_name: str = "google-creds",
    max_rows: Optional[int] = None,
    header_row: int = 0
) -> Dict[str, Any]:
    """
    Read data from a Google Sheet and return as pandas DataFrame records.
    
    Args:
        spreadsheet_id: Google Spreadsheet ID
        sheet_name: Name of the sheet to read
        credentials_block_name: Name of the Google credentials block
        max_rows: Maximum number of rows to read
        header_row: Row index to use as column headers (0-based)
        
    Returns:
        Dict containing sheet data and metadata
    """
    try:
        # Load credentials from block
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        
        # Use pandas DataFrame for data processing
        df = client.to_dataframe(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            max_rows=max_rows,
            header_row=header_row
        )
        
        if df.empty:
            logger.warning(f"No data found in sheet '{sheet_name}'")
            return {"data": [], "dataframe_info": None}
        
        # Return both raw data and DataFrame info
        return {
            "data": df.to_dict('records'),
            "dataframe_info": {
                "row_count": len(df),
                "column_count": len(df.columns),
                "columns": df.columns.tolist(),
                "dtypes": {str(k): str(v) for k, v in df.dtypes.to_dict().items()},
                "memory_usage": int(df.memory_usage(deep=True).sum())
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to read sheet data: {str(e)}")
        raise


@task(name="google-read-sheet-raw")
async def google_read_sheet_raw(
    spreadsheet_id: str,
    sheet_name: str,
    credentials_block_name: str = "google-creds",
    range_name: Optional[str] = None,
    max_rows: Optional[int] = None
) -> Dict[str, Any]:
    """
    Read raw data from a Google Sheet without pandas processing.
    
    Args:
        spreadsheet_id: Google Spreadsheet ID
        sheet_name: Name of the sheet to read
        credentials_block_name: Name of the Google credentials block
        range_name: Specific range to read (e.g., 'A1:D10')
        max_rows: Maximum number of rows to read
        
    Returns:
        Dict containing raw sheet data
    """
    try:
        # Load credentials from block
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        
        # Read raw data
        result = client.read_sheet_data(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            range_name=range_name,
            max_rows=max_rows
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to read raw sheet data: {str(e)}")
        raise


@task(name="google-filter-drive-files")
async def google_filter_drive_files(
    credentials_block_name: str = "google-creds",
    query: Optional[str] = None,
    max_results: int = 100,
    active: bool = True
) -> List[Dict[str, Any]]:
    """
    List files from Google Drive.
    
    Args:
        credentials_block_name: Name of the Google credentials block
        query: Drive query string (e.g., "name contains 'report'")
        max_results: Maximum number of files to return
        active: Whether the filter is active (acts like a faucet)
        
    Returns:
        List of file metadata dictionaries
    """
    try:
        # Check if filter is active
        if not active:
            logger.info("Google Drive filter is inactive, returning empty list")
            return []
        
        # Load credentials from block
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        
        # Get Drive service
        drive_service = client.get_drive_service()
        
        # Build query parameters
        request_params = {
            'pageSize': min(max_results, 1000),  # API limit
            'fields': 'files(id,name,mimeType,size,createdTime,modifiedTime)'
        }
        
        if query:
            request_params['q'] = query
        
        # Execute request
        result = drive_service.files().list(**request_params).execute()
        files = result.get('files', [])
        
        logger.info(f"Found {len(files)} files in Google Drive")
        return files
        
    except Exception as e:
        logger.error(f"Failed to list Drive files: {str(e)}")
        raise


@task(name="google-filter-files-in-folder")
async def google_filter_files_in_folder(
    folder_id: str,
    file_name_pattern: str,
    credentials_block_name: str = "google-creds",
    max_results: int = 50,
    include_subfolders: bool = False,
    active: bool = True
) -> List[Dict[str, Any]]:
    """
    Search for files in a specific Google Drive folder.
    
    Args:
        folder_id: Google Drive folder ID to search in
        file_name_pattern: File name pattern to search for (e.g., "Content Plan")
        credentials_block_name: Name of the Google credentials block
        max_results: Maximum number of files to return
        include_subfolders: Whether to search recursively in subfolders
        
    Returns:
        List of matching file metadata dictionaries
    """
    try:
        # Check if filter is active
        if not active:
            logger.info("Google Drive folder filter is inactive, returning empty list")
            return []
        
        # Load credentials from block
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        
        # Get Drive service
        drive_service = client.get_drive_service()
        
        files = []
        
        if include_subfolders:
            # Search recursively - first get all folders under this folder
            folders_to_search = [folder_id]
            
            # Get all subfolders recursively
            def get_subfolders(parent_folder_id):
                subfolder_query = f"'{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed = false"
                subfolder_result = drive_service.files().list(
                    q=subfolder_query,
                    spaces='drive',
                    fields='nextPageToken, files(id,name,mimeType,parents)'
                ).execute()
                
                subfolders = subfolder_result.get('files', [])
                for subfolder in subfolders:
                    folders_to_search.append(subfolder['id'])
                    get_subfolders(subfolder['id'])  # Recursive call
            
            get_subfolders(folder_id)
            
            # Search in all folders
            for search_folder_id in folders_to_search:
                if file_name_pattern:
                    query = f"'{search_folder_id}' in parents and name contains '{file_name_pattern}' and trashed = false"
                else:
                    query = f"'{search_folder_id}' in parents and trashed = false"
                
                request_params = {
                    'q': query,
                    'spaces': 'drive',
                    'pageSize': min(max_results, 1000),
                    'fields': 'nextPageToken, files(id,name,mimeType,size,createdTime,modifiedTime,parents,shared,ownedByMe)'
                }
                
                result = drive_service.files().list(**request_params).execute()
                folder_files = result.get('files', [])
                files.extend(folder_files)
                
                if len(files) >= max_results:
                    files = files[:max_results]
                    break
        else:
            # Search only in the specified folder - use proper API parameters
            if file_name_pattern:
                query = f"'{folder_id}' in parents and name contains '{file_name_pattern}' and trashed = false"
            else:
                query = f"'{folder_id}' in parents and trashed = false"
            
            logger.info(f"Executing Drive API query: {query}")
            
            request_params = {
                'q': query,
                'spaces': 'drive',
                'pageSize': min(max_results, 1000),
                'fields': 'nextPageToken, files(id,name,mimeType,size,createdTime,modifiedTime,parents,shared,ownedByMe)',
                'supportsAllDrives': True,
                'includeItemsFromAllDrives': True
            }
            
            result = drive_service.files().list(**request_params).execute()
            files = result.get('files', [])
            
            # Log detailed debugging info
            logger.info(f"API response received. Files found: {len(files)}")
            for file in files:
                logger.info(f"  - File: {file.get('name')} (ID: {file.get('id')}, Owned: {file.get('ownedByMe', 'Unknown')})")
        
        logger.info(f"Found {len(files)} files matching '{file_name_pattern}' in folder {folder_id} (include_subfolders={include_subfolders})")
        return files
        
    except Exception as e:
        logger.error(f"Failed to search files in folder: {str(e)}")
        raise


@task(name="drive.folder.ensure", retries=2, retry_delay_seconds=30)
async def drive_folder_ensure(
    name: str,
    parent_id: str,
    credentials_block_name: str = "google-creds"
) -> str:
    """
    Get or create a Drive folder named `name` directly under `parent_id`.

    Idempotent — safe to call every run for the same profile; returns the
    existing folder's id rather than creating a duplicate.

    Args:
        name: Folder name (e.g., the profile's account handle)
        parent_id: Id of the stable parent Drive folder (HARVEST_DRIVE_PARENT_ID)
        credentials_block_name: Name of the Google credentials block

    Returns:
        The folder's Drive file id
    """
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        folder_id = client.ensure_folder(name, parent_id)
        logger.info(f"Ensured Drive folder '{name}' -> {folder_id}")
        return folder_id
    except Exception as e:
        logger.error(f"Failed to ensure Drive folder '{name}': {str(e)}")
        raise


@task(name="drive.file.upload", retries=2, retry_delay_seconds=30)
async def drive_file_upload(
    local_path: str,
    folder_id: str,
    mime_type: Optional[str] = None,
    credentials_block_name: str = "google-creds"
) -> str:
    """
    Upload a local file into a Drive folder.

    Args:
        local_path: Path to the local file to upload
        folder_id: Destination Drive folder id
        mime_type: Optional MIME type override
        credentials_block_name: Name of the Google credentials block

    Returns:
        The uploaded file's Drive file id
    """
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        file_id = client.upload_file(local_path, folder_id, mime_type)
        logger.info(f"Uploaded '{local_path}' to folder {folder_id} -> {file_id}")
        return file_id
    except Exception as e:
        logger.error(f"Failed to upload '{local_path}': {str(e)}")
        raise


@task(name="google.drive.download-file", retries=2, retry_delay_seconds=30)
async def drive_file_download(
    file_id: str,
    local_path: str,
    credentials_block_name: str = "google-creds"
) -> str:
    """
    Download a retained Drive file to a local path.

    The counterpart to `drive.file.upload`, added by feature 007 — it did not
    exist before (research.md R2). Backfill re-extracts already-collected items
    and sources their media from HERE, never from the platform: re-fetching would
    be collection expansion and a politeness violation (FR-034, Constitution X).

    Args:
        file_id: Drive file id (from `harvested_items.drive_file_id`)
        local_path: Destination path on local disk
        credentials_block_name: Name of the Google credentials block

    Returns:
        The local path written.

    Raises:
        FileNotFoundError: the Drive object is gone or unreadable. The caller
            records this as `media_unavailable` and COUNTS it (FR-035) — never a
            silent skip, and never a re-download from the platform.
    """
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        path = client.download_file(file_id, local_path)
        logger.info(f"Downloaded Drive file {file_id} -> {path}")
        return path
    except FileNotFoundError:
        # Expected and classifiable — do not log at ERROR, and do not retry into
        # a 404 three times. Re-raised unchanged for the caller to classify.
        logger.warning(f"Drive file {file_id} is no longer retrievable")
        raise
    except Exception as e:
        logger.error(f"Failed to download Drive file {file_id}: {str(e)}")
        raise


@task(name="sheets.create", retries=2, retry_delay_seconds=30)
async def sheets_create(
    title: str,
    parent_id: str,
    header_row: Optional[List[str]] = None,
    credentials_block_name: str = "google-creds"
) -> str:
    """
    Create a new Google Sheet under `parent_id`, optionally seeding a header row.

    Args:
        title: Spreadsheet title
        parent_id: Id of the stable parent Drive folder (HARVEST_DRIVE_PARENT_ID)
        header_row: Optional list of column headers to write to row 1
        credentials_block_name: Name of the Google credentials block

    Returns:
        The new spreadsheet's id
    """
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        spreadsheet_id = client.create_spreadsheet(title, parent_id, header_row)
        logger.info(f"Created spreadsheet '{title}' -> {spreadsheet_id}")
        return spreadsheet_id
    except Exception as e:
        logger.error(f"Failed to create spreadsheet '{title}': {str(e)}")
        raise


@task(name="sheets.rows.append", retries=2, retry_delay_seconds=30)
async def sheets_rows_append(
    spreadsheet_id: str,
    rows: List[List[Any]],
    sheet_name: str = "Sheet1",
    credentials_block_name: str = "google-creds"
) -> Dict[str, Any]:
    """
    Append rows to the end of a sheet.

    Args:
        spreadsheet_id: Google Spreadsheet id
        rows: List of row value lists to append
        sheet_name: Target sheet/tab name
        credentials_block_name: Name of the Google credentials block

    Returns:
        The Sheets API append response
    """
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        return client.append_rows(spreadsheet_id, rows, sheet_name)
    except Exception as e:
        logger.error(f"Failed to append rows to spreadsheet {spreadsheet_id}: {str(e)}")
        raise


@task(name="drive.file.delete", retries=2, retry_delay_seconds=30)
async def drive_file_delete(file_id: str, credentials_block_name: str = "google-creds") -> None:
    """Permanently delete a Drive file (best-effort). Used to purge failed-run media."""
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        client.delete_file(file_id)
        logger.info(f"Deleted Drive file {file_id}")
    except Exception as e:
        logger.error(f"Failed to delete Drive file {file_id}: {str(e)}")
        raise


@task(name="sheets.spreadsheet.ensure", retries=2, retry_delay_seconds=30)
async def sheets_spreadsheet_ensure(
    title: str, parent_id: str, credentials_block_name: str = "google-creds"
) -> str:
    """Find (by name under parent) or create a Google Sheet; return its id (idempotent)."""
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        spreadsheet_id = client.ensure_spreadsheet(title, parent_id)
        logger.info(f"Ensured spreadsheet '{title}' -> {spreadsheet_id}")
        return spreadsheet_id
    except Exception as e:
        logger.error(f"Failed to ensure spreadsheet '{title}': {str(e)}")
        raise


@task(name="sheets.tab.ensure", retries=2, retry_delay_seconds=30)
async def sheets_tab_ensure(
    spreadsheet_id: str, tab_name: str, header_row: List[str],
    credentials_block_name: str = "google-creds"
) -> None:
    """Ensure a tab named `tab_name` exists with the given header row (idempotent)."""
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        client.ensure_tab(spreadsheet_id, tab_name, header_row)
    except Exception as e:
        logger.error(f"Failed to ensure tab '{tab_name}' in {spreadsheet_id}: {str(e)}")
        raise


def column_index(header_row: List[str], name: str) -> int:
    """0-based index of `name` in a sheet's header row.

    Raises `KeyError` when the column is absent (FR-016b). Deliberately not
    tolerant: silently defaulting to a positional index is worse than a failed
    read, because it would write a reviewer's advertisement flag onto unrelated
    content. A missing expected column means the tab is not the layout we think
    it is, and that must stop the caller.
    """
    try:
        return header_row.index(name)
    except ValueError:
        raise KeyError(
            f"column {name!r} not found in header {header_row!r}; "
            f"the tab may predate a layout change — run flows/sheet_header_backfill.py"
        ) from None


@task(name="google.sheets.ensure-header", retries=2, retry_delay_seconds=30)
async def sheets_ensure_header(
    spreadsheet_id: str, tab_name: str, header_row: List[str],
    credentials_block_name: str = "google-creds",
) -> str:
    """Bring one existing tab's header up to `header_row` by appending columns.

    Returns "unchanged", "extended", "empty", or "mismatch". Shared by the delivery path
    (via `sheets_tab_ensure`) and `sheet-header-backfill`, so one definition
    governs both and they cannot drift into disagreeing about the layout.
    """
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        return client.extend_tab_header(spreadsheet_id, tab_name, header_row)
    except Exception as e:
        logger.error(f"Failed to ensure header on '{tab_name}' in {spreadsheet_id}: {str(e)}")
        raise


@task(name="sheets.tab.row-count", retries=2, retry_delay_seconds=30)
async def sheets_tab_row_count(
    spreadsheet_id: str, tab_name: str, credentials_block_name: str = "google-creds"
) -> int:
    """Return the number of data rows (excluding header) in a tab."""
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        return client.tab_data_row_count(spreadsheet_id, tab_name)
    except Exception as e:
        logger.error(f"Failed to count rows in '{tab_name}' of {spreadsheet_id}: {str(e)}")
        raise


@task(name="sheets.rows.delete-by-content-id", retries=2, retry_delay_seconds=30)
async def sheets_rows_delete_by_content_id(
    spreadsheet_id: str, content_id: str, content_id_col: int,
    credentials_block_name: str = "google-creds"
) -> int:
    """Delete all rows across all tabs whose content_id column matches; return count deleted."""
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        deleted = client.delete_rows_by_content_id(spreadsheet_id, content_id, content_id_col)
        if deleted:
            logger.info(f"Deleted {deleted} row(s) for content_id {content_id} in {spreadsheet_id}")
        return deleted
    except Exception as e:
        logger.error(f"Failed to delete rows for content_id {content_id}: {str(e)}")
        raise




@task(name="google.sheets.write-cells", retries=2, retry_delay_seconds=30)
async def sheets_write_cells(
    spreadsheet_id: str,
    cells: Dict[str, Any],
    credentials_block_name: str = "google-creds",
) -> Dict[str, Any]:
    """Write single cells by A1 address, e.g. {"'Sheet1'!T5": '=HYPERLINK("…","ESKL-12")'}.

    One `values.batchUpdate` with USER_ENTERED, so a formula is stored as a formula. Only
    the addressed cells are touched; nothing else in the sheet is rewritten.
    """
    if not cells:
        return {"totalUpdatedCells": 0}
    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        body = {
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": a1, "values": [[value]]} for a1, value in cells.items()],
        }
        return client.sheets_service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id, body=body).execute()
    except Exception as e:
        logger.error(f"Failed to write {len(cells)} cell(s) in {spreadsheet_id}: {str(e)}")
        raise


@task(name="google.drive.upload-doc", retries=2, retry_delay_seconds=30)
async def drive_upload_doc(
    name: str,
    html: str,
    folder_id: str,
    credentials_block_name: str = "google-creds",
) -> str:
    """Upload HTML as a Google Doc (Drive converts it) into `folder_id`; returns the file id."""
    from googleapiclient.http import MediaIoBaseUpload
    import io

    try:
        google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
        client = google_creds.get_client()
        media = MediaIoBaseUpload(io.BytesIO(html.encode("utf-8")), mimetype="text/html", resumable=False)
        created = client.get_drive_service().files().create(
            body={"name": name, "mimeType": "application/vnd.google-apps.document", "parents": [folder_id]},
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        logger.info(f"Uploaded Google Doc '{name}' to folder {folder_id} -> {created['id']}")
        return created["id"]
    except Exception as e:
        logger.error(f"Failed to upload Google Doc '{name}': {str(e)}")
        raise
