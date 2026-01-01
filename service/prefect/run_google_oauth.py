"""
Test script to run OAuth flow and generate new token.json

This script will:
1. Use the client_secret_*.json file
2. Open your browser for authentication
3. Generate a new token.json file with access and refresh tokens
"""
import os
import sys
import asyncio
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from blocks.google_credentials import GoogleCredentials


async def main():
    """Run OAuth flow to generate new credentials."""

    print("=" * 60)
    print("Google OAuth Authentication Flow")
    print("=" * 60)

    # Find client_secret file
    current_dir = Path(__file__).parent
    client_secret_files = list(current_dir.glob("client_secret_*.json"))

    if not client_secret_files:
        print("\n[ERROR] No client_secret_*.json file found!")
        print(f"   Looking in: {current_dir}")
        print("\n[DOWNLOAD] Please download OAuth credentials from Google Cloud Console:")
        print("   https://console.cloud.google.com/apis/credentials")
        return

    client_secret_file = client_secret_files[0]
    print(f"\n[OK] Found client secret file: {client_secret_file.name}")

    # Create credentials block with client_secret file
    print("\n[INIT] Creating Google Credentials block...")
    google_creds = GoogleCredentials(
        credentials_file=str(client_secret_file),
        scopes=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.readonly'
        ]
    )

    print("\n[OAUTH] Starting OAuth flow...")
    print("   This will open your browser for authentication.")
    print("   Please sign in and authorize the application.")

    try:
        # This will trigger the OAuth flow
        client = google_creds.get_client()

        print("\n[SUCCESS] Authentication successful!")

        # Test the connection
        print("\n[TEST] Testing connection...")
        result = client.test_connection()

        if result["status"] == "success":
            print(f"[OK] {result['message']}")
        else:
            print(f"[ERROR] Connection test failed: {result.get('error')}")
            return

        # Check if token.json was created
        token_file = current_dir / "token.json"
        if token_file.exists():
            print(f"\n[OK] Token file created: {token_file}")
            print("\n[INFO] Token file contents (sanitized):")

            import json
            with open(token_file) as f:
                token_data = json.load(f)

            # Show non-sensitive information
            print(f"   Scopes: {token_data.get('scopes', [])}")
            print(f"   Token URI: {token_data.get('token_uri')}")
            print(f"   Client ID: {token_data.get('client_id', '')[:20]}...")

            # Extract refresh token for environment variable
            refresh_token = token_data.get('refresh_token')
            if refresh_token:
                print("\n" + "=" * 60)
                print("[IMPORTANT] Save this refresh token for production!")
                print("=" * 60)
                print("\nAdd to your .env file:")
                print(f"GOOGLE_REFRESH_TOKEN={refresh_token}")
                print(f"GOOGLE_CLIENT_ID={token_data.get('client_id')}")
                print(f"GOOGLE_CLIENT_SECRET={token_data.get('client_secret')}")
                print("\n[WARNING] Keep these values secret! Never commit to Git.")
                print("=" * 60)

        print("\n[SUCCESS] OAuth flow completed successfully!")
        print("\n[NEXT STEPS]")
        print("   1. Your token.json has been created")
        print("   2. Copy the refresh token to .env for production")
        print("   3. You can now run Prefect flows locally")

    except FileNotFoundError as e:
        print(f"\n[ERROR] {e}")
        print("\n[TIP] Make sure the client_secret_*.json file is in the correct location.")
    except Exception as e:
        print(f"\n[ERROR] Authentication failed: {e}")
        print("\n[TROUBLESHOOTING]")
        print("   - Check that OAuth redirect URI is set to http://localhost:8080/")
        print("   - Ensure the Google Cloud Console project has required APIs enabled")
        print("   - Try running the script again")


if __name__ == "__main__":
    asyncio.run(main())
