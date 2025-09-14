"""
Content Plan Spreadsheet Reader Workflow

This workflow reads content plan data from a Google Spreadsheet and outputs
the rows for further processing.
"""
import os
import json
import logging
import asyncio
import random
import re
from datetime import datetime
from typing import Dict, List, Any, Optional
from prefect import flow, task
from prefect.logging import get_run_logger

# Import tasks from tasks modules
try:
    from ..tasks.google_tasks import (
        google_test_connection,
        google_read_spreadsheet_info,
        google_read_sheet_data,
        google_filter_files_in_folder
    )
    from ..tasks.utility_tasks import (
        get_date,
        process_row_uniform,
        save_to_json,
        convert_content_plan_row_to_jira_issue
    )
except ImportError:
    # For running as standalone script
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from tasks.google_tasks import (
        google_test_connection,
        google_read_spreadsheet_info,
        google_read_sheet_data,
        google_filter_files_in_folder
    )
    from tasks.utility_tasks import (
        get_date,
        process_row_uniform,
        save_to_json,
        convert_content_plan_row_to_jira_issue
    )

logger = logging.getLogger(__name__)

# Configuration
SPREADSHEET_ID = "1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY"
SHEET_NAME = "Clients"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@flow(name="read-content-plan", description="Read content plan data from Google Spreadsheet")
async def read_content_plan_flow(
    spreadsheet_id: str = SPREADSHEET_ID,
    sheet_name: str = SHEET_NAME,
    max_rows: Optional[int] = None,
    credentials_block_name: str = "google-creds"
):
    """
    Read content plan data from Google Spreadsheet
    
    Args:
        spreadsheet_id: Google Spreadsheet ID
        sheet_name: Name of the sheet to read from
        max_rows: Maximum number of rows to read (for testing)
        credentials_block_name: Name of the Google credentials block
    """
    results = {
        "start_time": datetime.now().isoformat(),
        "spreadsheet_id": spreadsheet_id,
        "sheet_name": sheet_name,
        "data": [],
        "summary": {}
    }
    
    try:
        # Test connection
        sheets_test = await google_test_connection(credentials_block_name)
        if sheets_test["status"] != "success":
            results["error"] = f"Google API connection failed: {sheets_test.get('error')}"
            return results
        
        # Get spreadsheet info and validate sheet
        spreadsheet_info = await google_read_spreadsheet_info(spreadsheet_id, credentials_block_name)
        results["spreadsheet_title"] = spreadsheet_info["title"]
        
        sheet_found = any(sheet["title"] == sheet_name for sheet in spreadsheet_info["sheets"])
        if not sheet_found:
            available_sheets = [sheet["title"] for sheet in spreadsheet_info["sheets"]]
            results["error"] = f"Sheet '{sheet_name}' not found. Available sheets: {available_sheets}"
            return results
        
        # Read data using pandas
        content_result = await google_read_sheet_data(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            credentials_block_name=credentials_block_name,
            max_rows=max_rows,
            header_row=0
        )
        content_data = content_result["data"]
        dataframe_info = content_result["dataframe_info"]
        
        if not content_data:
            results["error"] = "No content data found in the spreadsheet"
            return results
        
        results["data"] = content_data
        results["total_rows"] = len(content_data)
        results["end_time"] = datetime.now().isoformat()
        results["dataframe_info"] = dataframe_info
        results["summary"] = {
            "total_rows": len(content_data),
            "columns": list(content_data[0].keys()) if content_data else [],
            "sample_data": content_data[:3] if len(content_data) >= 3 else content_data
        }
        
        return results
        
    except Exception as e:
        logger.error(f"Workflow failed: {str(e)}")
        results["error"] = str(e)
        results["end_time"] = datetime.now().isoformat()
        return results


