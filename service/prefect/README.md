# Prefect Multi-API Workflows

This service provides organized Prefect workflows for integrating multiple APIs and automation tools in a single platform.

## Structure

```
service/prefect/
├── workflows/
│   ├── jira_client.py        # Jira API client
│   ├── jira_workflows.py     # Jira workflow definitions
│   ├── common/               # Shared utilities and helpers
│   │   └── __init__.py
│   └── __init__.py           # Main workflows package
├── main.py                   # CLI runner for all workflows
├── requirements.txt          # Python dependencies
├── Dockerfile               # Container definition
├── .env.example             # Environment variables template
└── README.md
```

## Setup

1. **Environment Configuration**
   ```bash
   # Copy the example environment file to root directory
   cp .env.example ../../.env.local
   
   # Edit ../../.env.local with your API credentials
   JIRA_URL=https://your-domain.atlassian.net
   JIRA_USERNAME=your-email@example.com
   JIRA_API_TOKEN=your-jira-api-token
   
   # Add other API keys as needed
   SLACK_TOKEN=xoxb-your-slack-token
   GITHUB_TOKEN=ghp_your-github-token
   OPENAI_API_KEY=sk-your-openai-key
   ```

2. **API Tokens**
   - **Jira**: https://id.atlassian.com/manage-profile/security/api-tokens
   - **Slack**: https://api.slack.com/apps (Bot User OAuth Token)
   - **GitHub**: https://github.com/settings/tokens
   - **OpenAI**: https://platform.openai.com/api-keys

## Available Workflows

### Jira Integration

- **Connection Test** - Test API connection and list projects
- **Issue Search** - Search issues using JQL queries
- **Issue Creation** - Create new issues with optional comments
- **Health Check** - Periodic monitoring (scheduled every 15 minutes)

### Future Integrations (Ready to Add)

- **Slack** - Send notifications, manage channels
- **GitHub** - Create PRs, manage issues, repository operations
- **Email** - SMTP/IMAP automation
- **Database** - Data processing and ETL workflows
- **AI/ML** - OpenAI, Anthropic API integrations

## Usage

### Command Line Interface

The `main.py` script provides a comprehensive CLI for all workflows:

```bash
# Show all available commands
conda run -n noktah-dashboard-workflow python main.py --help

# Jira workflows
conda run -n noktah-dashboard-workflow python main.py jira test
conda run -n noktah-dashboard-workflow python main.py jira health
conda run -n noktah-dashboard-workflow python main.py jira search "project = ESKL ORDER BY created DESC"
conda run -n noktah-dashboard-workflow python main.py jira create ESKL "New task from workflow" --description "Automated task creation" --comment "Created via Prefect"

# Deploy all workflows to Prefect server
conda run -n noktah-dashboard-workflow python main.py deploy
```

### Using Docker

1. **Start Prefect server**
   ```bash
   # From project root
   docker-compose -f docker-compose.dev.yml up -d prefect
   ```

2. **Deploy workflows**
   ```bash
   # Access the container
   docker exec -it noktah-dashboard-prefect-1 python main.py deploy
   ```

3. **Access Prefect UI**
   Open http://localhost:4200

### Local Development

1. **Install dependencies**
   ```bash
   conda activate noktah-dashboard-workflow
   pip install -r requirements.txt
   ```

2. **Run workflows directly**
   ```bash
   python main.py jira test
   python main.py jira search "project = YOUR-PROJECT"
   ```

## Workflow Development

### Adding New API Integrations

1. **Create client file** (e.g., `workflows/slack_client.py`)
2. **Create workflow definitions** (e.g., `workflows/slack_workflows.py`)  
3. **Update `workflows/__init__.py`** to export new workflows
4. **Add commands to `main.py`** for CLI access
5. **Update deployment function** to include new workflows

### Example Workflow Structure

```python
# workflows/your_api_client.py
class YourAPIClient:
    def __init__(self):
        self.api_key = config('YOUR_API_KEY')
    
    def some_operation(self):
        # API implementation
        pass

# workflows/your_api_workflows.py
from prefect import flow, task
from .your_api_client import YourAPIClient

@task
async def your_api_task():
    client = YourAPIClient()
    return client.some_operation()

@flow
async def your_api_flow():
    result = await your_api_task()
    return result
```

## Workflow Monitoring

- **Prefect UI**: http://localhost:4200
- **Flow Runs**: Monitor execution status and logs
- **Schedules**: Health checks run automatically every 15 minutes
- **Artifacts**: Results and reports stored in Prefect database

## Testing Workflows

All workflows include comprehensive error handling and logging:

```bash
# Test individual components
conda run -n noktah-dashboard-workflow python -c "
import asyncio
from workflows.jira_workflows import jira_connection_test_flow
asyncio.run(jira_connection_test_flow())
"
```

## Deployment Options

### Development Environment
- Uses local SQLite database
- Temporary Prefect server
- Manual workflow execution

### Production Environment  
- PostgreSQL database for workflow state
- Persistent Prefect server
- Scheduled workflow execution
- Monitoring and alerting

## Integration with n8n Migration

This Prefect setup replaces n8n workflows with:

- **Type-safe workflow definitions** instead of visual nodes
- **Version-controlled Python code** instead of JSON exports
- **Built-in retry logic** and comprehensive error handling
- **Centralized monitoring** via Prefect UI
- **Multi-API support** in a single service
- **Scalable async execution** with better performance

## Extending the Platform

The structure is designed for easy extension:

1. **Add new API clients** in the `workflows/` directory
2. **Create workflow definitions** using Prefect decorators
3. **Update the CLI** to include new commands
4. **Deploy** using the existing deployment infrastructure

Each integration follows the same pattern, making the codebase consistent and maintainable.

## Best Practices

- **Environment Variables**: Store all credentials in `.env.local`
- **Error Handling**: Use try/catch in all API operations
- **Logging**: Use Prefect's built-in logging for observability
- **Type Hints**: Include type annotations for better code quality
- **Documentation**: Document all workflow parameters and return values
- **Testing**: Test workflows locally before deployment

## Troubleshooting

### Common Issues

1. **Environment Variables Not Found**
   - Check `.env.local` exists in project root
   - Verify variable names match exactly
   - Restart workflows after environment changes

2. **Import Errors**
   - Ensure all dependencies are installed
   - Check Python path configuration
   - Verify conda environment is activated

3. **API Authentication Failures**
   - Verify API tokens are valid and not expired
   - Check API permissions and scopes
   - Test connections independently

### Debug Mode

Enable detailed logging:

```bash
export PREFECT_LOGGING_LEVEL=DEBUG
python main.py jira test
```