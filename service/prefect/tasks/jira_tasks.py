"""
Jira API Tasks for Prefect workflows

This module contains reusable tasks organized by Jira API v3 resource groups:
- Server Info: Connection and server information
- Issues: Issue operations (create, get, update, search)
- Projects: Project management operations
- Issue Comments: Comment management
- Issue Types: Issue type operations

Follows Jira API v3 naming conventions from:
https://developer.atlassian.com/cloud/jira/platform/rest/v3/
"""
import logging
from typing import Dict, List, Any, Optional
from prefect import task
from prefect.logging import get_run_logger

try:
    from ..blocks.jira_credentials import JiraCredentials
    from . import jira_adf as adf
except ImportError:
    # For running as standalone script
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from blocks.jira_credentials import JiraCredentials
    from tasks import jira_adf as adf

logger = logging.getLogger(__name__)


# =============================================================================
# SERVER INFO API GROUP
# =============================================================================

@task(name="jira.server-info.get", retries=2, retry_delay_seconds=30)
async def get_server_info(credentials_block_name: str = "jira-creds") -> Dict[str, Any]:
    """
    Get Jira server information and test connection.
    
    Corresponds to GET /rest/api/3/serverInfo
    
    Args:
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        Dict containing server information and connection status
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        result = jira_creds.test_connection()
        
        if result["status"] != "success":
            logger.error(f"Jira connection failed: {result.get('error', 'Unknown error')}")
            
        return result
    except Exception as e:
        logger.error(f"Connection test failed: {str(e)}")
        return {"status": "error", "error": str(e)}


# =============================================================================
# PROJECTS API GROUP
# =============================================================================

@task(name="jira.projects.search")
async def search_projects(credentials_block_name: str = "jira-creds") -> List[Dict[str, Any]]:
    """
    Search for accessible Jira projects.
    
    Corresponds to GET /rest/api/3/project/search
    
    Args:
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        List of project metadata dictionaries
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        projects = client.get_projects()
        logger.info(f"Found {len(projects)} accessible Jira projects")
        return projects
        
    except Exception as e:
        logger.error(f"Failed to get Jira projects: {str(e)}")
        raise


# =============================================================================
# ISSUES API GROUP
# =============================================================================

@task(name="jira.issues.search")
async def search_issues(
    jql: str,
    credentials_block_name: str = "jira-creds",
    max_results: int = 50
) -> List[Dict[str, Any]]:
    """
    Search for issues using JQL (Jira Query Language).
    
    Corresponds to GET /rest/api/3/search
    
    Args:
        jql: JQL query string
        credentials_block_name: Name of the Jira credentials block
        max_results: Maximum number of issues to return
        
    Returns:
        List of issue dictionaries
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        issues = client.search_issues(jql, max_results)
        logger.info(f"Found {len(issues)} issues matching JQL: {jql}")
        return issues
        
    except Exception as e:
        logger.error(f"Failed to search Jira issues: {str(e)}")
        raise


@task(name="jira.issues.get")
async def get_issue(
    issue_key: str,
    credentials_block_name: str = "jira-creds"
) -> Dict[str, Any]:
    """
    Get a specific issue by its key or ID.
    
    Corresponds to GET /rest/api/3/issue/{issueIdOrKey}
    
    Args:
        issue_key: Jira issue key (e.g., 'PROJ-123')
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        Issue metadata dictionary
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        issue = client.get_issue(issue_key)
        logger.info(f"Retrieved Jira issue: {issue_key}")
        return issue
        
    except Exception as e:
        logger.error(f"Failed to get Jira issue {issue_key}: {str(e)}")
        raise