# New flow to search for content plan spreadsheets in client folders
@flow(name="search-content-plan-files", description="Search for content plan spreadsheets in client folders")
async def search_content_plan_files_flow(
    spreadsheet_id: str = SPREADSHEET_ID,
    sheet_name: str = SHEET_NAME,
    credentials_block_name: str = "google-creds",
    target_month: Optional[str] = None
):
    """
    Search for content plan spreadsheets in each client's Content Plan folder
    
    Args:
        spreadsheet_id: Google Spreadsheet ID for client data
        sheet_name: Name of the sheet containing client data
        credentials_block_name: Name of the Google credentials block
        target_month: Specific month to search for (e.g., "September 2025", "Januari 2024"). 
                     If None, searches for next month.
    """
    results = {
        "start_time": datetime.now().isoformat(),
        "clients": [],
        "summary": {}
    }
    
    try:
        # First get the client data
        client_data_result = await read_content_plan_flow(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            credentials_block_name=credentials_block_name
        )
        
        if "error" in client_data_result:
            results["error"] = f"Failed to get client data: {client_data_result['error']}"
            return results
        
        clients = client_data_result["data"]
        
        # Get target month - either specified or next month in Indonesian format
        if target_month:
            search_month = await get_date(
                date_input=target_month,
                format_type="month_year",
                language="indonesian"
            )
        else:
            search_month = await get_date(
                offset_months=1,
                format_type="month_year", 
                language="indonesian"
            )
        
        # Search for content plan files for each client
        output_list = []
        
        for index, client in enumerate(clients, 1):
            client_name = client.get("Name", "")
            content_plan_folder_id = client.get("Content Plan Folder ID", "")
            
            if not client_name or not content_plan_folder_id:
                logger.warning(f"Missing data for client: {client}")
                continue
            
            # Build expected file name pattern
            expected_filename = f"Content Plan - {client_name} - {search_month}"
            
            try:
                # Search for files in the client's content plan folder
                matching_files = await google_filter_files_in_folder(
                    folder_id=content_plan_folder_id,
                    file_name_pattern=f"Content Plan",
                    credentials_block_name=credentials_block_name,
                    active=True
                )
                
                # Look for exact match (more robust matching)
                exact_match = None
                for file in matching_files:
                    file_name = file.get("name", "").strip()
                    # Try exact match first
                    if file_name == expected_filename:
                        exact_match = file
                        break
                    # Try substring match as fallback
                    elif expected_filename in file_name:
                        exact_match = file
                        break
                
                # Add to output list with required format
                if exact_match:
                    output_list.append({
                        "number": index,
                        "client_name": client_name,
                        "content_plan_id": exact_match.get("id", "")
                    })
                else:
                    output_list.append({
                        "number": index,
                        "client_name": client_name,
                        "content_plan_id": None
                    })
                
                # Keep detailed results for summary
                client_result = {
                    "client_name": client_name,
                    "content_plan_folder_id": content_plan_folder_id,
                    "expected_filename": expected_filename,
                    "files_found": matching_files,
                    "exact_match": exact_match
                }
                results["clients"].append(client_result)
                
            except Exception as e:
                logger.error(f"Failed to search files for client {client_name}: {str(e)}")
                output_list.append({
                    "number": index,
                    "client_name": client_name,
                    "content_plan_id": None,
                    "error": str(e)
                })
                results["clients"].append({
                    "client_name": client_name,
                    "content_plan_folder_id": content_plan_folder_id,
                    "expected_filename": expected_filename,
                    "error": str(e)
                })
        
        # Add output list and summary
        results["output"] = output_list
        results["summary"] = {
            "total_clients": len(clients),
            "clients_processed": len(output_list),
            "clients_with_content_plans": sum(1 for item in output_list if item.get("content_plan_id")),
            "clients_without_content_plans": sum(1 for item in output_list if not item.get("content_plan_id")),
            "search_month": search_month,
            "target_month_input": target_month
        }
        
        results["end_time"] = datetime.now().isoformat()
        return results
        
    except Exception as e:
        logger.error(f"Flow failed: {str(e)}")
        results["error"] = str(e)
        results["end_time"] = datetime.now().isoformat()
        return results


# Filtering flow for debugging specific clients
@flow(name="filter-content-plan-results", description="Filter content plan results by client number or name")
async def filter_content_plan_results_flow(
    target_month: Optional[str] = None,
    client_numbers: Optional[List[int]] = None,
    client_names: Optional[List[str]] = None,
    spreadsheet_id: str = SPREADSHEET_ID,
    sheet_name: str = SHEET_NAME,
    credentials_block_name: str = "google-creds"
):
    """
    Filter content plan search results by specific client numbers or names for debugging.
    
    Args:
        target_month: Specific month to search for (e.g., "September 2025")
        client_numbers: List of client numbers to include (e.g., [1, 3, 5])
        client_names: List of client names to include (e.g., ["Klinik Utama Gresik"])
        spreadsheet_id: Google Spreadsheet ID for client data
        sheet_name: Name of the sheet containing client data
        credentials_block_name: Name of the Google credentials block
        
    Returns:
        Filtered results with only specified clients
    """
    results = {
        "start_time": datetime.now().isoformat(),
        "filter_criteria": {
            "client_numbers": client_numbers,
            "client_names": client_names,
            "target_month": target_month
        },
        "filtered_output": [],
        "summary": {}
    }
    
    try:
        # Get all content plan results first
        all_results = await search_content_plan_files_flow(
            target_month=target_month,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            credentials_block_name=credentials_block_name
        )
        
        if "error" in all_results:
            results["error"] = f"Failed to get content plan data: {all_results['error']}"
            return results
        
        all_output = all_results["output"]
        filtered_output = []
        
        # Apply filters
        for item in all_output:
            include_item = False
            
            # Filter by client numbers
            if client_numbers and item["number"] in client_numbers:
                include_item = True
            
            # Filter by client names (case-insensitive partial match)
            if client_names:
                for name_filter in client_names:
                    if name_filter.lower() in item["client_name"].lower():
                        include_item = True
                        break
            
            # If no filters specified, include all
            if not client_numbers and not client_names:
                include_item = True
            
            if include_item:
                filtered_output.append(item)
        
        results["filtered_output"] = filtered_output
        results["original_count"] = len(all_output)
        results["filtered_count"] = len(filtered_output)
        results["summary"] = {
            "original_total": len(all_output),
            "filtered_total": len(filtered_output),
            "clients_with_content_plans": sum(1 for item in filtered_output if item.get("content_plan_id")),
            "clients_without_content_plans": sum(1 for item in filtered_output if not item.get("content_plan_id")),
            "search_month": all_results["summary"]["search_month"],
            "filter_applied": bool(client_numbers or client_names)
        }
        
        results["end_time"] = datetime.now().isoformat()
        return results
        
    except Exception as e:
        logger.error(f"Filter flow failed: {str(e)}")
        results["error"] = str(e)
        results["end_time"] = datetime.now().isoformat()
        return results


