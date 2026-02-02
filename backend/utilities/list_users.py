#!/usr/bin/env python3
"""
Script to list all Gmail email accounts in the database.
"""

from backend.databases.database import DatabaseManager

def main():
    db_manager = DatabaseManager()
    
    print("Gmail Email Accounts in Database")
    print("=" * 50)
    
    email_accounts = db_manager.get_all_email_accounts()
    
    if not email_accounts:
        print("No email accounts found in database.")
        print("\\nTo add an email account, sign up through the web app.")
        return
    
    print(f"Found {len(email_accounts)} email account(s):")
    print()
    
    for email_account in email_accounts:
        print(f"ID: {email_account.id}")
        print(f"Account ID: {email_account.account_id}")
        print(f"Email: {email_account.email}")
        print(f"Provider: {email_account.provider}")
        print(f"Is Primary: {'Yes' if email_account.is_primary else 'No'}")
        print(f"Created: {email_account.created_at}")
        
        # Check if email account has stored credentials
        creds = db_manager.get_email_account_credentials(email_account.id)
        if creds:
            print(f"OAuth Status: ✓ Authenticated")
        else:
            print(f"OAuth Status: ✗ Not authenticated")
        
        # Check email count
        emails = db_manager.get_email_account_emails(email_account.id, limit=1)
        email_count = len(db_manager.get_email_account_emails(email_account.id, limit=10000))  # Get all to count
        print(f"Stored Emails: {email_count}")
        
        print("-" * 30)

if __name__ == "__main__":
    main()