@task(name="jira.issues.create")
async def create_issue(
    project_key: str,
    summary: str,
    description: str = "",
    issue_type: str = "Task",
    credentials_block_name: str = "jira-creds"
) -> str:
    """
    Create a new issue.
    
    Corresponds to POST /rest/api/3/issue
    
    Args:
        project_key: Jira project key (e.g., 'PROJ')
        summary: Issue summary/title
        description: Issue description
        issue_type: Issue type (Task, Bug, Story, etc.)
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        Created issue key
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        issue_key = client.create_issue(project_key, summary, description, issue_type)
        logger.info(f"Created Jira issue: {issue_key}")
        return issue_key
        
    except Exception as e:
        logger.error(f"Failed to create Jira issue: {str(e)}")
        raise


@task(name="jira.issues.update")
async def update_issue(
    issue_key: str,
    fields: Dict[str, Any],
    credentials_block_name: str = "jira-creds"
) -> bool:
    """
    Update an issue with new field values.
    
    Corresponds to PUT /rest/api/3/issue/{issueIdOrKey}
    
    Args:
        issue_key: Jira issue key (e.g., 'PROJ-123')
        fields: Dictionary of field updates
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        True if update was successful
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        result = client.update_issue(issue_key, fields)
        logger.info(f"Updated Jira issue: {issue_key}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to update Jira issue {issue_key}: {str(e)}")
        raise


# =============================================================================
# ISSUE COMMENTS API GROUP
# =============================================================================

@task(name="jira.issue-comments.add")
async def add_comment(
    issue_key: str,
    comment: str,
    credentials_block_name: str = "jira-creds"
) -> bool:
    """
    Add a comment to an issue.
    
    Corresponds to POST /rest/api/3/issue/{issueIdOrKey}/comment
    
    Args:
        issue_key: Jira issue key (e.g., 'PROJ-123')
        comment: Comment text
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        True if comment was added successfully
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        result = client.add_comment(issue_key, comment)
        logger.info(f"Added comment to Jira issue: {issue_key}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to add comment to Jira issue {issue_key}: {str(e)}")
        raise


# =============================================================================
# ISSUE TYPES API GROUP
# =============================================================================

@task(name="jira.issue-types.get-all")
async def get_all_issue_types(credentials_block_name: str = "jira-creds") -> List[Dict[str, Any]]:
    """
    Get all issue types.
    
    Corresponds to GET /rest/api/3/issuetype
    
    Args:
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        List of issue type dictionaries
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        # Get issue types using the client method
        issue_types = client.jira.get_issue_types()
        logger.info(f"Retrieved {len(issue_types)} issue types")
        return issue_types
        
    except Exception as e:
        logger.error(f"Failed to get issue types: {str(e)}")
        raise


@task(name="jira.issue-types.get")
async def get_issue_type(
    issue_type_id: str,
    credentials_block_name: str = "jira-creds"
) -> Dict[str, Any]:
    """
    Get a specific issue type by ID.
    
    Corresponds to GET /rest/api/3/issuetype/{id}
    
    Args:
        issue_type_id: Issue type ID
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        Issue type dictionary
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        # Get all issue types and find the specific one
        issue_types = client.jira.get_issue_types()
        target_issue_type = None
        
        for issue_type in issue_types:
            if str(issue_type.get("id")) == str(issue_type_id):
                target_issue_type = issue_type
                break
        
        if target_issue_type:
            logger.info(f"Retrieved issue type {issue_type_id}")
            return target_issue_type
        else:
            raise ValueError(f"Issue type {issue_type_id} not found")
        
    except Exception as e:
        logger.error(f"Failed to get issue type {issue_type_id}: {str(e)}")
        raise


@task(name="jira.issue-types.get-fields")
async def get_issue_type_fields(
    issue_type_id: str,
    project_key: str,
    credentials_block_name: str = "jira-creds"
) -> Dict[str, Any]:
    """
    Get field information for a specific issue type in a project.
    
    Corresponds to GET /rest/api/3/issue/createmeta
    
    Args:
        issue_type_id: Issue type ID
        project_key: Project key or ID
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        Dictionary containing field information for the issue type
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        # Get create metadata for the project and issue type
        create_meta = client.jira.issue_createmeta(
            project=project_key,
            expand="projects.issuetypes.fields"
        )
        
        fields_info = {}
        
        # Extract field information from create metadata
        projects = create_meta.get("projects", [])
        for project in projects:
            if project.get("key") == project_key:
                issue_types = project.get("issuetypes", [])
                for issue_type in issue_types:
                    if str(issue_type.get("id")) == str(issue_type_id):
                        fields = issue_type.get("fields", {})
                        
                        # Process each field to extract useful information
                        for field_key, field_data in fields.items():
                            fields_info[field_key] = {
                                "name": field_data.get("name"),
                                "required": field_data.get("required", False),
                                "hasDefaultValue": field_data.get("hasDefaultValue", False),
                                "schema": field_data.get("schema", {}),
                                "operations": field_data.get("operations", []),
                                "allowedValues": field_data.get("allowedValues", []),
                                "autoCompleteUrl": field_data.get("autoCompleteUrl"),
                                "fieldId": field_data.get("fieldId"),
                                "key": field_key
                            }
                        break
                break
        
        logger.info(f"Retrieved {len(fields_info)} fields for issue type {issue_type_id} in project {project_key}")
        
        return {
            "issueTypeId": issue_type_id,
            "projectKey": project_key,
            "fieldsCount": len(fields_info),
            "fields": fields_info
        }
        
    except Exception as e:
        logger.error(f"Failed to get fields for issue type {issue_type_id}: {str(e)}")
        raise