# Content Plan Reader Flow - reads data from content plan spreadsheets
@flow(name="read-content-plan-data", description="Read data from content plan spreadsheets with delays")
async def read_content_plan_data_flow(
    target_month: Optional[str] = None,
    client_numbers: Optional[List[int]] = None,
    client_names: Optional[List[str]] = None,
    min_delay_seconds: int = 5,
    max_delay_seconds: int = 10,
    credentials_block_name: str = "google-creds"
):
    """
    Read content plan data from each client's spreadsheet with random delays.
    
    Args:
        target_month: Specific month to search for (e.g., "September 2025") 
        client_numbers: List of client numbers to include (e.g., [1, 3, 5])
        client_names: List of client names to include (e.g., ["Klinik Utama Gresik"])
        min_delay_seconds: Minimum delay between requests (default: 5)
        max_delay_seconds: Maximum delay between requests (default: 10)
        credentials_block_name: Name of the Google credentials block
        
    Returns:
        Dict containing content plan data for each client
    """
    results = {
        "start_time": datetime.now().isoformat(),
        "content_plans": [],
        "summary": {},
        "processing_info": {
            "min_delay": min_delay_seconds,
            "max_delay": max_delay_seconds,
            "total_processing_time": 0
        }
    }
    
    try:
        # Get filtered content plan results
        if client_numbers or client_names:
            filtered_results = await filter_content_plan_results_flow(
                target_month=target_month,
                client_numbers=client_numbers,
                client_names=client_names,
                credentials_block_name=credentials_block_name
            )
            
            if "error" in filtered_results:
                results["error"] = f"Failed to get filtered results: {filtered_results['error']}"
                return results
                
            content_plan_list = filtered_results["filtered_output"]
        else:
            all_results = await search_content_plan_files_flow(
                target_month=target_month,
                credentials_block_name=credentials_block_name
            )
            
            if "error" in all_results:
                results["error"] = f"Failed to get content plan data: {all_results['error']}"
                return results
                
            content_plan_list = all_results["output"]
        
        # Process each content plan with delays
        processing_start_time = datetime.now()
        
        for index, content_plan in enumerate(content_plan_list):
            client_name = content_plan["client_name"]
            content_plan_id = content_plan.get("content_plan_id")
            
            if not content_plan_id:
                results["content_plans"].append({
                    "number": content_plan["number"],
                    "client_name": client_name,
                    "content_plan_id": None,
                    "error": "No content plan ID available"
                })
                continue
            
            try:
                # Read content plan spreadsheet data
                content_plan_data = await google_read_sheet_data(
                    spreadsheet_id=content_plan_id,
                    sheet_name="Sheet1",  # Default sheet name
                    credentials_block_name=credentials_block_name,
                    header_row=0
                )
                
                # Store the result
                client_result = {
                    "number": content_plan["number"],
                    "client_name": client_name,
                    "content_plan_id": content_plan_id,
                    "data": content_plan_data["data"],
                    "dataframe_info": content_plan_data["dataframe_info"],
                    "processing_timestamp": datetime.now().isoformat()
                }
                
                results["content_plans"].append(client_result)
                
            except Exception as e:
                logger.error(f"Failed to read content plan for {client_name}: {str(e)}")
                results["content_plans"].append({
                    "number": content_plan["number"],
                    "client_name": client_name,
                    "content_plan_id": content_plan_id,
                    "error": str(e),
                    "processing_timestamp": datetime.now().isoformat()
                })
            
            # Add random delay between requests (except for the last item)
            if index < len(content_plan_list) - 1:
                delay_seconds = random.uniform(min_delay_seconds, max_delay_seconds)
                await asyncio.sleep(delay_seconds)
        
        processing_end_time = datetime.now()
        total_processing_time = (processing_end_time - processing_start_time).total_seconds()
        
        # Update results with summary
        results["processing_info"]["total_processing_time"] = total_processing_time
        results["summary"] = {
            "total_content_plans": len(content_plan_list),
            "successfully_processed": sum(1 for cp in results["content_plans"] if "data" in cp),
            "failed_processing": sum(1 for cp in results["content_plans"] if "error" in cp),
            "total_processing_time_seconds": total_processing_time,
            "average_delay_seconds": (min_delay_seconds + max_delay_seconds) / 2
        }
        
        results["end_time"] = datetime.now().isoformat()
        return results
        
    except Exception as e:
        logger.error(f"Content plan reader flow failed: {str(e)}")
        results["error"] = str(e)
        results["end_time"] = datetime.now().isoformat()
        return results


