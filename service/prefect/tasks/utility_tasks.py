"""
Utility Tasks for Prefect workflows

This module contains reusable utility tasks for various operations.
"""
import logging
import re
import json
import os
import asyncio
from datetime import datetime, timedelta
from typing import Literal, Optional, Dict, List, Any
from prefect import task
from prefect.logging import get_run_logger
from hashmap import WORKERS, FIELD_ASSOCIATE, CONTENT_EDITOR

logger = logging.getLogger(__name__)


@task(name="wait-seconds")
async def wait_seconds(seconds: int) -> Dict[str, Any]:
    """
    Wait for a specified number of seconds.
    
    Args:
        seconds: Number of seconds to wait
        
    Returns:
        Dictionary with wait information
    """
    logger = get_run_logger()
    
    if seconds < 0:
        raise ValueError("Wait seconds must be non-negative")
    
    logger.info(f"Waiting for {seconds} seconds...")
    start_time = datetime.now()
    
    await asyncio.sleep(seconds)
    
    end_time = datetime.now()
    actual_wait_time = (end_time - start_time).total_seconds()
    
    result = {
        "requested_seconds": seconds,
        "actual_wait_seconds": actual_wait_time,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "status": "completed"
    }
    
    logger.info(f"Wait completed. Actual wait time: {actual_wait_time:.2f} seconds")
    return result


@task(name="get-date")
async def get_date(
    date_input: Optional[str] = None,
    format_type: Literal["complete", "month_year", "year"] = "month_year",
    offset_months: int = 1,
    language: Literal["indonesian", "english"] = "indonesian"
) -> str:
    """
    Get date in various formats with flexible input options.
    
    Args:
        date_input: Optional specific date in Indonesian format (e.g., "September 2025", "Januari 2024")
                   If None, uses current date with offset_months
        format_type: Output format type:
                    - "complete": Full date (e.g., "12 September 2025")
                    - "month_year": Month and year only (e.g., "September 2025")
                    - "year": Year only (e.g., "2025")
        offset_months: Months to add/subtract from current date (ignored if date_input provided)
        language: Output language ("indonesian" or "english")
    
    Returns:
        Formatted date string
    """
    # Language mappings
    indonesian_months = {
        1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
        5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
        9: "September", 10: "Oktober", 11: "November", 12: "Desember"
    }
    
    english_months = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December"
    }
    
    # Reverse mapping for parsing Indonesian input
    indonesian_month_reverse = {v: k for k, v in indonesian_months.items()}
    english_month_reverse = {v: k for k, v in english_months.items()}
    
    try:
        if date_input:
            # Parse date_input (e.g., "September 2025", "Januari 2024")
            parts = date_input.strip().split()
            if len(parts) == 2:
                month_name, year_str = parts
                year = int(year_str)
                
                # Try Indonesian month names first, then English
                if month_name in indonesian_month_reverse:
                    month = indonesian_month_reverse[month_name]
                elif month_name in english_month_reverse:
                    month = english_month_reverse[month_name]
                else:
                    raise ValueError(f"Unknown month name: {month_name}")
                
                # Create datetime object for the 1st of the month
                target_date = datetime(year, month, 1)
            else:
                raise ValueError(f"Invalid date format: {date_input}. Expected format: 'Month YYYY'")
        else:
            # Use current date with offset
            now = datetime.now()
            # Calculate target month/year with offset
            target_year = now.year
            target_month = now.month + offset_months
            
            # Handle month overflow/underflow
            while target_month > 12:
                target_month -= 12
                target_year += 1
            while target_month < 1:
                target_month += 12
                target_year -= 1
            
            target_date = datetime(target_year, target_month, now.day)
        
        # Choose language mapping
        month_names = indonesian_months if language == "indonesian" else english_months
        
        # Format output based on format_type
        if format_type == "complete":
            month_name = month_names[target_date.month]
            return f"{target_date.day} {month_name} {target_date.year}"
        elif format_type == "month_year":
            month_name = month_names[target_date.month]
            return f"{month_name} {target_date.year}"
        elif format_type == "year":
            return str(target_date.year)
        else:
            raise ValueError(f"Invalid format_type: {format_type}")
            
    except Exception as e:
        logger.error(f"Failed to process date: {str(e)}")
        raise


@task(name="get-current-month-indonesian")
async def get_current_month_indonesian() -> str:
    """
    Get current month in Indonesian format.
    
    Returns:
        Current month name in Indonesian with year (e.g., "Agustus 2025")
    """
    return await get_date(offset_months=0, language="indonesian")