@task(name="jira.issue-types.get-field-options")
async def get_issue_type_field_options(
    issue_type_id: str,
    project_key: str,
    field_key: str,
    credentials_block_name: str = "jira-creds"
) -> List[Dict[str, Any]]:
    """
    Get field options/values for a specific field in an issue type.
    
    Args:
        issue_type_id: Issue type ID
        project_key: Project key or ID
        field_key: Field key (e.g., 'priority', 'status')
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        List of field option dictionaries
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        # Get create metadata for the specific field
        create_meta = client.jira.issue_createmeta(
            project=project_key,
            expand="projects.issuetypes.fields"
        )
        
        field_options = []
        
        # Extract field options from create metadata
        projects = create_meta.get("projects", [])
        for project in projects:
            if project.get("key") == project_key:
                issue_types = project.get("issuetypes", [])
                for issue_type in issue_types:
                    if str(issue_type.get("id")) == str(issue_type_id):
                        fields = issue_type.get("fields", {})
                        if field_key in fields:
                            field_data = fields[field_key]
                            allowed_values = field_data.get("allowedValues", [])
                            
                            for value in allowed_values:
                                field_options.append({
                                    "id": value.get("id"),
                                    "name": value.get("name"),
                                    "value": value.get("value"),
                                    "description": value.get("description"),
                                    "iconUrl": value.get("iconUrl"),
                                    "self": value.get("self")
                                })
                        break
                break
        
        logger.info(f"Retrieved {len(field_options)} options for field {field_key}")
        
        return field_options
        
    except Exception as e:
        logger.error(f"Failed to get field options for {field_key}: {str(e)}")
        raise


# =============================================================================
# PROJECT COMPONENTS API GROUP  
# =============================================================================

@task(name="jira.project-components.get")
async def get_project_components(
    project_key: str,
    credentials_block_name: str = "jira-creds"
) -> List[Dict[str, Any]]:
    """
    Get components for a specific project.
    
    Corresponds to GET /rest/api/3/project/{projectIdOrKey}/components
    
    Args:
        project_key: Project key or ID
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        List of project component dictionaries
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        # Get project components
        components = client.jira.get_project_components(project_key)
        logger.info(f"Retrieved {len(components)} components for project {project_key}")
        return components
        
    except Exception as e:
        logger.error(f"Failed to get components for project {project_key}: {str(e)}")
        raise


# =============================================================================
# ISSUE BULK OPERATIONS API GROUP
# =============================================================================