@flow(name="format-data-processor", description="Process and format spreadsheet data uniformly")
async def format_data_processor_flow(
    spreadsheet_id: str = SPREADSHEET_ID,
    sheet_name: str = SHEET_NAME,
    max_rows: Optional[int] = 1,
    credentials_block_name: str = "google-creds",
    output_filename: Optional[str] = None,
    timestamp: Optional[str] = None
):
    """
    Process spreadsheet data with uniform formatting and output to JSON
    
    Args:
        spreadsheet_id: Google Spreadsheet ID
        sheet_name: Name of the sheet to read from  
        max_rows: Maximum number of rows to process (default: 1)
        credentials_block_name: Name of the Google credentials block
        output_filename: Custom output filename (optional)
    """
    logger = get_run_logger()
    
    results = {
        "start_time": datetime.now().isoformat() + "Z",
        "spreadsheet_id": spreadsheet_id,
        "sheet_name": sheet_name,
        "max_rows": max_rows,
        "processed_rows": [],
        "summary": {}
    }
    
    try:
        # Test Google API connection
        sheets_test = await google_test_connection(credentials_block_name)
        if sheets_test["status"] != "success":
            results["error"] = f"Google API connection failed: {sheets_test.get('error')}"
            return results
        
        # Get spreadsheet info and validate sheet
        spreadsheet_info = await google_read_spreadsheet_info(spreadsheet_id, credentials_block_name)
        results["spreadsheet_title"] = spreadsheet_info["title"]
        
        sheet_found = any(sheet["title"] == sheet_name for sheet in spreadsheet_info["sheets"])
        if not sheet_found:
            available_sheets = [sheet["title"] for sheet in spreadsheet_info["sheets"]]
            results["error"] = f"Sheet '{sheet_name}' not found. Available sheets: {available_sheets}"
            return results
        
        # Read data from spreadsheet
        content_result = await google_read_sheet_data(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            credentials_block_name=credentials_block_name,
            max_rows=max_rows,
            header_row=0
        )
        
        raw_data = content_result["data"]
        if not raw_data:
            results["error"] = "No data found in the spreadsheet"
            return results
        
        
        # Process each row using the utility task
        processed_rows = []
        for index, row in enumerate(raw_data):
            processed_row = process_row_uniform(row, index + 1)
            processed_rows.append(processed_row)
        
        results["processed_rows"] = processed_rows
        results["total_rows_processed"] = len(processed_rows)
        
        # Generate output filename with timestamp
        if not timestamp:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        run_output_dir = os.path.join(OUTPUT_DIR, timestamp)
        os.makedirs(run_output_dir, exist_ok=True)
        
        if not output_filename:
            output_filename = "formatted_data.json"
        
        output_path = os.path.join(run_output_dir, output_filename)
        
        # Prepare output data structure with metadata
        output_data = {
            "metadata": {
                "source_spreadsheet_id": spreadsheet_id,
                "source_sheet_name": sheet_name,
                "source_spreadsheet_title": results["spreadsheet_title"],
                "processed_at": datetime.now().isoformat() + "Z",
                "total_rows": len(processed_rows),
                "format_standards": {
                    "date_time": "ISO 8601 (YYYY-MM-DDTHH:MM:SSZ)",
                    "text": "Consistent spacing, normalized line breaks, max 2 consecutive newlines",
                    "numeric": "Normalized float values, currency symbols removed",
                    "field_names": "Lowercase with underscores, standardized"
                }
            },
            "data": processed_rows
        }
        
        # Save to JSON file using utility task
        saved_path = save_to_json(output_data, output_path)
        
        results["output_file"] = saved_path
        results["summary"] = {
            "total_rows_processed": len(processed_rows),
            "output_file_path": saved_path,
            "format_standards_applied": [
                "ISO 8601 date/time formatting",
                "Consistent text spacing and newlines", 
                "Normalized numeric values",
                "Standardized field naming (lowercase, underscores)"
            ]
        }
        
        results["end_time"] = datetime.now().isoformat() + "Z"
        
        return results
        
    except Exception as e:
        logger.error(f"Data processing flow failed: {str(e)}")
        results["error"] = str(e)
        results["end_time"] = datetime.now().isoformat() + "Z"
        return results


