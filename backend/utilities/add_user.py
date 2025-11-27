#!/usr/bin/env python3
"""
Script to add a new Gmail user account to the database.
This script will:
1. Prompt for email and name
2. Create user in database
3. Trigger OAuth flow to authenticate with Gmail
4. Store credentials in database
"""

import sys
import os

# Add project root to path so imports work when running as script
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.databases.database import DatabaseManager
from backend.services.gmail_read import get_service

def main():
    db_manager = DatabaseManager()
    
    print("Gmail Account Setup")
    print("=" * 50)
    
    # Get user details
    email = input("Enter Gmail address: ").strip()
    if not email:
        print("Email is required!")
        sys.exit(1)
    
    name = input("Enter display name (optional): ").strip()
    if not name:
        name = None
    
    try:
        # Create or get user
        user = db_manager.get_or_create_user(email, name)
        print(f"\nUser created/found: {user.email} (ID: {user.id})")
        
        # Trigger OAuth flow
        print("\nStarting OAuth flow...")
        print("Your browser will open for Gmail authentication.")
        print("Please sign in and authorize the application.")
        
        service = get_service(user.id)
        
        # Test the connection
        profile = service.users().getProfile(userId='me').execute()
        print(f"\nSuccess! Connected to Gmail account: {profile.get('emailAddress')}")
        print(f"Total messages in account: {profile.get('messagesTotal', 'Unknown')}")
        
        print(f"\nUser {email} has been successfully added to the system!")
        print("You can now use the web interface to view emails for this account.")
        
    except Exception as e:
        print(f"\nError setting up user: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