@task(name="jira.issue-bulk.create", retries=2, retry_delay_seconds=30)
async def create_issues_bulk(
    issue_updates: List[Dict[str, Any]],
    credentials_block_name: str = "jira-creds",
    max_issues: int = 45
) -> Dict[str, Any]:
    """
    Create multiple issues in bulk using the Jira REST API v3.
    
    Corresponds to POST /rest/api/3/issue/bulk
    
    Args:
        issue_updates: List of issue update objects with 'fields' property
        credentials_block_name: Name of the Jira credentials block
        max_issues: Maximum number of issues to create in one batch (default: 45)
        
    Returns:
        Dict containing created issues information and any errors
    """
    logger = get_run_logger()
    
    try:
        # Limit the number of issues to prevent API overload
        if len(issue_updates) > max_issues:
            logger.warning(f"Limiting issue creation from {len(issue_updates)} to {max_issues} issues")
            issue_updates = issue_updates[:max_issues]
        
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()

        # Last line of defence before the payload leaves the process. Idempotent,
        # so an already-clean issue is unchanged; an unsanitised one loses the
        # newline / empty text node Jira would reject the whole issue over.
        issue_updates = [adf.sanitize_issue(issue) for issue in issue_updates]

        # Prepare bulk create payload
        bulk_payload = {
            "issueUpdates": issue_updates
        }
        
        # Execute bulk create request using raw API call
        import requests
        import json
        
        # Get auth headers from client
        auth_header = client._auth_header if hasattr(client, '_auth_header') else None
        if not auth_header:
            # Fallback to basic auth using correct attribute names
            import base64
            auth_string = f"{client.jira_username}:{client.jira_token}"
            auth_bytes = auth_string.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            auth_header = f"Basic {auth_b64}"
        
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # Make the bulk create request
        url = f"{client.jira_url}/rest/api/3/issue/bulk"
        response = requests.post(
            url=url,
            headers=headers,
            data=json.dumps(bulk_payload),
            timeout=120  # 2 minute timeout for bulk operations
        )
        
        if response.status_code == 201:
            result_data = response.json()
            created_issues = result_data.get("issues", [])
            errors = result_data.get("errors", [])
            
            logger.info(f"Successfully created {len(created_issues)} issues in bulk")
            if errors:
                # Log what Jira actually objected to, per issue. Without this a
                # rejection is only ever a count, and the row that caused it has
                # to be guessed at.
                logger.warning(f"Encountered {len(errors)} errors during bulk creation")
                for error in errors:
                    failed_index = error.get("failedElementNumber")
                    summary = None
                    if isinstance(failed_index, int) and failed_index < len(issue_updates):
                        summary = (issue_updates[failed_index].get("fields") or {}).get("summary")
                    logger.warning(
                        f"Jira rejected issue #{failed_index} ({summary!r}): "
                        f"{json.dumps(error.get('elementErrors', error))}"
                    )

            return {
                "status": "success",
                "created_issues": created_issues,
                "errors": errors,
                "total_requested": len(issue_updates),
                "total_created": len(created_issues),
                "total_errors": len(errors)
            }
        else:
            error_msg = f"Bulk issue creation failed with status {response.status_code}"
            logger.error(f"{error_msg}: {response.text}")
            return {
                "status": "error",
                "error": error_msg,
                "response_text": response.text,
                "status_code": response.status_code
            }
        
    except Exception as e:
        logger.error(f"Failed to create issues in bulk: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
            "total_requested": len(issue_updates) if issue_updates else 0
        }


@task(name="jira.issue-bulk.read-json-data")
async def read_jira_formatted_json(
    json_file_path: str
) -> Dict[str, Any]:
    """
    Read and parse Jira-formatted JSON data from a file.
    
    Args:
        json_file_path: Path to the JSON file containing Jira issue data
        
    Returns:
        Dict containing parsed JSON data
    """
    logger = get_run_logger()
    
    try:
        import json
        import os
        
        if not os.path.exists(json_file_path):
            raise FileNotFoundError(f"JSON file not found: {json_file_path}")
        
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        logger.info(f"Successfully loaded JSON data from {json_file_path}")
        
        # Extract issue updates from the data structure
        issue_updates = []
        
        # Handle the flat format (issue_updates) and the nested combined-file
        # format. The nested key was renamed jira_assets -> jira_content (and
        # assets -> content) when Jira issue type 10009 was renamed "Asset" ->
        # "Content"; the old spellings are still read because the run-output
        # files already written to data/ use them.
        if "issue_updates" in data:
            # Flat format: direct issue_updates array
            issue_updates = data["issue_updates"]
        else:
            for outer, inner in (("jira_content", "content"), ("jira_assets", "assets")):
                if outer not in data:
                    continue
                for client_data in data[outer]:
                    for issue in client_data.get(inner, []):
                        issue_updates.append(issue)
                break
        
        return {
            "status": "success",
            "file_path": json_file_path,
            "metadata": data.get("metadata", {}),
            "issue_updates": issue_updates,
            "total_issues": len(issue_updates)
        }
        
    except Exception as e:
        logger.error(f"Failed to read JSON file {json_file_path}: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
            "file_path": json_file_path
        }