@flow(name="convert-content-plan-to-jira-assets", description="Convert content plan rows to Jira issue type 10009 format")
async def convert_content_plan_to_jira_assets_flow(
    target_month: Optional[str] = None,
    client_numbers: Optional[List[int]] = None,
    client_names: Optional[List[str]] = None,
    credentials_block_name: str = "google-creds",
    component_hashmap: Optional[Dict[str, str]] = None,
    timestamp: Optional[str] = None
):
    """
    Convert content plan data to Jira issue type 10009 (Asset) format
    
    Args:
        target_month: Specific month to search for (e.g., "September 2025")
        client_numbers: List of client numbers to include (e.g., [1, 3, 5])
        client_names: List of client names to include (e.g., ["Klinik Utama Gresik"])
        credentials_block_name: Name of the Google credentials block
        component_hashmap: Custom mapping of client names to component IDs
        
    Returns:
        Dict containing converted Jira assets for each content plan row
    """
    results = {
        "start_time": datetime.now().isoformat(),
        "jira_assets": [],
        "summary": {}
    }
    
    try:
        # Get content plan data first
        content_plan_results = await read_content_plan_data_flow(
            target_month=target_month,
            client_numbers=client_numbers,
            client_names=client_names,
            credentials_block_name=credentials_block_name
        )
        
        if "error" in content_plan_results:
            results["error"] = f"Failed to get content plan data: {content_plan_results['error']}"
            return results
        
        # Process each client's content plan
        total_assets_created = 0
        for client_data in content_plan_results["content_plans"]:
            client_name = client_data["client_name"]
            
            if "error" in client_data or "data" not in client_data:
                logger.warning(f"Skipping client {client_name} due to missing data")
                continue
            
            # Convert each row to Jira asset
            client_assets = []
            for row in client_data["data"]:
                try:
                    jira_asset = convert_content_plan_row_to_jira_issue(
                        row=row,
                        client_name=client_name,
                        component_hashmap=component_hashmap
                    )
                    client_assets.append(jira_asset)
                    total_assets_created += 1
                except Exception as e:
                    logger.error(f"Failed to convert row for {client_name}: {str(e)}")
            
            # Add client result
            if client_assets:
                results["jira_assets"].append({
                    "client_name": client_name,
                    "content_plan_id": client_data.get("content_plan_id"),
                    "assets": client_assets,
                    "asset_count": len(client_assets)
                })
        
        # Save to JSON file
        if not timestamp:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        run_output_dir = os.path.join(OUTPUT_DIR, timestamp)
        os.makedirs(run_output_dir, exist_ok=True)
        
        # Save separate JSON file for each client
        client_files = []
        for client_data in results["jira_assets"]:
            client_name = client_data["client_name"]
            
            # Create safe filename from client name
            safe_client_name = re.sub(r'[^\w\s-]', '', client_name).strip()
            safe_client_name = re.sub(r'[-\s]+', '_', safe_client_name)
            
            # Create client-specific JSON data
            client_output_data = {
                "metadata": {
                    "client_name": client_name,
                    "content_plan_id": client_data.get("content_plan_id"),
                    "converted_at": datetime.now().isoformat() + "Z",
                    "total_assets": client_data["asset_count"],
                    "target_month": target_month,
                    "jira_format": "issue_type_10009_asset"
                },
                "issue_updates": client_data["assets"]
            }
            
            # Save client file
            client_filename = f"jira_issues_{safe_client_name}.json"
            client_output_path = os.path.join(run_output_dir, client_filename)
            client_saved_path = save_to_json(client_output_data, client_output_path)
            
            client_files.append({
                "client_name": client_name,
                "file_path": client_saved_path,
                "asset_count": client_data["asset_count"]
            })
        
        # Also save combined file for reference
        combined_output_data = {
            "metadata": {
                "converted_at": datetime.now().isoformat() + "Z",
                "total_clients_processed": len([c for c in content_plan_results["content_plans"] if "data" in c]),
                "total_assets_created": total_assets_created,
                "target_month": target_month,
                "filter_criteria": {
                    "client_numbers": client_numbers,
                    "client_names": client_names
                }
            },
            "jira_assets": results["jira_assets"]
        }
        
        combined_output_path = os.path.join(run_output_dir, "content_plan_jira_assets_combined.json")
        combined_saved_path = save_to_json(combined_output_data, combined_output_path)
        
        results["output_files"] = {
            "client_files": client_files,
            "combined_file": combined_saved_path
        }
        results["summary"] = {
            "total_clients_processed": len([c for c in content_plan_results["content_plans"] if "data" in c]),
            "total_assets_created": total_assets_created,
            "client_files_created": len(client_files),
            "combined_file_path": combined_saved_path
        }
        
        results["end_time"] = datetime.now().isoformat()
        return results
        
    except Exception as e:
        logger.error(f"Jira asset conversion flow failed: {str(e)}")
        results["error"] = str(e)
        results["end_time"] = datetime.now().isoformat()
        return results


