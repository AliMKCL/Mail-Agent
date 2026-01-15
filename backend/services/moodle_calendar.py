"""
Service to fetch events from subscribed Moodle calendar.

IMPORTANT: The Moodle calendar must be subscribed to in the user's Google Calendar account.

This module:
1. Lists all calendars accessible to the user
2. Finds the Moodle calendar by name
3. Fetches events from the Moodle calendar
4. Provides formatted events for the API

Usage:
    python -m backend.services.moodle_calendar
    python -m backend.services.moodle_calendar --list-only  # Just list calendars
"""

import sys
import os
from datetime import datetime, timedelta

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.setup_calendar import get_calendar_service

# Default user ID
DEFAULT_USER_ID = 1

# Calendar name to search for
MOODLE_CALENDAR_NAME = "Moodle"


def list_all_calendars(user_id: int = DEFAULT_USER_ID):
    """
    List all calendars accessible to the user.
    Returns a list of calendar dictionaries with id, summary (name), and accessRole.
    """
    service, error = get_calendar_service(user_id)
    if not service:
        print(f"Error getting calendar service: {error}")
        return []

    try:
        calendar_list = service.calendarList().list().execute()
        calendars = calendar_list.get('items', [])

        print(f"\n{'='*60}")
        print(f"Found {len(calendars)} calendars for user {user_id}")
        print(f"{'='*60}\n")

        for cal in calendars:
            print(f"Name: {cal.get('summary', 'No name')}")
            print(f"  ID: {cal.get('id')}")
            print(f"  Access Role: {cal.get('accessRole')}")
            print(f"  Primary: {cal.get('primary', False)}")
            print()

        return calendars

    except Exception as e:
        print(f"Error listing calendars: {e}")
        return []


def find_moodle_calendar_id(user_id: int = DEFAULT_USER_ID, calendar_name: str = MOODLE_CALENDAR_NAME):
    """
    Find the Moodle calendar by name.
    Returns the calendar ID if found, None otherwise.
    """
    service, error = get_calendar_service(user_id)
    if not service:
        return None

    try:
        calendar_list = service.calendarList().list().execute()
        calendars = calendar_list.get('items', [])

        for cal in calendars:
            summary = cal.get('summary', '')
            if calendar_name.lower() in summary.lower():
                return cal.get('id')

        return None

    except Exception as e:
        print(f"Error finding calendar: {e}")
        return None


