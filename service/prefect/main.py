#!/usr/bin/env python3
"""
Main workflow runner for Prefect workflows
"""
import os
import sys
import asyncio
import argparse
from pathlib import Path

# Add the current directory to Python path for imports
sys.path.insert(0, str(Path(__file__).parent))

from decouple import Config, RepositoryEnv

def setup_environment():
    """Load environment variables from .env"""
    # Set Prefect API URL for local server
    import os
    os.environ['PREFECT_API_URL'] = 'http://localhost:4200/api'
    
    config = Config(RepositoryEnv('../../.env'))
    
    # Set environment variables for integrations
    env_vars = [
        'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN',
        'GOOGLE_SERVICE_ACCOUNT_JSON',
        'DATABASE_URL', 'REDIS_URL'
    ]
    
    for var in env_vars:
        if config(var, default=None):
            os.environ[var] = config(var)
    
    return config

# Content Plan Workflows
async def run_content_plan_test(max_rows: int = 5):
    """Run content plan workflow test - read data only"""
    from flows.content_plan_spreadsheet_to_jira_issue import content_plan_test_flow
    
    result = await content_plan_test_flow(max_rows)
    print(f'Read {result.get("total_rows", 0)} rows from spreadsheet')
    return result

async def run_content_plan_read(
    spreadsheet_id: str = "1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY",
    sheet_name: str = "Clients",
    max_rows: int = None
):
    """Read content plan data from Google Spreadsheet"""
    from flows.content_plan_spreadsheet_to_jira_issue import read_content_plan_flow
    
    result = await read_content_plan_flow(
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        max_rows=max_rows
    )
    
    if 'error' in result:
        print(f'Error: {result["error"]}')
    else:
        print(f'Successfully read {result.get("total_rows", 0)} rows from {sheet_name} sheet')
    
    return result

# Workflow deployment
async def deploy_workflows():
    """Deploy all workflows to Prefect server"""
    try:
        from flows.content_plan_spreadsheet_to_jira_issue import (
            read_content_plan_flow,
            content_plan_test_flow
        )
        
        # Deploy Content Plan workflows
        await read_content_plan_flow.to_deployment(
            name="read-content-plan",
            description="Read content plan data from Google Spreadsheet",
            tags=["content-plan", "google-sheets", "data-read"],
            version="1.0.0",
            parameters={
                "spreadsheet_id": "1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY",
                "sheet_name": "Clients",
                "max_rows": None
            }
        )
        
        await content_plan_test_flow.to_deployment(
            name="content-plan-test",
            description="Test content plan workflow with dry run",
            tags=["content-plan", "testing", "dry-run"],
            version="1.0.0"
        )
        
        print("Workflows deployed successfully!")
        print("Available: read-content-plan, content-plan-test")
        print("Access Prefect UI at: http://localhost:4200")
        
    except Exception as e:
        print(f"Deployment failed: {str(e)}")
        raise

def main():
    parser = argparse.ArgumentParser(description='Run Prefect Workflows')
    
    # Main command
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Content Plan commands
    content_parser = subparsers.add_parser('content-plan', help='Content plan workflow commands')
    content_subparsers = content_parser.add_subparsers(dest='content_action', help='Content plan actions')
    
    # Content plan test
    test_parser = content_subparsers.add_parser('test', help='Test reading content plan data')
    test_parser.add_argument('--max-rows', type=int, default=5, help='Max rows for testing (default: 5)')
    
    # Content plan read
    read_parser = content_subparsers.add_parser('read', help='Read content plan data from spreadsheet')
    read_parser.add_argument('--spreadsheet-id', default='1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY', 
                            help='Google Spreadsheet ID')
    read_parser.add_argument('--sheet-name', default='Clients', help='Sheet name (default: Clients)')
    read_parser.add_argument('--max-rows', type=int, help='Maximum number of rows to read')
    
    # Deployment command
    subparsers.add_parser('deploy', help='Deploy workflows to Prefect server')
    
    args = parser.parse_args()
    
    # Show help if no command provided
    if not args.command:
        parser.print_help()
        return
    
    # Setup environment
    setup_environment()
    
    # Route commands
    if args.command == 'content-plan':
        if not args.content_action:
            content_parser.print_help()
            return
            
        if args.content_action == 'test':
            asyncio.run(run_content_plan_test(args.max_rows))
        elif args.content_action == 'read':
            asyncio.run(run_content_plan_read(
                args.spreadsheet_id,
                args.sheet_name,
                args.max_rows
            ))
    
    elif args.command == 'deploy':
        asyncio.run(deploy_workflows())

if __name__ == "__main__":
    main()