@task(name="get-next-month-indonesian")
async def get_next_month_indonesian() -> str:
    """
    Get next month in Indonesian format.
    
    Returns:
        Next month name in Indonesian with year (e.g., "September 2025")
    """
    return await get_date(offset_months=1, language="indonesian")


@task(name="format-date-indonesian")
async def format_date_indonesian(date_str: str, format_input: str = "%Y-%m-%d") -> str:
    """
    Format a date string to Indonesian month format.
    
    Args:
        date_str: Date string to format
        format_input: Input date format (default: "%Y-%m-%d")
    
    Returns:
        Formatted date with Indonesian month name
    """
    # Indonesian month names
    indonesian_months = {
        1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
        5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
        9: "September", 10: "Oktober", 11: "November", 12: "Desember"
    }
    
    try:
        # Parse the date string
        date_obj = datetime.strptime(date_str, format_input)
        
        # Return Indonesian month name with year
        month_name = indonesian_months[date_obj.month]
        return f"{month_name} {date_obj.year}"
        
    except ValueError as e:
        logger.error(f"Failed to parse date '{date_str}' with format '{format_input}': {str(e)}")
        raise


@task(name="format-date-for-jira")
def format_date_for_jira(date_value: Any) -> str:
    """
    Format date values to Jira date format (YYYY-MM-DD)
    
    Args:
        date_value: Date value to format (string, datetime, or other)
        
    Returns:
        Formatted date string in Jira date format (YYYY-MM-DD)
    """
    if date_value is None or date_value == "":
        return ""
    
    try:
        if isinstance(date_value, datetime):
            return date_value.strftime("%Y-%m-%d")
        
        if isinstance(date_value, str):
            date_formats = [
                "%Y-%m-%d",
                "%d/%m/%Y", 
                "%m/%d/%Y",
                "%d-%m-%Y",
                "%Y/%m/%d",
                "%d/%m/%y",
                "%m/%d/%y",
                "%d-%m-%y"
            ]
            
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(date_value.strip(), fmt)
                    return parsed_date.strftime("%Y-%m-%d")
                except ValueError:
                    continue
        
        if isinstance(date_value, (int, float)):
            parsed_date = datetime.fromtimestamp(date_value)
            return parsed_date.strftime("%Y-%m-%d")
            
    except Exception as e:
        logger.warning(f"Could not parse date value '{date_value}': {str(e)}")
    
    return str(date_value)


@task(name="format-date-time-iso")
def format_date_time_iso(date_value: Any) -> str:
    """
    Format date/time values to ISO 8601 international standard
    
    Args:
        date_value: Date value to format (string, datetime, or other)
        
    Returns:
        Formatted date string in ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ)
    """
    if date_value is None or date_value == "":
        return ""
    
    try:
        if isinstance(date_value, datetime):
            return date_value.isoformat() + "Z"
        
        if isinstance(date_value, str):
            date_formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%d/%m/%Y %H:%M:%S", 
                "%d/%m/%Y",
                "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y",
                "%d-%m-%Y %H:%M:%S",
                "%d-%m-%Y",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d"
            ]
            
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(date_value.strip(), fmt)
                    return parsed_date.isoformat() + "Z"
                except ValueError:
                    continue
        
        if isinstance(date_value, (int, float)):
            parsed_date = datetime.fromtimestamp(date_value)
            return parsed_date.isoformat() + "Z"
            
    except Exception as e:
        logger.warning(f"Could not parse date value '{date_value}': {str(e)}")
    
    return str(date_value)


@task(name="format-text-field-uniform")
def format_text_field_uniform(text_value: Any) -> str:
    """
    Format text fields with consistent spacing, newlines, and tabs
    
    Args:
        text_value: Text value to format
        
    Returns:
        Formatted text string with consistent spacing
    """
    if text_value is None:
        return ""
    
    text = str(text_value)
    text = text.strip()
    
    # Normalize line breaks
    text = text.replace('\r\n', '\n')
    text = text.replace('\r', '\n')
    
    # Normalize spacing
    text = re.sub(r' +', ' ', text)  # Multiple spaces to single space
    text = re.sub(r'\t+', '\t', text)  # Multiple tabs to single tab
    text = re.sub(r'\n{3,}', '\n\n', text)  # Max 2 consecutive newlines
    
    return text


