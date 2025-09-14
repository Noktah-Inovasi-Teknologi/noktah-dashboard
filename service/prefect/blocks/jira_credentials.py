"""
Jira Credentials Block for Prefect workflows
"""
import logging
from typing import Dict, List, Optional, Any
from prefect.blocks.core import Block
from pydantic import Field, SecretStr
from atlassian import Jira

logger = logging.getLogger(__name__)


class JiraCredentials(Block):
    """
    Prefect Block for storing and managing Jira credentials.
    
    This block securely stores Jira connection details and provides
    methods to create authenticated Jira clients and perform operations.
    """
    
    _block_type_name = "Jira Credentials"
    _block_type_slug = "jira-credentials"
    _logo_url = "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/jira/jira-original.svg"
    _description = "Block for storing Jira credentials and creating authenticated clients"
    
    # Connection details
    url: str = Field(..., description="Jira instance URL (e.g., https://yourcompany.atlassian.net)")
    username: str = Field(..., description="Jira username or email")
    token: SecretStr = Field(..., description="Jira API token")
    cloud: bool = Field(default=True, description="Whether this is Jira Cloud (vs Server)")
    
    def get_client(self) -> 'JiraClient':
        """
        Create and return an authenticated Jira client.
        
        Returns:
            JiraClient: Authenticated Jira client wrapper
        """
        return JiraClient(
            url=self.url,
            username=self.username,
            token=self.token.get_secret_value(),
            cloud=self.cloud
        )
    
    def test_connection(self) -> Dict[str, Any]:
        """
        Test the Jira connection and return server info.
        
        Returns:
            Dict containing connection status and server info
        """
        client = self.get_client()
        return client.test_connection()


class JiraClient:
    """Jira API client wrapper for workflow operations"""
    
    def __init__(self, url: str, username: str, token: str, cloud: bool = True):
        """
        Initialize Jira client with credentials.
        
        Args:
            url: Jira instance URL
            username: Jira username
            token: Jira API token
            cloud: Whether this is Jira Cloud
        """
        self.jira_url = url
        self.jira_username = username
        self.jira_token = token
        self.cloud = cloud
        
        if not all([url, username, token]):
            raise ValueError("Missing required Jira credentials: url, username, token")
            
        self.jira = Jira(
            url=self.jira_url,
            username=self.jira_username,
            password=self.jira_token,
            cloud=self.cloud
        )
    
    def test_connection(self) -> Dict[str, Any]:
        """Test Jira connection and return server info"""
        try:
            server_info = self.jira.get_server_info()
            logger.info("Successfully connected to Jira")
            return {
                "status": "success",
                "server_info": server_info,
                "connection_time": server_info.get("serverTime", "Unknown")
            }
        except Exception as e:
            logger.error(f"Failed to connect to Jira: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def get_projects(self) -> List[Dict[str, Any]]:
        """Get list of accessible projects"""
        try:
            projects = self.jira.projects()
            return [
                {
                    "key": project["key"],
                    "name": project["name"],
                    "id": project["id"],
                    "projectTypeKey": project.get("projectTypeKey", "Unknown")
                }
                for project in projects
            ]
        except Exception as e:
            logger.error(f"Failed to get projects: {str(e)}")
            raise
    
    def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """Get specific issue by key"""
        try:
            issue = self.jira.issue(issue_key)
            return {
                "key": issue["key"],
                "summary": issue["fields"]["summary"],
                "status": issue["fields"]["status"]["name"],
                "assignee": issue["fields"]["assignee"]["displayName"] if issue["fields"]["assignee"] else "Unassigned",
                "created": issue["fields"]["created"],
                "updated": issue["fields"]["updated"]
            }
        except Exception as e:
            logger.error(f"Failed to get issue {issue_key}: {str(e)}")
            raise
    
    def search_issues(self, jql: str, max_results: int = 50) -> List[Dict[str, Any]]:
        """Search issues using JQL"""
        try:
            results = self.jira.jql(jql, limit=max_results)
            issues = []
            
            for issue in results["issues"]:
                issues.append({
                    "key": issue["key"],
                    "summary": issue["fields"]["summary"],
                    "status": issue["fields"]["status"]["name"],
                    "assignee": issue["fields"]["assignee"]["displayName"] if issue["fields"]["assignee"] else "Unassigned",
                    "priority": issue["fields"]["priority"]["name"] if issue["fields"]["priority"] else "None",
                    "created": issue["fields"]["created"]
                })
            
            return issues
        except Exception as e:
            logger.error(f"Failed to search issues with JQL '{jql}': {str(e)}")
            raise
    
    def create_issue(self, project_key: str, summary: str, description: str = "", issue_type: str = "Task") -> str:
        """Create a new issue and return its key"""
        try:
            issue_data = {
                "project": {"key": project_key},
                "summary": summary,
                "description": description,
                "issuetype": {"name": issue_type}
            }
            
            result = self.jira.issue_create(fields=issue_data)
            issue_key = result["key"]
            logger.info(f"Created issue: {issue_key}")
            return issue_key
        except Exception as e:
            logger.error(f"Failed to create issue: {str(e)}")
            raise
    
    def update_issue(self, issue_key: str, fields: Dict[str, Any]) -> bool:
        """Update issue fields"""
        try:
            self.jira.issue_update(issue_key, fields=fields)
            logger.info(f"Updated issue: {issue_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to update issue {issue_key}: {str(e)}")
            raise
    
    def add_comment(self, issue_key: str, comment: str) -> bool:
        """Add comment to issue"""
        try:
            self.jira.issue_add_comment(issue_key, comment)
            logger.info(f"Added comment to issue: {issue_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to add comment to issue {issue_key}: {str(e)}")
            raise