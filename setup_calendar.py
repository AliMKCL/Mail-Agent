"""
Setup script for Google Calendar authentication
Run this script to authenticate your Gmail account for calendar access
"""

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from database import DatabaseManager

# Scopes needed for Gmail and Calendar access
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar'
]

def authenticate_calendar(user_id=1):
    """Authenticate user for Google Calendar access"""
    db_manager = DatabaseManager()
    creds = None
    
    # Check if we already have saved credentials in database
    creds = db_manager.get_user_credentials(user_id)

    # Check if credentials are valid and include the required scopes
    if creds and creds.valid:
        if 'https://www.googleapis.com/auth/calendar' not in creds.scopes:
            print("⚠️ Calendar scope missing. Re-authenticating...")
            creds = None  # Force re-authentication
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                print(f"🔄 Attempting to refresh expired token for user {user_id}...")
                creds.refresh(Request())
                print("✅ Credentials refreshed successfully!")
                # Save refreshed credentials back to database
                db_manager.save_user_token(user_id, creds)
            except Exception as e:
                print(f"❌ Error refreshing credentials: {e}")
                print("   Triggering re-authentication...")
                creds = None
        
        if not creds:
            if not os.path.exists('credentials.json'):
                print("❌ credentials.json file not found!")
                print("Please download it from Google Cloud Console and place it in this directory.")
                return False
            
            # Use the centralized re-authentication function
            from reauth_user import reauthenticate_user_token_failure
            creds = reauthenticate_user_token_failure(user_id)
            
            if not creds:
                print("❌ Re-authentication failed. Cannot proceed without valid credentials.")
                return False
                
            print(f"✅ Credentials saved to database for user ID {user_id}")
    
    return True

def get_calendar_service(user_id=None):
    """Get Google Calendar service for the user"""
    try:
        if user_id is None:
            return None, "User ID is required"
            
        db_manager = DatabaseManager()

        creds = db_manager.get_user_credentials(user_id)
        
        # Check if credentials exist and are valid
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    print(f"🔄 Attempting to refresh expired token for user {user_id}...")
                    creds.refresh(Request())
                    # Save refreshed credentials back to database
                    db_manager.save_user_token(user_id, creds)
                    print(f"✅ Token refreshed successfully for user {user_id}")
                except Exception as e:
                    print(f"❌ Error refreshing credentials for user {user_id}: {e}")
                    print("   Triggering re-authentication...")
                    
                    # Use centralized re-authentication
                    from reauth_user import reauthenticate_user_token_failure
                    creds = reauthenticate_user_token_failure(user_id)
                    
                    if not creds:
                        return None, "Re-authentication failed"
            else:
                print(f"⚠️  No valid credentials for user {user_id}")
                return None, "Authentication required"
        
        # Check if credentials include the Calendar scope
        if creds and creds.scopes:
            if 'https://www.googleapis.com/auth/calendar' not in creds.scopes:
                print(f"Calendar scope missing for user {user_id}. Current scopes: {creds.scopes}")
                return None, "Authentication required - Calendar scope missing"
        else:
            return None, "Authentication required - No scopes found"
        
        # Build calendar service
        service = build('calendar', 'v3', credentials=creds)
        return service, None
        
    except Exception as e:
        print(f"Error getting calendar service: {e}")
        return None, str(e)

def authenticate_google_calendar(user_id=None):
    """Initiate OAuth flow for Google Calendar"""
    try:
        flow = Flow.from_client_secrets_file('credentials.json', SCOPES)
        flow.redirect_uri = 'http://localhost:8080/oauth/callback'  # Updated to match setup port
        
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',  # Force consent to get refresh_token
            state=str(user_id) if user_id else 'default'
        )
        
        return auth_url, state
        
    except Exception as e:
        print(f"Error creating auth URL: {e}")
        return None, str(e)
    

def main():
    print("🔐 Google Calendar Authentication Setup")
    print("=" * 50)
    print("This script will authenticate your Google account for calendar access.")
    print("Make sure you have:")
    print("1. ✅ Google Calendar API enabled in Google Cloud Console")
    print("2. ✅ credentials.json file in this directory")
    print("3. ✅ OAuth consent screen configured")
    print("4. ✅ Redirect URI 'http://localhost:8080/' added to Google Cloud Console")
    print()
    
    # Ask for user ID (default to 1 for backward compatibility)
    user_input = input("Enter User ID (press Enter for default user ID 1): ").strip()
    user_id = int(user_input) if user_input.isdigit() else 1
    print(f"Using User ID: {user_id}")
    print()
    
    input("Press Enter to continue...")
    
    try:
        if authenticate_calendar(user_id):
            print()
            print("🎉 Setup completed successfully!")
            print("Calendar credentials have been saved to the database.")
            print("You can now run your FastAPI application and use the calendar features.")
            print()
            print("To start the application, run:")
            print("  python app.py")
        else:
            print()
            print("❌ Setup failed. Please check the requirements above.")
    
    except Exception as e:
        print(f"❌ Error during setup: {e}")
        print("Please make sure you have installed the required packages:")
        print("  pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client")

if __name__ == "__main__":
    main()
