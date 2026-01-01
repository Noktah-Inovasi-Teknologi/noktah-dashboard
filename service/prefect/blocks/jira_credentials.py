"""
Jira Credentials Block for Prefect workflows

This module provides Prefect Blocks for securely storing and managing
Jira API credentials with Basic Auth (email + API token).
"""
import os
import logging
from typing import Dict, List, Any, Optional

from prefect.blocks.core import Block
from pydantic import Field, SecretStr
from atlassian import Jira

logger = logging.getLogger(__name__)


class JiraCredentials(Block):
    """
    Prefect Block for storing and managing Jira credentials.

    This block securely stores Jira connection details and provides
    methods to create authenticated Jira clients for API operations.
    """

    _block_type_name = "Jira Credentials"
    _block_type_slug = "jira-credentials"
    _logo_url = "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/jira/jira-original.svg"
    _description = "Block for storing Jira API credentials (Basic Auth with email and API token)"

    # Connection details
    jira_url: Optional[str] = Field(
        default=None,
        description="Jira instance URL (e.g., https://yourcompany.atlassian.net)"
    )

    jira_username: Optional[str] = Field(
        default=None,
        description="Jira username or email address"
    )

    jira_token: Optional[SecretStr] = Field(
        default=None,
        description="Jira API token (generate from Atlassian account settings)"
    )

    cloud: bool = Field(
        default=True,
        description="Whether this is Jira Cloud (True) or Jira Server/Data Center (False)"
    )

    def get_client(self) -> 'JiraClient':
        """
        Create and return an authenticated Jira client.

        Returns:
            JiraClient: Authenticated Jira client wrapper
        """
        return JiraClient(
            jira_url=self.jira_url,
            jira_username=self.jira_username,
            jira_token=self.jira_token.get_secret_value() if self.jira_token else None,
            cloud=self.cloud
        )

    def test_connection(self) -> Dict[str, Any]:
        """
        Test the Jira connection.

        Returns:
            Dict containing connection status and server info
        """
        client = self.get_client()
        return client.test_connection()


