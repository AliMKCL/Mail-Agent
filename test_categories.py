#!/usr/bin/env python3
"""
Test script to verify Gmail category filtering works correctly.
"""

from database import DatabaseManager
from gmail_read import get_service, fetch_emails_by_category

def test_categories():
    db_manager = DatabaseManager()
    
    # Get all users
    users = db_manager.get_all_users()
    
    if not users:
        print("No users found. Please add a user first with: python add_user.py")
        return
    
    # Use first user for testing
    user = users[0]
    print(f"Testing with user: {user.email}")
    
    # Get Gmail service
    service = get_service(user.id)
    
    # Test different categories
    categories = ['primary', 'promotions', 'social', 'updates', 'all']
    
    for category in categories:
        print(f"\n--- Testing {category.upper()} category ---")
        try:
            ids = fetch_emails_by_category(service, category=category, max_results=10)
            print(f"Found {len(ids)} emails in {category} category")
            
            if ids:
                # Show first few message subjects
                from gmail_read import get_message_metadata
                for i, msg_id in enumerate(ids[:3], 1):
                    meta = get_message_metadata(service, msg_id)
                    subject = meta.get('Subject', 'No Subject')[:60]
                    sender = meta.get('From', 'Unknown')
                    print(f"  {i}. {subject} (from: {sender})")
                    
        except Exception as e:
            print(f"Error testing {category}: {e}")

if __name__ == "__main__":
    test_categories()