@flow(name="bulk-create-jira-issues-per-client", description="Create Jira issues in bulk for each client separately")
async def bulk_create_jira_issues_per_client_flow(
    client_files: List[Dict[str, Any]],
    max_issues: int = 45,
    credentials_block_name: str = "jira-creds",
    validate_only: bool = False,
    timestamp: Optional[str] = None
):
    """
    Create Jira issues in bulk for each client separately to avoid API limits.
    
    Args:
        client_files: List of client file information from convert flow
        max_issues: Maximum number of issues per client (default: 45)
        credentials_block_name: Name of the Jira credentials block
        validate_only: If True, only validate data without creating issues
        timestamp: Custom timestamp for output files
        
    Returns:
        Dict containing bulk creation results for each client
    """
    try:
        from ..tasks.jira_tasks import (
            get_server_info,
            create_issues_bulk,
            read_jira_formatted_json,
            validate_bulk_issue_data
        )
    except ImportError:
        # For running as standalone script
        from tasks.jira_tasks import (
            get_server_info,
            create_issues_bulk,
            read_jira_formatted_json,
            validate_bulk_issue_data
        )
    
    results = {
        "start_time": datetime.now().isoformat(),
        "client_results": [],
        "summary": {}
    }
    
    try:
        # Test Jira connection once (skip if validation only)
        if not validate_only:
            jira_test = await get_server_info(credentials_block_name)
            if jira_test["status"] != "success":
                results["error"] = f"Jira connection failed: {jira_test.get('error')}"
                return results
            results["jira_connection"] = jira_test
        
        total_issues_created = 0
        total_clients_processed = 0
        
        # Process each client file
        for client_info in client_files:
            client_name = client_info["client_name"]
            file_path = client_info["file_path"]
            
            try:
                # Read client's JSON data
                json_data = await read_jira_formatted_json(file_path)
                if json_data["status"] != "success":
                    results["client_results"].append({
                        "client_name": client_name,
                        "status": "error",
                        "error": f"Failed to read JSON: {json_data.get('error')}"
                    })
                    continue
                
                # Validate issue data
                validation_result = await validate_bulk_issue_data(
                    issue_updates=json_data["issue_updates"],
                    max_issues=max_issues
                )
                
                if validation_result["status"] != "success":
                    results["client_results"].append({
                        "client_name": client_name,
                        "status": "error",
                        "error": f"Validation failed: {validation_result.get('error')}"
                    })
                    continue
                
                client_result = {
                    "client_name": client_name,
                    "file_path": file_path,
                    "validation": validation_result
                }
                
                # If validation only, skip creation
                if validate_only:
                    client_result["status"] = "validated"
                    results["client_results"].append(client_result)
                    total_clients_processed += 1
                    continue
                
                # Create issues in bulk for this client
                if validation_result["final_count"] > 0:
                    bulk_result = await create_issues_bulk(
                        issue_updates=validation_result["valid_issues"],
                        credentials_block_name=credentials_block_name,
                        max_issues=max_issues
                    )
                    
                    client_result["bulk_creation"] = bulk_result
                    
                    if bulk_result["status"] == "success":
                        client_result["status"] = "success"
                        client_result["issues_created"] = bulk_result["total_created"]
                        total_issues_created += bulk_result["total_created"]
                    else:
                        client_result["status"] = "error" 
                        client_result["error"] = bulk_result.get("error")
                else:
                    client_result["status"] = "no_valid_issues"
                    client_result["issues_created"] = 0
                
                results["client_results"].append(client_result)
                total_clients_processed += 1
                
                # Add delay between clients to avoid rate limiting
                if len(client_files) > 1:
                    await asyncio.sleep(2)
                
            except Exception as e:
                results["client_results"].append({
                    "client_name": client_name,
                    "status": "error",
                    "error": str(e)
                })
        
        # Create summary
        results["summary"] = {
            "total_clients": len(client_files),
            "clients_processed": total_clients_processed,
            "total_issues_created": total_issues_created,
            "successful_clients": sum(1 for r in results["client_results"] if r.get("status") == "success"),
            "failed_clients": sum(1 for r in results["client_results"] if r.get("status") == "error"),
            "validate_only": validate_only
        }
        
        results["end_time"] = datetime.now().isoformat()
        return results
        
    except Exception as e:
        logger.error(f"Bulk issue creation per client flow failed: {str(e)}")
        results["error"] = str(e)
        results["end_time"] = datetime.now().isoformat()
        return results