@task(name="jira.issue-bulk.validate-issue-data")
async def validate_bulk_issue_data(
    issue_updates: List[Dict[str, Any]],
    max_issues: int = 45
) -> Dict[str, Any]:
    """
    Validate issue data before bulk creation.
    
    Args:
        issue_updates: List of issue update objects
        max_issues: Maximum number of issues allowed
        
    Returns:
        Dict containing validation results and filtered data
    """
    logger = get_run_logger()
    
    validation_result = {
        "status": "success",
        "original_count": len(issue_updates),
        "valid_issues": [],
        "invalid_issues": [],
        "formatting_repairs": [],
        "rejections_prevented": [],
        "warnings": []
    }

    try:
        for index, issue_update in enumerate(issue_updates):
            issue_valid = True
            issue_errors = []

            # Repair formatting BEFORE validating -- this is the last place it can
            # be fixed rather than lost. Also covers payloads written to disk by an
            # earlier run, before the converter sanitised its own output.
            #
            # Two classes, kept apart on purpose. FATAL is what Jira actually
            # rejects the issue over (measured: a newline in the summary caused
            # 10 of 10 losses across 21 production runs). Everything else is a
            # rendering repair that Jira would have accepted as-is -- and it
            # applies to nearly every row, so merging the two would bury the
            # class that costs content.
            fatal_problems = adf.find_fatal_problems(issue_update)
            all_problems = adf.find_issue_problems(issue_update)
            if all_problems:
                issue_update = adf.sanitize_issue(issue_update)
                record = {
                    "index": index,
                    "summary": (issue_update.get("fields") or {}).get("summary"),
                    "problems": all_problems
                }
                validation_result["formatting_repairs"].append(record)
                if fatal_problems:
                    validation_result["rejections_prevented"].append({
                        **record, "problems": fatal_problems
                    })

            # Check for required fields structure
            if "fields" not in issue_update:
                issue_errors.append("Missing 'fields' property")
                issue_valid = False
            else:
                fields = issue_update["fields"]
                
                # Check for required fields
                required_fields = ["project", "summary", "issuetype"]
                for field in required_fields:
                    if field not in fields:
                        issue_errors.append(f"Missing required field: {field}")
                        issue_valid = False
                
                # Validate project structure
                if "project" in fields:
                    project = fields["project"]
                    if not isinstance(project, dict) or "key" not in project:
                        issue_errors.append("Project must have 'key' property")
                        issue_valid = False
                
                # Validate issuetype structure
                if "issuetype" in fields:
                    issuetype = fields["issuetype"]
                    if not isinstance(issuetype, dict) or "id" not in issuetype:
                        issue_errors.append("Issuetype must have 'id' property")
                        issue_valid = False
                
                # Validate summary is not empty
                if "summary" in fields and not str(fields["summary"] or "").strip():
                    issue_errors.append("Summary cannot be empty")
                    issue_valid = False
            
            if issue_valid:
                validation_result["valid_issues"].append(issue_update)
            else:
                validation_result["invalid_issues"].append({
                    "index": index,
                    "issue": issue_update,
                    "errors": issue_errors
                })
        
        # Apply max issues limit
        if len(validation_result["valid_issues"]) > max_issues:
            validation_result["warnings"].append(
                f"Limiting from {len(validation_result['valid_issues'])} to {max_issues} issues"
            )
            validation_result["valid_issues"] = validation_result["valid_issues"][:max_issues]
        
        validation_result["final_count"] = len(validation_result["valid_issues"])
        validation_result["invalid_count"] = len(validation_result["invalid_issues"])
        validation_result["repaired_count"] = len(validation_result["formatting_repairs"])
        validation_result["rejections_prevented_count"] = len(validation_result["rejections_prevented"])

        if validation_result["invalid_count"] > 0:
            logger.warning(f"Found {validation_result['invalid_count']} invalid issues")

        # The loud one: without the repair, each of these rows would have been
        # lost, and Jira's only account of it is a count in the bulk response.
        if validation_result["rejections_prevented_count"] > 0:
            logger.warning(
                f"Prevented {validation_result['rejections_prevented_count']} Jira rejection(s): "
                + "; ".join(
                    f"[{r['index']}] {r['summary']!r}: {', '.join(r['problems'])}"
                    for r in validation_result["rejections_prevented"][:5]
                )
            )

        # The quiet one: accepted by Jira either way, cleaned for rendering.
        if validation_result["repaired_count"] > 0:
            logger.info(
                f"Cleaned rendering formatting on {validation_result['repaired_count']} issue(s)"
            )

        logger.info(f"Validated {validation_result['final_count']} valid issues for bulk creation")
        
        return validation_result
        
    except Exception as e:
        logger.error(f"Failed to validate issue data: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
            "original_count": len(issue_updates)
        }


