#!/usr/bin/env python3
"""
Script to delete all calendar events on a specific day for an account.
Usage: python -m backend.utilities.delete_calendar_events <account_id> <date>
Example: python -m backend.utilities.delete_calendar_events 1 2026-02-10
"""

import sys
from datetime import datetime, timedelta
from backend.databases.database import DatabaseManager
from backend.services.setup_calendar import get_calendar_service

def get_primary_email_account_id(account_id: int) -> int:
    """Get the primary email account ID for an account"""
    db_manager = DatabaseManager()
    
    # Get all email accounts for this account
    email_accounts = db_manager.get_account_email_accounts(account_id)
    
    if not email_accounts:
        raise ValueError(f"No email accounts found for account_id {account_id}")
    
    # Find the primary one
    for ea in email_accounts:
        if ea.is_primary:
            return ea.id
    
    # If no primary found, return the first one
    return email_accounts[0].id

def delete_events_on_date(account_id: int, date_str: str):
    """
    Delete all calendar events on a specific date for an account.
    
    Args:
        account_id: The Account ID
        date_str: Date in YYYY-MM-DD format
    """
    try:
        # Parse the date
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # Get primary email account ID
        primary_email_account_id = get_primary_email_account_id(account_id)
        print(f"Using primary email account ID: {primary_email_account_id}")
        
        # Get calendar service
        service, error = get_calendar_service(primary_email_account_id)
        if not service:
            print(f"❌ Error: Failed to get calendar service: {error}")
            return
        
        # Calculate date range for the day (start and end of day in UTC)
        start_datetime = datetime.combine(target_date, datetime.min.time())
        end_datetime = datetime.combine(target_date, datetime.max.time())
        
        # Format for Google Calendar API (RFC3339 format)
        time_min = start_datetime.isoformat() + 'Z'
        time_max = end_datetime.isoformat() + 'Z'
        
        print(f"\n📅 Fetching events on {date_str}...")
        print(f"   Time range: {time_min} to {time_max}")
        
        # Fetch events for that day
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            maxResults=2500,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            print(f"✅ No events found on {date_str}")
            return
        
        print(f"\n📋 Found {len(events)} event(s) on {date_str}:")
        print("-" * 60)
        
        for i, event in enumerate(events, 1):
            title = event.get('summary', 'No Title')
            start = event['start'].get('dateTime', event['start'].get('date'))
            event_id = event.get('id')
            
            # Get category if exists
            category = None
            ext_props = event.get('extendedProperties', {})
            private_props = ext_props.get('private', {})
            if private_props and 'category' in private_props:
                category = private_props['category']
            
            print(f"{i}. {title}")
            print(f"   ID: {event_id}")
            print(f"   Start: {start}")
            if category:
                print(f"   Category: {category}")
            print()
        
        # Confirmation prompt
        print("-" * 60)
        response = input(f"⚠️  Are you sure you want to delete ALL {len(events)} event(s) on {date_str}? (yes/no): ")
        
        if response.lower() not in ['yes', 'y']:
            print("❌ Deletion cancelled.")
            return
        
        # Delete all events
        deleted_count = 0
        failed_count = 0
        
        print(f"\n🗑️  Deleting events...")
        for event in events:
            event_id = event.get('id')
            title = event.get('summary', 'No Title')
            
            try:
                service.events().delete(calendarId='primary', eventId=event_id).execute()
                print(f"   ✓ Deleted: {title}")
                deleted_count += 1
            except Exception as e:
                print(f"   ✗ Failed to delete {title}: {e}")
                failed_count += 1
        
        print("\n" + "=" * 60)
        print(f"✅ Successfully deleted {deleted_count} event(s)")
        if failed_count > 0:
            print(f"❌ Failed to delete {failed_count} event(s)")
        print("=" * 60)
        
    except ValueError as e:
        print(f"❌ Error: Invalid date format. Use YYYY-MM-DD (e.g., 2026-02-10)")
        print(f"   Details: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

def main():
    if len(sys.argv) != 3:
        print("Usage: python -m backend.utilities.delete_calendar_events <account_id> <date>")
        print("Example: python -m backend.utilities.delete_calendar_events 1 2026-02-10")
        sys.exit(1)
    
    try:
        account_id = int(sys.argv[1])
        date_str = sys.argv[2]
        
        print("=" * 60)
        print("🗑️  Delete Calendar Events on Specific Date")
        print("=" * 60)
        print(f"Account ID: {account_id}")
        print(f"Date: {date_str}")
        print()
        
        delete_events_on_date(account_id, date_str)
        
    except ValueError:
        print("❌ Error: account_id must be a number")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n❌ Operation cancelled by user.")
        sys.exit(1)

if __name__ == "__main__":
    main()