def fetch_moodle_events(user_id: int = DEFAULT_USER_ID,
                        calendar_id: str = None,
                        days_ahead: int = 90,
                        days_back: int = 30):
    """
    Fetch events from the Moodle calendar.

    Args:
        user_id: User ID for authentication
        calendar_id: The Moodle calendar ID. If None, will search for it.
        days_ahead: Number of days in the future to fetch
        days_back: Number of days in the past to fetch

    Returns:
        List of event dictionaries with normalized fields
    """
    service, error = get_calendar_service(user_id)
    if not service:
        print(f"Error getting calendar service: {error}")
        return []

    # Find Moodle calendar if ID not provided
    if not calendar_id:
        calendar_id = find_moodle_calendar_id(user_id)
        if not calendar_id:
            print(f"Could not find calendar '{MOODLE_CALENDAR_NAME}'")
            return []

    try:
        # Calculate time range
        now = datetime.utcnow()
        time_min = (now - timedelta(days=days_back)).isoformat() + 'Z'
        time_max = (now + timedelta(days=days_ahead)).isoformat() + 'Z'

        print(f"\nFetching events from {time_min[:10]} to {time_max[:10]}...")

        # Fetch events from the Moodle calendar
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime',
            maxResults=500
        ).execute()

        events = events_result.get('items', [])
        print(f"Found {len(events)} Moodle events\n")

        # Normalize events for our system
        normalized_events = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))

            # Parse date
            if 'T' in start:
                event_date = datetime.fromisoformat(start.replace('Z', '+00:00'))
                time_str = event_date.strftime('%I:%M %p')
            else:
                event_date = datetime.fromisoformat(start)
                time_str = 'All Day'

            date_key = event_date.strftime('%Y-%m-%d')

            normalized = {
                'id': event['id'],
                'title': event.get('summary', 'No Title'),
                'description': event.get('description', ''),
                'date': date_key,
                'time': time_str,
                'start': start,
                'end': end,
                'category': 'Moodle',
                'source': 'moodle',
                'calendar_id': calendar_id
            }
            normalized_events.append(normalized)

            print(f"  {date_key} | {time_str:12} | {normalized['title']}")

        return normalized_events

    except Exception as e:
        print(f"Error fetching Moodle events: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_moodle_events_for_api(user_id: int = DEFAULT_USER_ID,
                              start_date: str = None,
                              end_date: str = None):
    """
    Get Moodle events formatted for the API response.
    Called from app.py endpoints.

    Args:
        user_id: User ID for authentication
        start_date: ISO format start date (optional)
        end_date: ISO format end date (optional)

    Returns:
        Dictionary with events grouped by date
    """
    service, error = get_calendar_service(user_id)
    if not service:
        return {"error": error, "events": {}}

    # Find Moodle calendar
    calendar_id = find_moodle_calendar_id(user_id)
    if not calendar_id:
        return {"error": f"Calendar '{MOODLE_CALENDAR_NAME}' not found", "events": {}}

    
    if not start_date:
        # Display events from X months ago to now. 
        start_date = (datetime.now() - timedelta(days=180)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
    if not end_date:
        # End 2 years from now (730 days)
        end_date = (datetime.now() + timedelta(days=730)).replace(hour=23, minute=59, second=59).isoformat() + 'Z'

    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=start_date,
            timeMax=end_date,
            singleEvents=True,
            orderBy='startTime',
            maxResults=500
        ).execute()

        events = events_result.get('items', [])

        # Format events grouped by date
        formatted_events = {}
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))

            if 'T' in start:
                event_date = datetime.fromisoformat(start.replace('Z', '+00:00'))
                time_str = event_date.strftime('%I:%M %p')
            else:
                event_date = datetime.fromisoformat(start)
                time_str = 'All Day'

            date_key = event_date.strftime('%Y-%m-%d')

            if date_key not in formatted_events:
                formatted_events[date_key] = []

            formatted_events[date_key].append({
                'id': event['id'],
                'title': event.get('summary', 'No Title'),
                'category': 'Moodle',
                'time': time_str,
                'description': event.get('description', ''),
                'start': start,
                'end': event['end'].get('dateTime', event['end'].get('date')),
                'source': 'moodle'
            })

        return {
            "status": "success",
            "events": formatted_events,
            "calendar_id": calendar_id,
            "count": len(events)
        }

    except Exception as e:
        return {"error": str(e), "events": {}}


def main():
    """Main function to run the script"""
    import argparse

    parser = argparse.ArgumentParser(description='Fetch Moodle calendar events')
    parser.add_argument('--list-only', action='store_true',
                        help='Only list calendars, do not fetch events')
    parser.add_argument('--user-id', type=int, default=DEFAULT_USER_ID,
                        help=f'User ID (default: {DEFAULT_USER_ID})')
    parser.add_argument('--days-ahead', type=int, default=90,
                        help='Days ahead to fetch (default: 90)')
    parser.add_argument('--days-back', type=int, default=30,
                        help='Days back to fetch (default: 30)')

    args = parser.parse_args()

    print("=" * 60)
    print("Moodle Calendar Fetcher")
    print("=" * 60)

    if args.list_only:
        list_all_calendars(args.user_id)
    else:
        events = fetch_moodle_events(
            user_id=args.user_id,
            days_ahead=args.days_ahead,
            days_back=args.days_back
        )

        if events:
            print(f"\nSuccessfully fetched {len(events)} Moodle events")


if __name__ == "__main__":
    main()
