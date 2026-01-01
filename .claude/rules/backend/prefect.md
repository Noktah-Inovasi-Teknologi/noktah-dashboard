# Prefect Service Development Rules

## Overview

The Prefect service orchestrates workflow automation for the dashboard, handling integration with Google APIs (Sheets, Drive) and Jira. It runs in a Docker container with Prefect Server UI on port 4200.

## Architecture Principles

### Service Structure

The service follows a modular structure:
- **flows/** - Workflow definitions using @flow decorator for orchestration
- **tasks/** - Atomic, reusable operations using @task decorator
- **blocks/** - Prefect Blocks for secure credential storage
- **hashmap.py** - Static data mappings (workers, components, role assignments)
- **pyproject.toml** - UV package manager configuration
- **uv.lock** - Locked dependencies for reproducible builds
- **Dockerfile** - Container configuration using UV

### Core Concepts

**Flows** are high-level workflow orchestrations that coordinate multiple tasks, handle error recovery, and return comprehensive result dictionaries. They use async/await pattern and never raise exceptions to callers.

**Tasks** are atomic, reusable operations with single responsibility. They can be retried independently and should raise exceptions for Prefect's retry logic to handle.

**Blocks** provide secure credential storage with environment variable fallback and lazy service initialization. Use SecretStr for sensitive data.

## Development Standards

### Flow Development

**Naming Convention**: Use kebab-case for flow names (e.g., "content-plan-to-jira")

**Function Signature**: All flows must be async functions with descriptive parameter names, optional parameters with defaults, and credentials_block_name parameter

**Return Type**: Always return Dict[str, Any] with start_time, end_time, data, summary, and optional error fields

**Error Handling**: Use comprehensive try-except blocks, log errors, set error field in result, never raise exceptions

**Result Structure**: Include metadata (timestamps, input params), main data array, summary statistics, and error details if applicable

**Best Practices**:
- Always use async def for concurrent execution capability
- Log at appropriate levels (info for progress, warning for recoverable issues, error for failures)
- Implement rate limiting with random delays between API calls
- Return structured results consistently
- Include summary statistics for observability

### Task Development

**Naming Convention**: Use "api-group.resource.action" pattern (e.g., "google.sheets.read-data")

**Organization**: Group tasks by API service in separate files (google_tasks.py, jira_tasks.py, utility_tasks.py)

**Decorator Configuration**: Specify retries (typically 2) and retry_delay_seconds (typically 30) for API calls

**Error Handling**: Tasks should raise exceptions (unlike flows) to leverage Prefect's retry mechanism

**Credentials Pattern**: Accept credentials_block_name parameter with default value, load credentials dynamically

**Best Practices**:
- Single responsibility - one task performs one operation
- Use type hints for all parameters and return types
- Don't combine unrelated operations
- Keep tasks focused and reusable
- Raise exceptions for retry logic, don't catch and return error dicts

### Block Development

**Block Metadata**: Define _block_type_name, _block_type_slug, _logo_url, and _description

**Credential Fields**: Use SecretStr for sensitive data (api_key, client_secret, refresh_token)

**Environment Fallback**: Load credentials from environment variables if not provided directly

**Client Factory**: Implement get_client() method that returns authenticated client instance

**Connection Testing**: Provide test_connection() method that returns status dict

**Multiple Auth Methods**: Support OAuth, API key, and other authentication methods with fallback logic

**Lazy Initialization**: Use @property decorator for service initialization to defer until needed

## Data Management

### Hashmap Pattern

Static mappings stored in hashmap.py include:
- WORKERS - maps worker names to IDs
- COMPONENTS - maps client names to component IDs
- CONTENT_EDITOR - maps client names to assigned editor names
- FIELD_ASSOCIATE - maps client names to assigned associate names

Access hashmap data by importing and using dictionary .get() method with fallback values.

### Data Formatting Standards

**Date/Time Formatting**:
- Use ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ) for all timestamps
- Jira dates use YYYY-MM-DD format only
- Indonesian month names for user-facing content
- Always include timezone indicator (Z for UTC)

**Text Formatting**:
- Normalize line breaks to \n
- Remove excessive whitespace (max 2 consecutive newlines)
- Trim leading/trailing spaces
- Consistent spacing throughout

**Numeric Formatting**:
- Convert string numbers to float
- Remove currency symbols and formatting
- Return None for empty/missing values
- Handle both string and numeric inputs

### JSON Output Standards

All JSON outputs must include:
- **metadata** section with workflow name, execution timestamp, source identifier, record count, and format standards
- **data** section with array of records
- Proper encoding (UTF-8, ensure_ascii=False)
- Indentation of 2 spaces for readability

## API Integration Patterns

### Google API Integration

**Authentication**: Use three-method credential initialization:
1. Load from saved token.json file (if exists and valid)
2. Use refresh token from environment variables (production/Docker)
3. Run OAuth2 flow using client_secret_*.json (local development)

**Required Environment Variables**:
- GOOGLE_CLIENT_ID
- GOOGLE_CLIENT_SECRET
- GOOGLE_REFRESH_TOKEN

**API Scopes**:
- https://www.googleapis.com/auth/spreadsheets (read/write)
- https://www.googleapis.com/auth/drive.readonly (read-only)

**Spreadsheet Operations**: Use pandas DataFrame for data processing, convert to records dict, include metadata about columns and data types

**Drive Operations**: Use Drive API v3 with query syntax, support all drives, request specific fields to minimize data transfer

### Jira API Integration

**Authentication**: Use Basic Auth with email and API token

**Required Environment Variables**:
- JIRA_URL (e.g., https://domain.atlassian.net)
- JIRA_USERNAME (email address)
- JIRA_TOKEN (API token from Atlassian)

**Issue Creation**:
- Single issues via atlassian-python-api library
- Bulk creation via REST API (max 45 issues per request)
- Custom fields use customfield_XXXXX format

**Custom Field Mapping**: Maintain mapping of field names to customfield IDs in code or config

## Docker Environment

### Container Configuration

**Base Image**: prefecthq/prefect:3.6.9-python3.14

**Package Manager**: UV (installed via multi-stage build from ghcr.io/astral-sh/uv:latest)

**Dependency Installation**: Use "uv sync --frozen --no-dev" for reproducible builds

**File Copying Strategy**:
1. Copy pyproject.toml and uv.lock first (for layer caching)
2. Install dependencies
3. Copy application code last

**Environment Variables**:
- PYTHONPATH=/app
- PATH includes .venv/bin for virtual environment

**Health Check**: Verify Prefect API endpoint every 30 seconds with 10s timeout

**Command**: Use exec form with .venv/bin/prefect to start server on 0.0.0.0

### Environment Variables

**Prefect Configuration**:
- PREFECT_SERVER_API_HOST=0.0.0.0
- PREFECT_SERVER_API_PORT=4200
- PREFECT_API_DATABASE_CONNECTION_URL (PostgreSQL async connection)
- PREFECT_LOGGING_LEVEL=DEBUG (for development)

**Database**: Use asyncpg for Prefect 3.x async architecture

**Credentials**: Pass Google and Jira credentials as environment variables to container

**Volume Mounting**: Mount service/prefect to /app for live code updates during development

## Running Workflows

### In Docker Container (Recommended)

Execute flows using "docker exec prefect python flows/flow_name.py"

View logs with "docker logs -f prefect"

Access Prefect UI at http://localhost:4200

### Locally (Development)

Use "uv run" to execute flows with proper virtual environment

Set required environment variables before running

Test individual tasks and flows before containerizing

### Container Management

**Restart**: Use "docker-compose restart prefect" for simple restart

**Rebuild**: Use "docker-compose up -d --build prefect" after Dockerfile changes

**Force Rebuild**: Add --no-cache flag if experiencing caching issues

**View Logs**: Use "docker-compose logs -f prefect" to follow logs in real-time

## Testing Standards

### Flow Testing

Flows should include __name__ == "__main__" block for standalone testing

Test execution creates timestamped output directory for results

Results saved as JSON with metadata and formatted output

Print results to console for immediate feedback

### Task Testing

Test tasks independently with sample data

Verify correct error handling and retry behavior

Check credential loading and service initialization

Validate return value structure and data types

## Error Handling Standards

### Flow Error Handling

Use comprehensive try-except with specific exception types where possible

Log errors at appropriate levels (warning for recoverable, error for failures)

Continue processing when possible (don't fail entire flow for single item)

Always set end_time in finally block

Return error summary with successful and failed counts

### Task Error Handling

Raise exceptions to leverage Prefect's retry mechanism

Don't retry authentication failures (raise immediately)

Retry rate limit errors after delay

Log error details before raising

Use specific exception types when available

## Performance Optimization

### Rate Limiting

Add random delays between API calls to avoid rate limits

Don't delay after the last item in a sequence

Use reasonable delay ranges (e.g., 5-10 seconds)

Log delay information for debugging

### Batch Processing

Process items in batches appropriate to API limits (e.g., 45 for Jira bulk API)

Add delays between batches

Track progress with batch numbers and counts

### Concurrent Processing

Use asyncio.gather() for independent operations

Handle exceptions in concurrent execution (return_exceptions=True)

Process results and errors appropriately

Don't use concurrency for operations with dependencies

## Logging Standards

### Log Levels

**INFO**: Normal flow progress, important state changes, performance metrics

**WARNING**: Recoverable issues, skipped items, fallback behavior

**ERROR**: Failures affecting results, API errors, authentication failures

**DEBUG**: Detailed diagnostic info, request payloads, response data

### Structured Logging

Include context in log messages (client name, item counts, batch numbers)

Log state transitions and phase changes

Include performance metrics (duration, items per second)

Use consistent formatting across all logs

## Security Best Practices

### Credential Management

Never hardcode credentials in source code

Use environment variables for all sensitive data

Use Prefect Blocks for credential storage

Access secrets only when needed (lazy initialization)

### Secret Handling

Use SecretStr from pydantic for sensitive fields

Call get_secret_value() only when necessary

Don't pass secrets as function parameters unnecessarily

Store secrets in .env file (gitignored)

### Logging Security

Never log sensitive data (API keys, tokens, passwords)

Log non-sensitive identifiers (block names, resource IDs)

Sanitize data before logging (remove sensitive fields)

Be cautious with request/response logging

## Dependencies

### Package Management

Use UV package manager for fast, reliable dependency resolution

Pin exact versions in pyproject.toml for stability

Lock dependencies with uv.lock for reproducibility

Organize dependencies by category in pyproject.toml

### Required Packages

- **prefect** (>=3.6.9) - Workflow orchestration framework
- **asyncpg** (>=0.30.0) - Async PostgreSQL driver for Prefect 3.x
- **pandas** (>=2.3.3) - Data processing and transformation
- **atlassian-python-api** (>=4.0.7) - Jira API integration
- **google-api-python-client** (>=2.187.0) - Google Workspace APIs
- **google-auth** (>=2.40.3) - Google authentication
- **google-auth-oauthlib** (>=1.2.2) - OAuth flow support
- **requests** (>=2.32.5) - HTTP client for REST APIs
- **pydantic-settings** (>=2.0.0) - Configuration management

### Version Management

Review and update dependencies quarterly

Test thoroughly after updates

Check compatibility with Prefect version

Use "uv sync" to install exact locked versions

## Documentation Requirements

### Flow Documentation

Include comprehensive docstring with purpose, arguments, return value structure, and side effects

Document all parameters with types and descriptions

Describe return dictionary structure in detail

Provide example usage in test block

### Task Documentation

Write concise docstring describing operation

Use type hints for all parameters and return type

Document retry configuration rationale

Explain credential requirements

### Block Documentation

Document purpose and use cases in class docstring

Describe all fields with clear descriptions

Document credential requirements and environment variables

Provide usage examples in docstring or comments

## Migration Guide

### Adding New Flow

Create file in flows/ directory with descriptive name

Import required tasks from tasks/ modules

Define flow with @flow decorator and proper naming

Implement flow following standard pattern

Add comprehensive error handling

Include test block at bottom

Test locally before deploying to container

### Adding New Task

Determine API group and add to appropriate file in tasks/

Define task with @task decorator and retry configuration

Follow naming convention (api-group.resource.action)

Implement with proper error handling (raise exceptions)

Add comprehensive type hints

Document parameters and return value

Test independently before using in flows

### Adding New Block

Create file in blocks/ directory

Extend prefect.blocks.core.Block

Define block metadata (_block_type_name, etc.)

Add credential fields with SecretStr for sensitive data

Implement get_client() factory method

Implement test_connection() method

Register block and test with sample credentials

## Common Patterns

### Multi-Step Workflow Pattern

Create timestamped output directory for all results

Execute steps sequentially with dependency handling

Save intermediate results to JSON files

Return early on step failure

Pass data between steps via result dictionaries

### Data Transformation Pattern

Follow Extract-Transform-Load (ETL) pattern

Extract raw data from source

Transform each item individually

Load processed data to destination

Include validation at each step

### Validation and Creation Pattern

Separate validation from creation logic

Support validate-only mode with flag

Return validation results with details

Only proceed with creation if validation passes

Log validation failures for debugging

## Troubleshooting

### Common Issues

**Import Errors**: Use try-except with fallback import for standalone execution compatibility

**Credential Loading**: Verify environment variables in container with "docker exec prefect env"

**Rate Limiting**: Add appropriate delays between API requests

**Docker Sync**: Rebuild container after Dockerfile or dependency changes

**Block Registration**: Register blocks explicitly if not auto-discovered

### Debugging Tips

Check Prefect UI for detailed flow run history

View container logs with docker-compose logs

Verify environment variables are set correctly

Test tasks independently before using in flows

Use DEBUG logging level for detailed diagnostics

## Additional Resources

- Prefect Documentation: https://docs.prefect.io/
- Google API Python Client: https://github.com/googleapis/google-api-python-client
- Atlassian Python API: https://atlassian-python-api.readthedocs.io/
- Jira REST API: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
- UV Documentation: https://github.com/astral-sh/uv

## Maintenance

### Regular Tasks

Update dependencies quarterly and test thoroughly

Monitor Prefect UI for failed runs and error patterns

Review and update hashmap data as team changes

Clean output directory and archive old results

Optimize retry strategies based on failure patterns

### Performance Monitoring

Track flow execution times and optimize slow operations

Monitor API rate limit usage

Review error logs for patterns

Optimize batch sizes based on performance data

Adjust retry delays based on success rates

---

**Last Updated:** 2026-01-01
**Prefect Version:** 3.6.9
**Python Version:** 3.14
**Package Manager:** UV