@task(name="format-numeric-field-uniform")
def format_numeric_field_uniform(numeric_value: Any) -> Optional[float]:
    """
    Format numeric fields consistently
    
    Args:
        numeric_value: Numeric value to format
        
    Returns:
        Formatted numeric value or None if not numeric
    """
    if numeric_value is None or numeric_value == "":
        return None
    
    try:
        if isinstance(numeric_value, str):
            # Remove common formatting characters
            cleaned = numeric_value.replace(',', '').replace('$', '').replace('€', '').replace('£', '').strip()
            if cleaned == "":
                return None
            return float(cleaned)
        
        if isinstance(numeric_value, (int, float)):
            return float(numeric_value)
            
    except (ValueError, TypeError):
        logger.warning(f"Could not parse numeric value '{numeric_value}'")
    
    return None


@task(name="process-row-uniform")
def process_row_uniform(row: Dict[str, Any], row_index: int) -> Dict[str, Any]:
    """
    Process a single row with uniform formatting according to international standards
    
    Args:
        row: Row data dictionary
        row_index: Index of the row for tracking
        
    Returns:
        Formatted row dictionary with standardized fields
    """
    processed_row = {
        "row_index": row_index,
        "processed_at": datetime.now().isoformat() + "Z",
        "original_data": row,
        "formatted_data": {}
    }
    
    # Process each field in the row
    for key, value in row.items():
        # Standardize field key (lowercase, underscores)
        field_key = key.strip().lower().replace(' ', '_').replace('-', '_')
        
        # Identify field type and apply appropriate formatting
        if any(date_keyword in key.lower() for date_keyword in ['date', 'time', 'created', 'updated', 'modified']):
            # Date/time field - format to ISO 8601
            processed_row["formatted_data"][field_key] = format_date_time_iso(value)
        elif any(num_keyword in key.lower() for num_keyword in ['amount', 'price', 'cost', 'value', 'number', 'count']):
            # Numeric field
            processed_row["formatted_data"][field_key] = format_numeric_field_uniform(value)
        elif 'id' in key.lower():
            # ID fields should remain as formatted text
            processed_row["formatted_data"][field_key] = format_text_field_uniform(value)
        else:
            # Text field
            processed_row["formatted_data"][field_key] = format_text_field_uniform(value)
    
    return processed_row


