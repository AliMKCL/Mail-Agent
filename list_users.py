#!/usr/bin/env python3
"""
Script to list all Gmail user accounts in the database.
"""

from database import DatabaseManager

def main():
    db_manager = DatabaseManager()
    
    print("Gmail Users in Database")
    print("=" * 50)
    
    users = db_manager.get_all_users()
    
    if not users:
        print("No users found in database.")
        print("\nTo add a user, run: python add_user.py")
        return
    
    print(f"Found {len(users)} user(s):")
    print()
    
    for user in users:
        print(f"ID: {user.id}")
        print(f"Email: {user.email}")
        print(f"Name: {user.name or 'Not set'}")
        print(f"Created: {user.created_at}")
        
        # Check if user has stored credentials
        creds = db_manager.get_user_credentials(user.id)
        if creds:
            print(f"OAuth Status: ✓ Authenticated")
        else:
            print(f"OAuth Status: ✗ Not authenticated")
        
        # Check email count
        emails = db_manager.get_user_emails(user.id, limit=1)
        email_count = len(db_manager.get_user_emails(user.id, limit=10000))  # Get all to count
        print(f"Stored Emails: {email_count}")
        
        print("-" * 30)

if __name__ == "__main__":
    main()
