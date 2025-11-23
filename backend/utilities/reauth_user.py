"""
Re-authentication module for users with expired or invalid OAuth tokens.

This module provides functionality to re-authenticate users when their
Gmail and Calendar API tokens are expired, revoked, or invalid.
"""

from __future__ import annotations

import sys
import os
from typing import Optional

# Add project root to path so imports work when running as script
if __name__ == "__main__":
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, project_root)

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

try:
    from ..databases.database import DatabaseManager
except ImportError:
    # Fallback for when running as a script
    from backend.databases.database import DatabaseManager

# Scopes for Gmail and Calendar access
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar"
]

# OAuth configuration
OAUTH_HOST = "localhost"
OAUTH_PORT = 8080  # Must match the authorized redirect URI in Google Cloud Console

# Database manager
db_manager = DatabaseManager()


def reauthenticate_user_token_failure(user_id: int) -> Optional[Credentials]:
    """
    Re-authenticate a user whose tokens have expired or been revoked.
    
    This function is triggered when token refresh fails or tokens are invalid.
    It will:
    1. Display a clear message to the user about why re-authentication is needed
    2. Open the browser for OAuth consent flow
    3. Save the new credentials to the database
    4. Return the new credentials
    
    Args:
        user_id: The database ID of the user to re-authenticate
        
    Returns:
        New Credentials object if successful, None if failed
    """
    print("\n" + "="*80)
    print("🔐 RE-AUTHENTICATION REQUIRED")
    print("="*80)
    print(f"User ID: {user_id}")
    print("\nYour Google OAuth tokens have expired or been revoked.")
    print("This can happen when:")
    print("  • Tokens have been inactive for 6+ months")
    print("  • You revoked access in your Google Account settings")
    print("  • API scopes have changed")
    print("\nYou need to sign in again to grant access to Gmail and Calendar.")
    print("="*80)
    
    try:
        print(f"\nRe-authenticating user ID: {user_id}")
        print("\n🌐 Opening browser for Google sign-in...")
        print("   Please complete the authorization in your browser.")
        print("   Make sure to:")
        print("   1. Sign in with the correct Google account")
        print("   2. Review and accept all requested permissions")
        print("   3. Wait for the success message before closing the browser")
        print("\n" + "-"*80)
        
        # Create OAuth flow
        flow = InstalledAppFlow.from_client_secrets_file(
            "credentials.json",
            SCOPES
        )
        
        # Run the OAuth flow with browser
        creds = flow.run_local_server(
            host=OAUTH_HOST,
            port=OAUTH_PORT,
            authorization_prompt_message="🔓 Opening browser for Google sign-in...",
            success_message="✅ Authorization successful! You may close this tab and return to the terminal.",
            open_browser=True,
            access_type='offline',  # Request refresh token
            prompt='consent'        # Force consent screen to ensure refresh token is issued
        )
        
        if not creds:
            print("\n❌ Failed to obtain credentials from OAuth flow.")
            return None
        
        # Verify we got a refresh token
        if not creds.refresh_token:
            print("\n⚠️  Warning: No refresh token received.")
            print("   This may cause issues in the future.")
            print("   Consider revoking app access in Google Account settings and trying again.")
        
        # Save the new credentials to the database
        db_manager.save_user_token(user_id, creds)
        
        print("\n" + "="*80)
        print("✅ RE-AUTHENTICATION SUCCESSFUL")
        print("="*80)
        print("New credentials have been saved to the database.")
        print("You can now access Gmail and Calendar APIs.")
        print("="*80 + "\n")
        
        return creds
        
    except KeyboardInterrupt:
        print("\n\n❌ Re-authentication cancelled by user.")
        print("   The application cannot continue without valid credentials.")
        return None
        
    except Exception as e:
        print(f"\n❌ Re-authentication failed: {e}")
        print("\nTroubleshooting:")
        print("  1. Check that credentials.json exists and is valid")
        print("  2. Verify http://localhost:8080/ is in authorized redirect URIs")
        print("  3. Ensure no other service is using port 8080")
        print("  4. Check your internet connection")
        print(f"\nError details: {type(e).__name__}: {e}")
        return None


def force_reauth_for_user(user_id: int) -> bool:
    """
    Manually trigger re-authentication for a specific user.
    
    This can be called directly to force a user to re-authenticate,
    even if their tokens appear valid.
    
    Args:
        user_id: The database ID of the user to re-authenticate
        
    Returns:
        True if successful, False otherwise
    """
    print(f"\n🔄 Forcing re-authentication for user ID: {user_id}")
    
    creds = reauthenticate_user_token_failure(user_id)
    return creds is not None


def reauth_all_users() -> dict:
    """
    Re-authenticate all users in the database.
    
    Useful for bulk re-authentication after scope changes or
    when multiple users have expired tokens.
    
    Returns:
        Dictionary with user_id as keys and success status as values
    """
    users = db_manager.get_all_users()
    
    if not users:
        print("❌ No users found in database.")
        return {}
    
    print(f"\n{'='*80}")
    print(f"🔄 RE-AUTHENTICATING {len(users)} USER(S)")
    print(f"{'='*80}\n")
    
    results = {}
    
    for i, user in enumerate(users, 1):
        print(f"\n[{i}/{len(users)}] Processing user: {user.email} (ID: {user.id})")
        
        try:
            creds = reauthenticate_user_token_failure(user.id)
            results[user.id] = creds is not None
            
            if creds:
                print(f"✅ Successfully re-authenticated {user.email}")
            else:
                print(f"❌ Failed to re-authenticate {user.email}")
                
        except Exception as e:
            print(f"❌ Error re-authenticating {user.email}: {e}")
            results[user.id] = False
    
    # Summary
    successful = sum(1 for success in results.values() if success)
    failed = len(results) - successful
    
    print(f"\n{'='*80}")
    print("📊 RE-AUTHENTICATION SUMMARY")
    print(f"{'='*80}")
    print(f"Total users:  {len(results)}")
    print(f"Successful:   {successful}")
    print(f"Failed:       {failed}")
    print(f"{'='*80}\n")
    
    return results


# Command-line interface for manual re-authentication
def main():
    """
    Command-line interface for re-authenticating users.
    
    Usage:
        python reauth_user.py              # Re-auth all users
        python reauth_user.py <user_id>    # Re-auth specific user
    """
    if len(sys.argv) > 1:
        # Re-authenticate specific user
        try:
            user_id = int(sys.argv[1])
            success = force_reauth_for_user(user_id)
            sys.exit(0 if success else 1)
        except ValueError:
            print(f"❌ Error: Invalid user ID '{sys.argv[1]}'. Must be an integer.")
            sys.exit(1)
    else:
        # Re-authenticate all users
        results = reauth_all_users()
        
        # Exit with error code if any re-auth failed
        if not all(results.values()):
            sys.exit(1)


if __name__ == "__main__":
    main()