# =============================================================================
# WORKFLOWS API GROUP
# =============================================================================

@task(name="jira.workflows.get-transitions")
async def get_issue_transitions(
    issue_key: str,
    credentials_block_name: str = "jira-creds"
) -> List[Dict[str, Any]]:
    """
    Get available transitions for an issue.
    
    Corresponds to GET /rest/api/3/issue/{issueIdOrKey}/transitions
    
    Args:
        issue_key: Issue key or ID
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        List of available transition dictionaries
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        # Get issue transitions
        transitions = client.jira.get_issue_transitions(issue_key)
        transition_list = transitions.get("transitions", [])
        logger.info(f"Retrieved {len(transition_list)} transitions for issue {issue_key}")
        return transition_list
        
    except Exception as e:
        logger.error(f"Failed to get transitions for issue {issue_key}: {str(e)}")
        raise


@task(name="jira.workflows.transition-issue")
async def transition_issue(
    issue_key: str,
    transition_id: str,
    credentials_block_name: str = "jira-creds"
) -> bool:
    """
    Transition an issue to a new status.
    
    Corresponds to POST /rest/api/3/issue/{issueIdOrKey}/transitions
    
    Args:
        issue_key: Issue key or ID
        transition_id: Transition ID to execute
        credentials_block_name: Name of the Jira credentials block
        
    Returns:
        True if transition was successful
    """
    try:
        # Load credentials from block
        jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
        client = jira_creds.get_client()
        
        # Execute transition
        client.jira.issue_transition(issue_key, transition_id)
        logger.info(f"Transitioned issue {issue_key} using transition {transition_id}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to transition issue {issue_key}: {str(e)}")
        raise

# =============================================================================
# NOKTAH HUB (spec 009): comments, entity properties, fields, search, changelog
# =============================================================================
#
# Plain REST calls with the same Basic auth as `jira.issue-bulk.create`. Unlike the older
# tasks above, these RAISE on failure (Prefect retries them, and the flow decides what a
# failure means for its row, comment or page).

async def _jira_http(credentials_block_name: str) -> tuple:
    """(base url, headers) for a raw Jira REST call."""
    import base64

    jira_creds = await JiraCredentials.load_or_env(credentials_block_name)
    client = jira_creds.get_client()
    auth_header = getattr(client, "_auth_header", None)
    if not auth_header:
        raw = f"{client.jira_username}:{client.jira_token}".encode("utf-8")
        auth_header = f"Basic {base64.b64encode(raw).decode('ascii')}"
    headers = {"Authorization": auth_header, "Content-Type": "application/json", "Accept": "application/json"}
    return client.jira_url.rstrip("/"), headers


async def _jira_request(method: str, path: str, credentials_block_name: str, *,
                        params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None,
                        timeout: float = 60.0) -> Any:
    """One Jira REST call; raises RuntimeError with Jira's own error text on non-2xx."""
    import requests

    base, headers = await _jira_http(credentials_block_name)
    response = requests.request(method, f"{base}{path}", headers=headers, params=params,
                                json=body, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"Jira {method} {path} answered HTTP {response.status_code}: {response.text[:300]}")
    if response.status_code == 204 or not response.content:
        return {}
    return response.json()