@task(name="convert-content-plan-row-to-jira-issue")
def convert_content_plan_row_to_jira_issue(
    row: Dict[str, Any], 
    client_name: str,
    component_hashmap: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Convert a content plan row to Jira issue type 10009 (Asset) format
    
    Args:
        row: Content plan row data
        client_name: Client name for component mapping
        component_hashmap: Mapping of client names to component IDs
        
    Returns:
        Formatted Jira issue data for type 10009
    """
    logger = get_run_logger()
    
    # Default component hashmap based on issue_type_10009_fields.json
    if component_hashmap is None:
        component_hashmap = {
            "Balakosa Rewind and Play": "10034",
            "Ecky Dental Center": "10000",
            "Gudang Karung Jumbo Sidoarjo": "10010",
            "Kabin Event Organizer": "10003",
            "Karsa Studio": "10099",
            "Klinik Mata Bireuen": "10101",
            "Klinik Mata Boyolali": "10008",
            "Klinik Mata Jogja": "10068",
            "Klinik Mata Sampang": "10006",
            "Klinik Spesialis Langsa": "10033",
            "Klinik Utama Gresik": "10007",
            "Klinik Utama Sumenep": "10005",
            "Lasik Asyik by SMEC Tebet": "10009",
            "Nirwana Coffee Space Pamekasan": "10002",
            "Nirwana Coffee Space Sumenep": "10001",
            "Pelita Delapan": "10100",
            "Qur'anic Integrated School of Muhammadiyah (QISMu)": "10004",
            "RS Mata SMEC Balikpapan": "10067",
            "Satoe Rock Steak": "10066"
        }
    
    try:
        # Extract summary from "Topik" column
        summary = row.get("Topik", "")
        if not summary:
            logger.warning(f"No 'Topik' column found in row: {row}")
            summary = "Content Asset"
        
        # Get component ID from hashmap
        component_id = component_hashmap.get(client_name)
        if not component_id:
            logger.warning(f"No component mapping found for client: {client_name}")
        
        # Parse publication date from "Tanggal" column
        publication_date_raw = row.get("Tanggal", "")
        publication_date = format_date_for_jira(publication_date_raw)
        
        # Calculate start date (publication date minus 7 days)
        start_date = ""
        if publication_date:
            try:
                pub_date_obj = datetime.strptime(publication_date, "%Y-%m-%d")
                start_date_obj = pub_date_obj - timedelta(days=7)
                start_date = start_date_obj.strftime("%Y-%m-%d")
            except ValueError:
                logger.warning(f"Could not calculate start date from publication date: {publication_date}")
        
        # Calculate due date (publication date minus 1 day)
        due_date = ""
        if publication_date:
            try:
                pub_date_obj = datetime.strptime(publication_date, "%Y-%m-%d")
                due_date_obj = pub_date_obj - timedelta(days=1)
                due_date = due_date_obj.strftime("%Y-%m-%d")
            except ValueError:
                logger.warning(f"Could not calculate due date from publication date: {publication_date}")
        
        # Get Field Associate
        field_associate_name = FIELD_ASSOCIATE.get(client_name, "")
        field_associate_id = WORKERS.get(field_associate_name, "") if field_associate_name else ""
        
        # Get Content Editor
        content_editor_name = CONTENT_EDITOR.get(client_name, "")
        content_editor_id = WORKERS.get(content_editor_name, "") if content_editor_name else ""
        
        # Get Reporter (Noktah Inovasi Teknologi)
        reporter_id = WORKERS.get("Noktah Inovasi Teknologi", "")
        
        # Get Content Type from "Bentuk" column
        content_type = row.get("Bentuk", "")
        
        # Build Jira issue structure for type 10009
        jira_issue = {
            "fields": {
                "project": {
                    "key": "ESKL"
                },
                "summary": str(summary).strip(),
                "issuetype": {
                    "id": "10009"
                },
                "components": [
                    {
                        "id": component_id
                    }
                ] if component_id else [],
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Tanggal dan Waktu: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": f"{row.get('Tanggal', '')} {row.get('Waktu', '')}"}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Bentuk: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": str(content_type)}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Creator: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": str(row.get('Creator', ''))}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Format: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": str(row.get('Format', ''))}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Purpose/Theme: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": str(row.get('Purpose/Theme', ''))}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Strategic Application: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": str(row.get('Strategic Application', ''))}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Kebutuhan Personil: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": str(row.get('Kebutuhan Personil', ''))}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Visualisasi Konten: ", "marks": [{"type": "strong"}]},
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": format_text_field_uniform(row.get('Visualisasi Konten', ''))}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Caption: ", "marks": [{"type": "strong"}]},
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": format_text_field_uniform(row.get('Caption', ''))}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Approval: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": str(row.get('Approval', ''))}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Link Referensi: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": str(row.get('Link Referensi', ''))}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Revisi: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": ""}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Link Contoh Footage: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": ""}
                            ]
                        },
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "PIC: ", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": ""}
                            ]
                        }
                    ]
                },
                "customfield_10040": publication_date,  # Publication date
                "customfield_10041": None,  # Category - empty value
                "customfield_10042": {  # Field Associate
                    "accountId": field_associate_id
                } if field_associate_id else None,
                "reporter": {  # Reporter
                    "accountId": reporter_id
                } if reporter_id else None,
                "customfield_10043": {  # Content Editor
                    "accountId": content_editor_id
                } if content_editor_id else None,
                "priority": {
                    "id": "5"  # Lowest priority as default
                },
                "customfield_10001": None,  # Team - empty value
                "customfield_10015": start_date,  # Start date
                "customfield_10039": content_type,  # Content type
                "attachment": [],  # Attachment - empty value
                "duedate": due_date,  # Due date
                "assignee": {  # Assignee - same as Field Associate
                    "accountId": field_associate_id
                } if field_associate_id else None
            }
        }
        
        # Add metadata for tracking
        jira_issue["metadata"] = {
            "client_name": client_name,
            "component_id": component_id,
            "field_associate_name": field_associate_name,
            "content_editor_name": content_editor_name,
            "converted_at": datetime.now().isoformat() + "Z",
            "original_row": row
        }
        
        logger.info(f"Successfully converted row to Jira issue for client: {client_name}")
        return jira_issue
        
    except Exception as e:
        logger.error(f"Failed to convert row to Jira issue: {str(e)}")
        raise


@task(name="save-to-json")
def save_to_json(data: Dict[str, Any], output_path: str) -> str:
    """
    Save data to JSON file with proper formatting
    
    Args:
        data: Data to save
        output_path: Path to save the JSON file
        
    Returns:
        Path to the saved file
    """
    # Ensure directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Write JSON with proper formatting
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Data saved to JSON file: {output_path}")
    return output_path