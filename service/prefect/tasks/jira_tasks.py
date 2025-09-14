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
except ImportError:
    # For running as standalone script
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from blocks.jira_credentials import JiraCredentials

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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
        client = jira_creds.get_client()
        
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
                logger.warning(f"Encountered {len(errors)} errors during bulk creation")
            
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
        
        # Handle both old format (jira_assets) and new format (issue_updates)
        if "issue_updates" in data:
            # New format: direct issue_updates array
            issue_updates = data["issue_updates"]
        elif "jira_assets" in data:
            # Old format: nested jira_assets structure
            for client_data in data["jira_assets"]:
                if "assets" in client_data:
                    for asset in client_data["assets"]:
                        issue_updates.append(asset)
        
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
        "warnings": []
    }
    
    try:
        for index, issue_update in enumerate(issue_updates):
            issue_valid = True
            issue_errors = []
            
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
                if "summary" in fields and not fields["summary"].strip():
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
        
        if validation_result["invalid_count"] > 0:
            logger.warning(f"Found {validation_result['invalid_count']} invalid issues")
        
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
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
        jira_creds = await JiraCredentials.load(credentials_block_name)
        client = jira_creds.get_client()
        
        # Execute transition
        client.jira.issue_transition(issue_key, transition_id)
        logger.info(f"Transitioned issue {issue_key} using transition {transition_id}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to transition issue {issue_key}: {str(e)}")
        raise