class JiraClient:
    """
    Jira API client with Basic Auth (email + API token).

    This client manages authentication, handles connection initialization,
    and provides methods for interacting with Jira Cloud/Server APIs.

    Attributes:
        jira: Atlassian Jira API client instance
        jira_url: Jira instance URL
        jira_username: Jira username/email
        cloud: Whether this is Jira Cloud or Server
    """

    def __init__(
        self,
        jira_url: Optional[str] = None,
        jira_username: Optional[str] = None,
        jira_token: Optional[str] = None,
        cloud: bool = True
    ):
        """
        Initialize Jira client with credentials.

        Args:
            jira_url: Jira instance URL
            jira_username: Jira username or email
            jira_token: Jira API token
            cloud: Whether this is Jira Cloud (vs Server/Data Center)

        Raises:
            ValueError: If credentials cannot be initialized
        """
        # Load from parameters or environment variables
        self.jira_url = jira_url or os.getenv('JIRA_URL')
        self.jira_username = jira_username or os.getenv('JIRA_USERNAME')
        self.jira_token = jira_token or os.getenv('JIRA_TOKEN')
        self.cloud = cloud

        # Validate credentials
        if not self.jira_url:
            raise ValueError("JIRA_URL is required (set via parameter or environment variable)")
        if not self.jira_username:
            raise ValueError("JIRA_USERNAME is required (set via parameter or environment variable)")
        if not self.jira_token:
            raise ValueError("JIRA_TOKEN is required (set via parameter or environment variable)")

        # Initialize Jira client
        self._initialize_client()

    def _initialize_client(self) -> None:
        """
        Initialize Jira API client with credentials.

        Raises:
            ValueError: If client initialization fails
        """
        try:
            self.jira = Jira(
                url=self.jira_url,
                username=self.jira_username,
                password=self.jira_token,  # API token is passed as password for Basic Auth
                cloud=self.cloud
            )
            logger.info(f"Initialized Jira client for {self.jira_url}")
        except Exception as e:
            logger.error(f"Failed to initialize Jira client: {str(e)}")
            raise ValueError(f"Failed to initialize Jira client: {str(e)}")

    def test_connection(self) -> Dict[str, Any]:
        """
        Test Jira connection and return server info.

        Returns:
            Dict with status and server information
        """
        try:
            server_info = self.jira.get_server_info()
            logger.info("Successfully connected to Jira")
            return {
                "status": "success",
                "message": "Jira connection successful",
                "server_info": {
                    "version": server_info.get("version", "Unknown"),
                    "server_time": server_info.get("serverTime", "Unknown"),
                    "base_url": server_info.get("baseUrl", self.jira_url)
                }
            }
        except Exception as e:
            error_message = f"Jira connection test failed: {str(e)}"
            logger.error(error_message)
            return {
                "status": "error",
                "error": error_message
            }

    def get_projects(self) -> List[Dict[str, Any]]:
        """
        Get list of accessible projects.

        Returns:
            List of project dictionaries with key, name, id

        Raises:
            Exception: If API call fails
        """
        try:
            projects = self.jira.projects()
            logger.info(f"Retrieved {len(projects)} projects")
            return [
                {
                    "key": project["key"],
                    "name": project["name"],
                    "id": project["id"],
                    "project_type": project.get("projectTypeKey", "Unknown")
                }
                for project in projects
            ]
        except Exception as e:
            logger.error(f"Failed to get projects: {str(e)}")
            raise

    def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """
        Get specific issue by key.

        Args:
            issue_key: Jira issue key (e.g., "PROJ-123")

        Returns:
            Dict containing issue details

        Raises:
            Exception: If issue not found or API call fails
        """
        try:
            issue = self.jira.issue(issue_key)
            return {
                "key": issue["key"],
                "summary": issue["fields"]["summary"],
                "status": issue["fields"]["status"]["name"],
                "assignee": issue["fields"]["assignee"]["displayName"] if issue["fields"]["assignee"] else None,
                "created": issue["fields"]["created"],
                "updated": issue["fields"]["updated"]
            }
        except Exception as e:
            logger.error(f"Failed to get issue {issue_key}: {str(e)}")
            raise

    def search_issues(self, jql: str, max_results: int = 50) -> List[Dict[str, Any]]:
        """
        Search issues using JQL (Jira Query Language).

        Args:
            jql: JQL query string
            max_results: Maximum number of results to return

        Returns:
            List of issue dictionaries

        Raises:
            Exception: If search fails
        """
        try:
            results = self.jira.jql(jql, limit=max_results)
            issues = []

            for issue in results.get("issues", []):
                issues.append({
                    "key": issue["key"],
                    "summary": issue["fields"]["summary"],
                    "status": issue["fields"]["status"]["name"],
                    "assignee": issue["fields"]["assignee"]["displayName"] if issue["fields"].get("assignee") else None,
                    "priority": issue["fields"]["priority"]["name"] if issue["fields"].get("priority") else None,
                    "created": issue["fields"]["created"]
                })

            logger.info(f"Found {len(issues)} issues matching JQL: {jql}")
            return issues
        except Exception as e:
            logger.error(f"Failed to search issues with JQL '{jql}': {str(e)}")
            raise

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str = "",
        issue_type: str = "Task",
        additional_fields: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Create a new Jira issue.

        Args:
            project_key: Project key (e.g., "PROJ")
            summary: Issue summary/title
            description: Issue description
            issue_type: Issue type (e.g., "Task", "Story", "Bug")
            additional_fields: Additional custom fields to set

        Returns:
            Created issue key (e.g., "PROJ-123")

        Raises:
            Exception: If issue creation fails
        """
        try:
            # Build issue data
            issue_data = {
                "project": {"key": project_key},
                "summary": summary,
                "description": description,
                "issuetype": {"name": issue_type}
            }

            # Add additional fields if provided
            if additional_fields:
                issue_data.update(additional_fields)

            result = self.jira.issue_create(fields=issue_data)
            issue_key = result["key"]
            logger.info(f"Created issue: {issue_key}")
            return issue_key
        except Exception as e:
            logger.error(f"Failed to create issue: {str(e)}")
            raise

    def update_issue(self, issue_key: str, fields: Dict[str, Any]) -> bool:
        """
        Update issue fields.

        Args:
            issue_key: Jira issue key
            fields: Dictionary of fields to update

        Returns:
            True if successful

        Raises:
            Exception: If update fails
        """
        try:
            self.jira.issue_update(issue_key, fields=fields)
            logger.info(f"Updated issue: {issue_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to update issue {issue_key}: {str(e)}")
            raise

    def add_comment(self, issue_key: str, comment: str) -> bool:
        """
        Add comment to an issue.

        Args:
            issue_key: Jira issue key
            comment: Comment text

        Returns:
            True if successful

        Raises:
            Exception: If adding comment fails
        """
        try:
            self.jira.issue_add_comment(issue_key, comment)
            logger.info(f"Added comment to issue: {issue_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to add comment to issue {issue_key}: {str(e)}")
            raise