@flow(name="bulk-create-jira-issues", description="Create Jira issues in bulk from JSON data, capped at 45 issues")
async def bulk_create_jira_issues_flow(
    json_file_path: Optional[str] = None,
    max_issues: int = 45,
    credentials_block_name: str = "jira-creds",
    validate_only: bool = False,
    timestamp: Optional[str] = None
):
    """
    Create Jira issues in bulk from previously generated JSON data.
    Reads Jira-formatted data and creates up to 45 issues per execution.
    
    Args:
        json_file_path: Path to JSON file with Jira issue data (if None, uses latest)
        max_issues: Maximum number of issues to create (default: 45)
        credentials_block_name: Name of the Jira credentials block
        validate_only: If True, only validate data without creating issues
        timestamp: Custom timestamp for output files
        
    Returns:
        Dict containing bulk creation results
    """
    try:
        from ..tasks.jira_tasks import (
            get_server_info,
            create_issues_bulk,
            read_jira_formatted_json,
            validate_bulk_issue_data
        )
    except ImportError:
        # For running as standalone script
        from tasks.jira_tasks import (
            get_server_info,
            create_issues_bulk,
            read_jira_formatted_json,
            validate_bulk_issue_data
        )
    
    results = {
        "start_time": datetime.now().isoformat(),
        "json_file_path": json_file_path,
        "max_issues": max_issues,
        "validate_only": validate_only,
        "summary": {}
    }
    
    try:
        # Determine JSON file path
        if not json_file_path:
            # Find the latest data directory and JSON file
            data_dirs = []
            for item in os.listdir(OUTPUT_DIR):
                item_path = os.path.join(OUTPUT_DIR, item)
                if os.path.isdir(item_path) and item.replace("_", "").isdigit():
                    data_dirs.append(item)
            
            if not data_dirs:
                results["error"] = "No data directories found in output directory"
                return results
            
            latest_dir = sorted(data_dirs)[-1]
            json_file_path = os.path.join(OUTPUT_DIR, latest_dir, "content_plan_jira_asset_issue.json")
            results["latest_directory"] = latest_dir
        
        if not os.path.exists(json_file_path):
            results["error"] = f"JSON file not found: {json_file_path}"
            return results
        
        results["json_file_path"] = json_file_path
        
        # Step 1: Test Jira connection (skip if validation only)
        if not validate_only:
            jira_test = await get_server_info(credentials_block_name)
            if jira_test["status"] != "success":
                results["error"] = f"Jira connection failed: {jira_test.get('error')}"
                return results
            results["jira_connection"] = jira_test
        
        # Step 2: Read and parse JSON data
        json_data = await read_jira_formatted_json(json_file_path)
        if json_data["status"] != "success":
            results["error"] = f"Failed to read JSON data: {json_data.get('error')}"
            return results
        
        results["json_metadata"] = json_data["metadata"]
        results["total_issues_in_file"] = json_data["total_issues"]
        
        # Step 3: Validate issue data
        validation_result = await validate_bulk_issue_data(
            issue_updates=json_data["issue_updates"],
            max_issues=max_issues
        )
        
        if validation_result["status"] != "success":
            results["error"] = f"Validation failed: {validation_result.get('error')}"
            return results
        
        results["validation"] = validation_result
        
        # If validation only, return here
        if validate_only:
            results["end_time"] = datetime.now().isoformat()
            results["summary"] = {
                "mode": "validation_only",
                "total_issues_in_file": json_data["total_issues"],
                "valid_issues": validation_result["final_count"],
                "invalid_issues": validation_result["invalid_count"],
                "warnings": validation_result["warnings"]
            }
            return results
        
        # Step 4: Create issues in bulk
        if validation_result["final_count"] == 0:
            results["error"] = "No valid issues found for creation"
            return results
        
        bulk_result = await create_issues_bulk(
            issue_updates=validation_result["valid_issues"],
            credentials_block_name=credentials_block_name,
            max_issues=max_issues
        )
        
        results["bulk_creation"] = bulk_result
        
        # Step 5: Save results to file
        if not timestamp:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        run_output_dir = os.path.join(OUTPUT_DIR, timestamp)
        os.makedirs(run_output_dir, exist_ok=True)
        
        output_data = {
            "metadata": {
                "workflow": "bulk-create-jira-issues",
                "executed_at": datetime.now().isoformat() + "Z",
                "source_file": json_file_path,
                "max_issues_limit": max_issues,
                "validate_only": validate_only
            },
            "results": results
        }
        
        output_path = os.path.join(run_output_dir, "bulk_issue_creation_results.json")
        saved_path = save_to_json(output_data, output_path)
        
        results["output_file"] = saved_path
        results["end_time"] = datetime.now().isoformat()
        
        # Create summary
        if bulk_result["status"] == "success":
            results["summary"] = {
                "mode": "creation",
                "status": "success",
                "total_issues_requested": validation_result["final_count"],
                "total_issues_created": bulk_result["total_created"],
                "total_errors": bulk_result["total_errors"],
                "validation_warnings": validation_result["warnings"],
                "output_file": saved_path
            }
        else:
            results["summary"] = {
                "mode": "creation", 
                "status": "error",
                "error": bulk_result.get("error"),
                "total_issues_requested": validation_result["final_count"],
                "validation_warnings": validation_result["warnings"],
                "output_file": saved_path
            }
        
        return results
        
    except Exception as e:
        logger.error(f"Bulk issue creation flow failed: {str(e)}")
        results["error"] = str(e)
        results["end_time"] = datetime.now().isoformat()
        return results


