"""
Utility Tasks for Prefect workflows

This module contains reusable utility tasks for various operations.
"""
import logging
import random
import re
import json
import os
import asyncio
from collections import deque
from datetime import datetime, timedelta
from typing import Literal, Optional, Dict, Any
from prefect import task
from prefect.logging import get_run_logger
from hashmap import WORKERS, FIELD_ASSOCIATE, CONTENT_EDITOR, COMPONENTS

# Jira rejects an ADF text node that carries a newline or is empty, which is exactly
# what a multi-line or unfilled spreadsheet cell produces. Every value that reaches
# a Jira payload goes through this module first -- see tasks/jira_adf.py.
try:
    from . import jira_adf as adf
except ImportError:  # standalone execution
    import jira_adf as adf

logger = logging.getLogger(__name__)


async def randomized_item_delay(is_last: bool, min_seconds: float = 5.0, max_seconds: float = 10.0) -> float:
    """
    Sleep a random 5-10s interval between item requests (FR-013), omitted after
    the last item in a sequence (constitution II).

    Args:
        is_last: True if this is the last item in the current sequence (no delay applied)
        min_seconds: Lower bound of the randomized delay
        max_seconds: Upper bound of the randomized delay

    Returns:
        Seconds actually slept (0.0 if omitted)
    """
    if is_last:
        return 0.0
    delay = random.uniform(min_seconds, max_seconds)
    await asyncio.sleep(delay)
    return delay


class RateWindow:
    """
    Rolling-window limiter enforcing at most `limit` collected items within any
    `window_seconds` window (FR-011: <=100 items/hour). In-memory only — a run
    is a single flow execution, so no cross-process state is needed.
    """

    def __init__(self, limit: int = 100, window_seconds: float = 3600.0):
        self.limit = limit
        self.window_seconds = window_seconds
        self._timestamps: "deque[float]" = deque()

    def _evict_expired(self, now: float) -> None:
        while self._timestamps and now - self._timestamps[0] >= self.window_seconds:
            self._timestamps.popleft()

    def seconds_until_slot(self, now: Optional[float] = None) -> float:
        """Seconds to wait before the window has room for one more item (0.0 if room now)."""
        now = now if now is not None else datetime.now().timestamp()
        self._evict_expired(now)
        if len(self._timestamps) < self.limit:
            return 0.0
        return max(0.0, self.window_seconds - (now - self._timestamps[0]))

    def record(self, now: Optional[float] = None) -> None:
        """Record one collected item at `now` (defaults to current time)."""
        now = now if now is not None else datetime.now().timestamp()
        self._evict_expired(now)
        self._timestamps.append(now)

    async def wait_for_slot(self) -> float:
        """Sleep until the window has room, then return the seconds waited."""
        wait_seconds = self.seconds_until_slot()
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        return wait_seconds


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
    Convert a content plan row to Jira issue type 10009 (Content) format

    Args:
        row: Content plan row data
        client_name: Client name for component mapping
        component_hashmap: Override for the client -> component ID mapping;
            defaults to the sheet-backed COMPONENTS hashmap

    Returns:
        Formatted Jira issue data for type 10009
    """
    logger = get_run_logger()

    # Falls back to the "Hashmaps" worksheet (hashmap.py), resolved on first access
    if component_hashmap is None:
        component_hashmap = COMPONENTS
    
    try:
        # Extract summary from "Topik" column. A summary is a plain string, so a
        # multi-line or over-long Topik is rejected by Jira, not truncated by it.
        summary = adf.clean_summary(row.get("Topik", ""))
        if not summary:
            logger.warning(f"No 'Topik' column found in row: {row}")
            summary = "Content"
        
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
        
        # Reporter: the company account that reports every issue. Named "Noktah" in the Hub since the
        # 2026-09-26 merge; the old name is kept until the WORKERS copy has the new one.
        reporter_id = WORKERS.get("Noktah") or WORKERS.get("Noktah Inovasi Teknologi", "")
        
        # Get Content Type from "Bentuk" column
        content_type = row.get("Bentuk", "")
        
        # Description body. Every value is routed through the ADF builders: they
        # turn newlines into hardBreak nodes and drop empty text nodes, the two
        # things Jira rejects the whole issue over (see tasks/jira_adf.py).
        # labelled_block is for the multi-line fields -- a bold label on its own
        # line, then the value as its own paragraphs, matching the previous layout.
        description_blocks = [
            adf.labelled_paragraph(
                "Tanggal dan Waktu",
                f"{row.get('Tanggal', '')} {row.get('Waktu', '')}",
            ),
            adf.labelled_paragraph("Bentuk", content_type),
            adf.labelled_paragraph("Creator", row.get("Creator", "")),
            adf.labelled_paragraph("Format", row.get("Format", "")),
            adf.labelled_paragraph("Purpose/Theme", row.get("Purpose/Theme", "")),
            adf.labelled_paragraph("Strategic Application", row.get("Strategic Application", "")),
            adf.labelled_paragraph("Kebutuhan Personil", row.get("Kebutuhan Personil", "")),
            # "Shoot Guide" and "Visualisasi Konten" are two separate columns on
            # the live sheet (Shoot Guide sits at column L, Visualisasi Konten at
            # M), not one column under two names. They used to be merged with
            # `or`, which silently dropped Visualisasi Konten whenever Shoot
            # Guide was filled -- 60 rows across the saved runs -- and mislabelled
            # it as "Shoot Guide" on the 2,679 rows where only it was filled.
            # Emit each under its own label; labelled_block yields just the
            # label when a column is empty or absent (pre-rename sheets).
            *adf.labelled_block("Shoot Guide", row.get("Shoot Guide", "")),
            *adf.labelled_block("Visualisasi Konten", row.get("Visualisasi Konten", "")),
            *adf.labelled_block("Reference", row.get("Reference", "")),
            # "Asset" here is the content-plan COLUMN (the produced asset's
            # link), not the Jira issue type -- that was renamed to "Content"
            # and is selected by id 10009 below. Renaming this string would
            # silently read an absent column and blank the field.
            *adf.labelled_block("Asset", row.get("Asset", "")),
            *adf.labelled_block("Caption", row.get("Caption", "")),
            adf.labelled_paragraph("Approval", row.get("Approval", "")),
            adf.labelled_paragraph("Link Referensi", row.get("Link Referensi", "")),
            # Placeholders the assignee fills in on the ticket itself.
            adf.labelled_paragraph("Revisi", ""),
            adf.labelled_paragraph("Link Contoh Footage", ""),
            adf.labelled_paragraph("PIC", ""),
        ]

        # Build Jira issue structure for type 10009
        jira_issue = {
            "fields": {
                "project": {
                    "key": "ESKL"
                },
                "summary": summary,  # already cleaned + length-capped by adf.clean_summary
                "issuetype": {
                    "id": "10009"
                },
                "components": [
                    {
                        "id": component_id
                    }
                ] if component_id else [],
                "description": adf.document(description_blocks),
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
        
        # Final pass over the assembled payload: catches any raw sheet value that
        # reaches a Jira field without going through the builders above (a newline
        # in "Bentuk" would be rejected the same way one in the description is).
        jira_issue = adf.sanitize_issue(jira_issue)

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