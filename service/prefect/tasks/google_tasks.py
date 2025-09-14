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
        google_creds = await GoogleCredentials.load(credentials_block_name)
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
        google_creds = await GoogleCredentials.load(credentials_block_name)
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
        google_creds = await GoogleCredentials.load(credentials_block_name)
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
        google_creds = await GoogleCredentials.load(credentials_block_name)
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
        google_creds = await GoogleCredentials.load(credentials_block_name)
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
        google_creds = await GoogleCredentials.load(credentials_block_name)
        client = google_creds.get_client()
        
        # Get Drive service
        drive_service = client.get_drive_service()
        
        files = []
        
        if include_subfolders:
            # Search recursively - first get all folders under this folder
            folders_to_search = [folder_id]
            
            # Get all subfolders recursively
            def get_subfolders(parent_folder_id):
                subfolder_query = f"'{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder'"
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
                    query = f"'{search_folder_id}' in parents and name contains '{file_name_pattern}'"
                else:
                    query = f"'{search_folder_id}' in parents"
                
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
                query = f"'{folder_id}' in parents and name contains '{file_name_pattern}'"
            else:
                query = f"'{folder_id}' in parents"
            
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