if __name__ == "__main__":
    import asyncio
    
    async def main():
        """
        Complete Content Plan to Jira Issues Workflow
        Numbered steps for clear execution tracking
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create run-specific output directory
        run_output_dir = os.path.join(OUTPUT_DIR, timestamp)
        os.makedirs(run_output_dir, exist_ok=True)
        
        # Step 1: Read client data from main spreadsheet
        step1_result = await read_content_plan_flow()
        
        if "error" not in step1_result:
            output_path1 = os.path.join(run_output_dir, "step1_client_data.json")
            save_to_json(step1_result, output_path1)
        else:
            return
        
        # Step 2: Search for content plan files in client folders
        step2_result = await search_content_plan_files_flow(
            target_month="September 2025"
        )
        
        if "error" not in step2_result:
            output_path2 = os.path.join(run_output_dir, "step2_content_plan_search.json")
            save_to_json(step2_result, output_path2)
        else:
            return
        
        # Step 3: Filter results for all clients
        step3_result = await filter_content_plan_results_flow(
            target_month="September 2025"
        )
        
        if "error" not in step3_result:
            output_path3 = os.path.join(run_output_dir, "step3_filtered_results.json")
            save_to_json(step3_result, output_path3)
        else:
            return
        
        # Step 4: Read content plan data with delays
        step4_result = await read_content_plan_data_flow(
            target_month="September 2025",
            min_delay_seconds=2,
            max_delay_seconds=4
        )
        
        if "error" not in step4_result:
            output_path4 = os.path.join(run_output_dir, "step4_content_plan_data.json")
            save_to_json(step4_result, output_path4)
        else:
            return
        
        # Step 5: Format data uniformly
        step5_result = await format_data_processor_flow(
            max_rows=3,
            output_filename="step5_formatted_data.json",
            timestamp=timestamp
        )
        
        # Step 6: Convert content plan to Jira assets
        step6_result = await convert_content_plan_to_jira_assets_flow(
            target_month="September 2025",
            timestamp=timestamp
        )
        
        if "error" not in step6_result:
            output_path6 = os.path.join(run_output_dir, "step6_jira_assets.json")
            save_to_json(step6_result, output_path6)
        else:
            return
        
        # Step 7: Validate Jira issues per client (dry run)
        if "error" not in step6_result and "output_files" in step6_result:
            client_files = step6_result["output_files"]["client_files"]
            
            step7_result = await bulk_create_jira_issues_per_client_flow(
                client_files=client_files,
                max_issues=45,
                validate_only=True,
                timestamp=timestamp
            )
            
            if "error" not in step7_result:
                output_path7 = os.path.join(run_output_dir, "step7_validation_per_client.json")
                save_to_json(step7_result, output_path7)
            
            # Step 8: Create Jira issues in bulk per client (production)
            step8_result = await bulk_create_jira_issues_per_client_flow(
                client_files=client_files,
                max_issues=45,
                validate_only=False,
                timestamp=timestamp
            )
            
            if "error" not in step8_result:
                output_path8 = os.path.join(run_output_dir, "step8_bulk_creation_per_client.json")
                save_to_json(step8_result, output_path8)
    
    asyncio.run(main())