def comment_document(text: str) -> Dict[str, Any]:
    """An ADF comment body from plain text: one paragraph per blank-line block, hardBreaks inside."""
    blocks = adf.paragraphs_from_text(text)
    return adf.document(blocks or [adf.paragraph([adf.text_node("-")])])


@task(name="jira.issue.comment", retries=2, retry_delay_seconds=30)
async def jira_issue_comment(issue_key: str, text: str, credentials_block_name: str = "jira-creds") -> Dict[str, Any]:
    """POST /rest/api/3/issue/{key}/comment with an ADF body built from plain text. Returns {id}."""
    result = await _jira_request("POST", f"/rest/api/3/issue/{issue_key}/comment", credentials_block_name,
                                 body={"body": comment_document(text)})
    return {"id": result.get("id")}


@task(name="jira.issue.get-property", retries=2, retry_delay_seconds=30)
async def jira_issue_get_property(issue_key: str, property_key: str,
                                  credentials_block_name: str = "jira-creds") -> Any:
    """GET /rest/api/3/issue/{key}/properties/{property}; returns the property's value."""
    result = await _jira_request("GET", f"/rest/api/3/issue/{issue_key}/properties/{property_key}",
                                 credentials_block_name)
    return result.get("value")


@task(name="jira.fields.map", retries=2, retry_delay_seconds=30)
async def jira_fields_map(credentials_block_name: str = "jira-creds") -> Dict[str, str]:
    """GET /rest/api/3/field → {field id: field name}, e.g. {"customfield_10042": "Field Associate"}."""
    result = await _jira_request("GET", "/rest/api/3/field", credentials_block_name)
    return {f["id"]: f.get("name") or f["id"] for f in (result or []) if isinstance(f, dict) and f.get("id")}


@task(name="jira.search.page", retries=2, retry_delay_seconds=30)
async def jira_search_page(jql: str, next_page_token: Optional[str] = None, max_results: int = 50,
                           credentials_block_name: str = "jira-creds") -> Dict[str, Any]:
    """POST /rest/api/3/search/jql, every field, with the changelog.

    Returns Jira's page as is: {issues, nextPageToken?, isLast?}.
    """
    body: Dict[str, Any] = {"jql": jql, "fields": ["*all"], "expand": "changelog", "maxResults": max_results}
    if next_page_token:
        body["nextPageToken"] = next_page_token
    return await _jira_request("POST", "/rest/api/3/search/jql", credentials_block_name, body=body, timeout=120.0)


@task(name="jira.issue.changelog", retries=2, retry_delay_seconds=30)
async def jira_issue_changelog(issue_key: str, credentials_block_name: str = "jira-creds",
                               page_size: int = 100) -> List[Dict[str, Any]]:
    """GET /rest/api/3/issue/{key}/changelog, every page: the full history when the search's
    embedded changelog was truncated. Returns the histories, oldest first."""
    histories: List[Dict[str, Any]] = []
    start_at = 0
    while True:
        page = await _jira_request("GET", f"/rest/api/3/issue/{issue_key}/changelog", credentials_block_name,
                                   params={"startAt": start_at, "maxResults": page_size})
        values = page.get("values") or []
        histories.extend(values)
        start_at += len(values)
        total = page.get("total")
        if not values or page.get("isLast") or (isinstance(total, int) and start_at >= total):
            return histories


@task(name="jira.user.timezone", retries=2, retry_delay_seconds=30)
async def jira_user_timezone(credentials_block_name: str = "jira-creds") -> Optional[str]:
    """GET /rest/api/3/myself → the API user's time zone. JQL reads a bare date-time in this zone."""
    result = await _jira_request("GET", "/rest/api/3/myself", credentials_block_name)
    return result.get("timeZone")
