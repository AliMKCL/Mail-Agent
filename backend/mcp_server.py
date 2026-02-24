"""
MCP Server for Gmail Calendar Agent - Updated for new database schema

Exposes email and calendar functions as MCP tools for LLM integration

Updated for new database schema:
- Account: The logged-in user (account_id from accounts table)
- EmailAccount: A connected Gmail/Outlook account (email_account_id from email_accounts table)
- Email: Email messages linked to EmailAccounts
"""

# Run the testing UI env: npx @modelcontextprotocol/inspector python backend/mcp_server2.py

import sys
import os

# Ensure the project root is in the path for imports to work
# This allows the file to run both as a module and directly
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import json
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta

from fastmcp import FastMCP

# Import existing backend components (updated for new schema)
from backend.databases.database import DatabaseManager, Account, EmailAccount, Email
from backend.services.gmail_read import get_service, list_message_ids, prepare_email_data
from backend.services.setup_calendar import get_calendar_service
from backend.services.moodle_calendar import get_moodle_events_for_api
from backend.databases.vector_database import query_vector_db, embed_and_store
from backend.utilities.ask_ollama import slm_response, llm_response
from backend.utilities.clean_mails import clean_email

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("Mail Agent")

# Initialize database manager
db_manager = DatabaseManager()

# Constants - Updated for new schema
CALENDAR_EMAIL_ACCOUNT_ID = 1  # Calendar operations use email_account ID 1 (main email account)

# Global context - only for tracking, not enforcing user restrictions
# Email operations can work across all email accounts (LLM can specify email_account_id in queries)
# Calendar operations always use CALENDAR_EMAIL_ACCOUNT_ID
context = {
    "last_sync_time": None
}


# Helper function to save credentials after calendar operations
def save_calendar_credentials_after_use(service, email_account_id):
    """
    Save potentially refreshed credentials after calendar service use.
    Google's library may auto-refresh tokens during API calls.
    """
    try:
        if hasattr(service, '_http') and hasattr(service._http, 'credentials'):
            creds = service._http.credentials
            if creds:
                db_manager.save_email_token(email_account_id, creds)
                logger.debug(f"Saved potentially refreshed credentials for email account {email_account_id}")
    except Exception as e:
        logger.warning(f"Could not save credentials after calendar operation: {e}")


# ============================================================================
# MCP TOOLS - Functions the LLM can execute
# ============================================================================

# --- Account Management Tools ---

@mcp.tool()
async def list_accounts() -> dict:
    """
    List all registered accounts in the system.
    Returns account information including id, primary_email for each account.
    """
    try:
        accounts = db_manager.get_all_accounts()
        account_list = [
            {
                "id": account.id,
                "primary_email": account.primary_email,
                "created_at": account.created_at.isoformat() if account.created_at else None
            }
            for account in accounts
        ]
        logger.info(f"Listed {len(account_list)} accounts")
        return {
            "status": "success",
            "accounts": account_list,
            "count": len(account_list)
        }
    except Exception as e:
        logger.error(f"Error listing accounts: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def list_email_accounts(account_id: Optional[int] = None) -> dict:
    """
    List all email accounts (Gmail/Outlook) in the system.
    
    Args:
        account_id: Optional - if provided, only returns email accounts for this account
    
    Returns account information including id, email, provider, account_id.
    """
    try:
        if account_id is not None:
            email_accounts = db_manager.get_account_email_accounts(account_id)
        else:
            email_accounts = db_manager.get_all_email_accounts()
            
        email_account_list = [
            {
                "id": ea.id,
                "email": ea.email,
                "provider": ea.provider,
                "account_id": ea.account_id,
                "is_primary": bool(ea.is_primary)
            }
            for ea in email_accounts
        ]
        logger.info(f"Listed {len(email_account_list)} email accounts")
        return {
            "status": "success",
            "email_accounts": email_account_list,
            "count": len(email_account_list)
        }
    except Exception as e:
        logger.error(f"Error listing email accounts: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def get_account_info(account_id: int) -> dict:
    """
    Get detailed information about a specific account by their ID.

    Args:
        account_id: The ID of the account to retrieve
    """
    try:
        with db_manager.get_session() as session:
            account = session.query(Account).filter_by(id=account_id).first()
            if not account:
                return {"status": "error", "error": f"Account with ID {account_id} not found"}

            # Get email accounts for this account
            email_accounts = session.query(EmailAccount).filter_by(account_id=account_id).all()
            
            return {
                "status": "success",
                "account": {
                    "id": account.id,
                    "primary_email": account.primary_email,
                    "created_at": account.created_at.isoformat() if account.created_at else None,
                    "email_accounts_count": len(email_accounts),
                    "email_accounts": [
                        {
                            "id": ea.id,
                            "email": ea.email,
                            "provider": ea.provider,
                            "is_primary": bool(ea.is_primary)
                        }
                        for ea in email_accounts
                    ]
                }
            }
    except Exception as e:
        logger.error(f"Error getting account info: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def get_email_account_info(email_account_id: int) -> dict:
    """
    Get detailed information about a specific email account by ID.

    Args:
        email_account_id: The ID of the email account to retrieve
    """
    try:
        with db_manager.get_session() as session:
            email_account = session.query(EmailAccount).filter_by(id=email_account_id).first()
            if not email_account:
                return {"status": "error", "error": f"Email account with ID {email_account_id} not found"}

            # Get parent account
            account = session.query(Account).filter_by(id=email_account.account_id).first()
            
            # Count emails for this email account
            email_count = session.query(Email).filter_by(email_account_id=email_account_id).count()

            return {
                "status": "success",
                "email_account": {
                    "id": email_account.id,
                    "email": email_account.email,
                    "provider": email_account.provider,
                    "account_id": email_account.account_id,
                    "is_primary": bool(email_account.is_primary),
                    "account_primary_email": account.primary_email if account else None,
                    "email_count": email_count,
                    "created_at": email_account.created_at.isoformat() if email_account.created_at else None
                }
            }
    except Exception as e:
        logger.error(f"Error getting email account info: {e}")
        return {"status": "error", "error": str(e)}


# --- Email Tools ---

@mcp.tool()
async def search_emails(query: str, email_account_id: Optional[int] = None, use_semantic: bool = False, limit: int = 10) -> dict:
    """
    Search emails using Gmail query syntax or semantic search across the vector database.

    Args:
        query: Search query (Gmail syntax like "from:example@gmail.com" or natural language for semantic search)
        email_account_id: Optional email account ID to filter emails. If not provided, searches across all email accounts
        use_semantic: If True, uses vector database semantic search; if False, uses Gmail API search
        limit: Maximum number of results to return (default: 10)

    Examples:
        - search_emails("deadline", email_account_id=2, use_semantic=True)
        - search_emails("from:professor@university.edu", email_account_id=1)
    """
    try:
        if use_semantic:
            # Use vector database for semantic search
            logger.info(f"Semantic search: '{query}' (limit: {limit})")
            results = await query_vector_db(query, top_k=limit)

            email_results = []
            for doc in results:
                result = {
                    "message_id": doc.metadata.get("message_id"),
                    "sender": doc.metadata.get("sender"),
                    "subject": doc.metadata.get("subject"),
                    "date": doc.metadata.get("date_sent"),
                    "snippet": doc.page_content[:200] if len(doc.page_content) > 200 else doc.page_content
                }
                # Filter by email_account_id if specified
                if email_account_id is not None:
                    # Get full email to check email_account_id
                    with db_manager.get_session() as session:
                        email = session.query(Email).filter_by(message_id=result["message_id"]).first()
                        if email and email.email_account_id == email_account_id:
                            email_results.append(result)
                else:
                    email_results.append(result)

            return {
                "status": "success",
                "results": email_results,
                "count": len(email_results),
                "search_type": "semantic"
            }
        else:
            # Use Gmail API search - requires email_account_id
            if email_account_id is None:
                return {"status": "error", "error": "email_account_id required for Gmail API search"}

            logger.info(f"Gmail API search for email account {email_account_id}: '{query}'")
            service = get_service(email_account_id)
            ids = list_message_ids(service, query=query, max_results=limit)

            # Get email details from database
            with db_manager.get_session() as session:
                emails = session.query(Email).filter(
                    Email.email_account_id == email_account_id,
                    Email.message_id.in_(ids)
                ).limit(limit).all()

                email_results = [
                    {
                        "message_id": email.message_id,
                        "sender": email.sender,
                        "subject": email.subject,
                        "date": email.date_sent.isoformat() if email.date_sent else None,
                        "snippet": email.snippet
                    }
                    for email in emails
                ]

            return {
                "status": "success",
                "results": email_results,
                "count": len(email_results),
                "search_type": "gmail_api"
            }
    except Exception as e:
        logger.error(f"Error searching emails: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def sync_emails(email_account_id: int, max_results: int = 50) -> dict:
    """
    Fetch new emails from Gmail for a specific email account and store them in the database and vector store.

    Args:
        email_account_id: The ID of the email account whose emails to sync
        max_results: Maximum number of emails to fetch (default: 50)

    Returns:
        Status of sync operation including counts of fetched and new emails
    """
    try:
        logger.info(f"Syncing emails for email account {email_account_id}, max_results: {max_results}")

        # Get Gmail service
        service = get_service(email_account_id)

        # Fetch message IDs from Gmail
        ids = list_message_ids(service, query="in:inbox category:primary", max_results=max_results)

        if not ids:
            return {
                "status": "success",
                "message": "No new emails found",
                "total_fetched": 0,
                "new_emails": 0
            }

        # Prepare email data
        email_data = prepare_email_data(service, ids)

        # Save to database
        saved_emails = db_manager.save_emails(email_account_id, email_data)

        # Clean and prepare emails for embedding
        emails_for_embedding = []
        for email in email_data:
            cleaned_content = clean_email(email.get('body_text', ''), email.get('body_html'))
            emails_for_embedding.append({
                'message_id': email.get('message_id'),
                'sender': email.get('sender'),
                'subject': email.get('subject'),
                'date_sent': email.get('date_sent'),
                'body_text': cleaned_content
            })

        # Embed in vector database
        if emails_for_embedding:
            await embed_and_store(emails_for_embedding)

        context["last_sync_time"] = datetime.now().isoformat()

        logger.info(f"Sync complete: {len(saved_emails)} new emails")
        return {
            "status": "success",
            "total_fetched": len(ids),
            "new_emails": len(saved_emails),
            "last_sync": context["last_sync_time"]
        }
    except Exception as e:
        logger.error(f"Error syncing emails: {e}")
        return {"status": "error", "error": str(e)}

# Uses NORMAL DB
@mcp.tool()
async def get_email_details(message_id: str) -> dict:
    """
    Get full details of a specific email by its message ID.

    Args:
        message_id: The Gmail message ID of the email

    Returns:
        Complete email details including subject, sender, body, etc.
    """
    try:
        logger.info(f"Getting email details for: {message_id}")

        with db_manager.get_session() as session:
            email = session.query(Email).filter_by(message_id=message_id).first()

            if not email:
                return {"status": "error", "error": f"Email with message_id {message_id} not found"}

            return {
                "status": "success",
                "email": {
                    "message_id": email.message_id,
                    "thread_id": email.thread_id,
                    "subject": email.subject,
                    "sender": email.sender,
                    "recipient": email.recipient,
                    "date_sent": email.date_sent.isoformat() if email.date_sent else None,
                    "snippet": email.snippet,
                    "body_text": email.body_text,
                    "body_html": email.body_html,
                    "email_account_id": email.email_account_id
                }
            }
    except Exception as e:
        logger.error(f"Error getting email details: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def get_email_account_emails(email_account_id: int, limit: int = 50) -> dict:
    """
    Get cached emails for a specific email account from the database.

    Args:
        email_account_id: The ID of the email account whose emails to retrieve
        limit: Maximum number of emails to return (default: 50)

    Returns:
        List of emails for the specified email account
    """
    try:
        logger.info(f"Getting {limit} emails for email account {email_account_id}")

        emails = db_manager.get_email_account_emails(email_account_id, limit=limit)

        email_list = [
            {
                "message_id": email.message_id,
                "subject": email.subject,
                "sender": email.sender,
                "recipient": email.recipient,
                "date_sent": email.date_sent.isoformat() if email.date_sent else None,
                "snippet": email.snippet
            }
            for email in emails
        ]

        return {
            "status": "success",
            "emails": email_list,
            "count": len(email_list),
            "email_account_id": email_account_id
        }
    except Exception as e:
        logger.error(f"Error getting email account emails: {e}")
        return {"status": "error", "error": str(e)}


# --- Calendar Tools ---

@mcp.tool()
async def create_calendar_event(
    title: str,
    date: str,
    time: str = "All Day",
    description: str = "",
    category: Optional[str] = None
) -> dict:
    """
    Create a new calendar event. Always uses the main calendar email account (email account ID 1).

    Args:
        title: Event title/summary
        date: Event date in YYYY-MM-DD format
        time: Event time in "HH:MM AM/PM" format, or "All Day" for all-day events (default: "All Day")
        description: Event description/details (optional)
        category: Event category - one of: Academic, Career, Social, Deadline (optional)

    Examples:
        - create_calendar_event("Team Meeting", "2025-03-15", "10:00 AM", "Discuss project updates")
        - create_calendar_event("Assignment Due", "2025-03-20", category="Deadline")
    """
    try:
        logger.info(f"Creating calendar event: {title} on {date}")

        # Get calendar service (always uses CALENDAR_EMAIL_ACCOUNT_ID)
        service, error = get_calendar_service(CALENDAR_EMAIL_ACCOUNT_ID)
        if not service:
            return {"status": "error", "error": error}

        # Parse date and time
        event = {}
        if time and time != 'All Day':
            try:
                time_obj = datetime.strptime(time, '%I:%M %p').time()
                start_datetime = datetime.combine(datetime.fromisoformat(date).date(), time_obj)
                end_datetime = start_datetime + timedelta(hours=1)

                event = {
                    'summary': title,
                    'description': description,
                    'start': {'dateTime': start_datetime.isoformat(), 'timeZone': 'UTC'},
                    'end': {'dateTime': end_datetime.isoformat(), 'timeZone': 'UTC'},
                }
            except ValueError:
                # If time parsing fails, create all-day event
                event = {
                    'summary': title,
                    'description': description,
                    'start': {'date': date},
                    'end': {'date': date},
                }
        else:
            # All-day event
            event = {
                'summary': title,
                'description': description,
                'start': {'date': date},
                'end': {'date': date},
            }

        # Add category if provided
        if category:
            event['extendedProperties'] = {'private': {'category': category}}

        # Create the event
        created_event = service.events().insert(calendarId='primary', body=event).execute()

        # Save credentials in case they were refreshed during the API call
        save_calendar_credentials_after_use(service, CALENDAR_EMAIL_ACCOUNT_ID)

        logger.info(f"Event created: {created_event['id']}")
        return {
            "status": "success",
            "event_id": created_event['id'],
            "event_link": created_event.get('htmlLink'),
            "title": title,
            "date": date
        }
    except Exception as e:
        logger.error(f"Error creating calendar event: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def update_calendar_event(
    event_id: str,
    title: Optional[str] = None,
    date: Optional[str] = None,
    time: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None
) -> dict:
    """
    Update an existing calendar event. Always uses the main calendar email account (email account ID 1).

    Args:
        event_id: The Google Calendar event ID
        title: New event title (optional)
        date: New date in YYYY-MM-DD format (optional)
        time: New time in "HH:MM AM/PM" format (optional)
        description: New description (optional)
        category: New category (optional)

    Returns:
        Status and updated event link
    """
    try:
        logger.info(f"Updating calendar event: {event_id}")

        # Get calendar service (always uses CALENDAR_EMAIL_ACCOUNT_ID)
        service, error = get_calendar_service(CALENDAR_EMAIL_ACCOUNT_ID)
        if not service:
            return {"status": "error", "error": error}

        # Get existing event
        event = service.events().get(calendarId='primary', eventId=event_id).execute()

        # Update fields if provided
        if title:
            event['summary'] = title
        if description is not None:
            event['description'] = description
        if date or time:
            if time and time != 'All Day':
                try:
                    time_obj = datetime.strptime(time, '%I:%M %p').time()
                    event_date = date if date else event['start'].get('date', event['start'].get('dateTime')[:10])
                    start_datetime = datetime.combine(datetime.fromisoformat(event_date).date(), time_obj)
                    end_datetime = start_datetime + timedelta(hours=1)

                    event['start'] = {'dateTime': start_datetime.isoformat(), 'timeZone': 'UTC'}
                    event['end'] = {'dateTime': end_datetime.isoformat(), 'timeZone': 'UTC'}
                except ValueError:
                    pass
            elif date:
                event['start'] = {'date': date}
                event['end'] = {'date': date}

        if category:
            if 'extendedProperties' not in event:
                event['extendedProperties'] = {'private': {}}
            event['extendedProperties']['private']['category'] = category

        # Update the event
        updated_event = service.events().update(calendarId='primary', eventId=event_id, body=event).execute()

        # Save credentials in case they were refreshed during the API call
        save_calendar_credentials_after_use(service, CALENDAR_EMAIL_ACCOUNT_ID)

        logger.info(f"Event updated: {event_id}")
        return {
            "status": "success",
            "event_id": updated_event['id'],
            "event_link": updated_event.get('htmlLink')
        }
    except Exception as e:
        logger.error(f"Error updating calendar event: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def delete_calendar_event(event_id: str) -> dict:
    """
    Delete a calendar event. Always uses the main calendar email account (email account ID 1).

    Args:
        event_id: The Google Calendar event ID to delete

    Returns:
        Status of deletion operation
    """
    try:
        logger.info(f"Deleting calendar event: {event_id}")

        # Get calendar service (always uses CALENDAR_EMAIL_ACCOUNT_ID)
        service, error = get_calendar_service(CALENDAR_EMAIL_ACCOUNT_ID)
        if not service:
            return {"status": "error", "error": error}

        # Delete the event
        service.events().delete(calendarId='primary', eventId=event_id).execute()

        # Save credentials in case they were refreshed during the API call
        save_calendar_credentials_after_use(service, CALENDAR_EMAIL_ACCOUNT_ID)

        logger.info(f"Event deleted: {event_id}")
        return {
            "status": "success",
            "message": f"Event {event_id} deleted successfully"
        }
    except Exception as e:
        logger.error(f"Error deleting calendar event: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def get_calendar_events(start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict:
    """
    Get calendar events for a date range from ALL calendars (primary + Moodle).
    Always uses the main calendar email account (email account ID 1).
    If no dates provided, returns events for the current month.

    Args:
        start_date: Start date in YYYY-MM-DD format (optional, defaults to start of current month)
        end_date: End date in YYYY-MM-DD format (optional, defaults to end of current month)

    Returns:
        List of events in the specified date range from all calendars
    """
    try:
        # Get calendar service (always uses CALENDAR_EMAIL_ACCOUNT_ID)
        service, error = get_calendar_service(CALENDAR_EMAIL_ACCOUNT_ID)
        if not service:
            return {"status": "error", "error": error}

        # Set default date range to current month if not provided
        if not start_date:
            start_date_str = datetime.now().replace(day=1, hour=0, minute=0, second=0).isoformat() + 'Z'
            start_date_plain = datetime.now().replace(day=1, hour=0, minute=0, second=0).strftime('%Y-%m-%d')
        else:
            start_date_str = datetime.fromisoformat(start_date).isoformat() + 'Z'
            start_date_plain = start_date

        if not end_date:
            next_month = datetime.now().replace(day=28) + timedelta(days=4)
            last_day = next_month - timedelta(days=next_month.day)
            end_date_str = last_day.replace(hour=23, minute=59, second=59).isoformat() + 'Z'
            end_date_plain = last_day.strftime('%Y-%m-%d')
        else:
            # Add 1 day to end_date because Google Calendar API's timeMax is EXCLUSIVE
            # So to include events ON the end_date, we need to query up to the next day
            end_date_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
            end_date_str = end_date_dt.isoformat() + 'Z'
            end_date_plain = end_date

        logger.info(f"Getting calendar events from {start_date_str} to {end_date_str}")

        # Fetch events from primary calendar
        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_date_str,
            timeMax=end_date_str,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        primary_events = events_result.get('items', [])

        # Format primary calendar events
        formatted_events = []
        for event in primary_events:
            formatted_event = {
                "id": event['id'],
                "title": event.get('summary', 'No Title'),
                "start": event['start'].get('dateTime', event['start'].get('date')),
                "end": event['end'].get('dateTime', event['end'].get('date')),
                "description": event.get('description', ''),
                "link": event.get('htmlLink'),
                "source": "primary"
            }

            # Add category if exists
            if 'extendedProperties' in event and 'private' in event['extendedProperties']:
                formatted_event['category'] = event['extendedProperties']['private'].get('category')

            formatted_events.append(formatted_event)

        # Fetch Moodle events and merge them
        try:
            # Pass the ISO formatted dates with 'Z' suffix (same format as primary calendar)
            moodle_result = get_moodle_events_for_api(
                email_account_id=CALENDAR_EMAIL_ACCOUNT_ID,
                start_date=start_date_str,  # Use the 'Z' formatted version
                end_date=end_date_str       # Use the 'Z' formatted version
            )

            # Extract Moodle events from the grouped format
            if 'events' in moodle_result and isinstance(moodle_result['events'], dict):
                moodle_count = 0
                for date_key, events_on_date in moodle_result['events'].items():
                    for event in events_on_date:
                        formatted_events.append({
                            "id": event.get('id', 'moodle-' + str(event.get('title', ''))),
                            "title": event.get('title', 'No Title'),
                            "start": event.get('start', ''),
                            "end": event.get('end', ''),
                            "description": event.get('description', ''),
                            "link": event.get('link', ''),
                            "category": "Moodle",
                            "source": "moodle"
                        })
                        moodle_count += 1
                logger.info(f"Added {moodle_count} Moodle events")
        except Exception as moodle_error:
            logger.warning(f"Could not fetch Moodle events: {moodle_error}")
            # Continue without Moodle events - don't fail the entire request

        # Sort all events by start time
        formatted_events.sort(key=lambda e: e['start'])

        # Save credentials in case they were refreshed during the API call
        save_calendar_credentials_after_use(service, CALENDAR_EMAIL_ACCOUNT_ID)

        return {
            "status": "success",
            "events": formatted_events,
            "count": len(formatted_events),
            "primary_count": len(primary_events),
            "sources": ["primary", "moodle"]
        }
    except Exception as e:
        logger.error(f"Error getting calendar events: {e}")
        return {"status": "error", "error": str(e)}


# --- AI-Enhanced Tools ---

@mcp.tool()
async def extract_dates_from_emails(email_account_id: int, limit: int = 20, auto_create_events: bool = False) -> dict:
    """
    Extract deadlines and important dates from recent emails using LLM.
    Optionally create calendar events automatically.

    Args:
        email_account_id: The ID of the email account whose emails to analyze
        limit: Number of recent emails to analyze (default: 20)
        auto_create_events: If True, automatically creates calendar events for found dates (default: False)

    Returns:
        Extracted dates and optionally created event IDs
    """
    try:
        logger.info(f"Extracting dates from {limit} emails for email account {email_account_id}")

        # Get recent emails
        emails = db_manager.get_email_account_emails(email_account_id, limit=limit)

        if not emails:
            return {"status": "success", "extracted_dates": [], "message": "No emails found"}

        # Build email text for LLM
        email_texts = []
        for email in emails:
            email_text = f"Subject: {email.subject}\nFrom: {email.sender}\nDate: {email.date_sent}\n"
            if email.body_text:
                email_text += f"Body: {email.body_text[:500]}\n"
            email_texts.append(email_text)

        # Get current date context
        now = datetime.now()
        current_date = now.strftime("%B %d, %Y")
        current_year = now.year
        is_end_of_year = now.month >= 11

        # Create prompt for LLM with date context
        prompt = f"""TODAY'S DATE: {current_date}

IMPORTANT: When you see dates without years in the emails:
1. If the month hasn't passed yet this year, assume it's {current_year}
2. If the month has already passed this year, assume it's {current_year + 1}
{"3. Since it's near end of year, months like January, February, March likely refer to " + str(current_year + 1) if is_end_of_year else ""}
4. For relative dates like "next week", "this Friday", calculate from today: {current_date}

Extract all dates, deadlines, and time-sensitive information from these emails.
For each date found, provide:
- date: in YYYY-MM-DD format (MUST include the year based on rules above)
- description: what the deadline/event is about
- email_subject: the subject of the email it came from

Format your response as a JSON array like this:
[{{"date": "2025-03-15", "description": "CS101 Assignment due", "email_subject": "Assignment 3"}}]

Only include actual deadlines and important dates. Skip general references to dates.

Emails:
{chr(10).join(email_texts)}

Respond with ONLY the JSON array, no other text.
"""

        # Call LLM
        response = llm_response(prompt)

        # Try to parse JSON response
        try:
            # Clean up response - remove markdown code blocks if present
            clean_response = response.strip()
            if clean_response.startswith("```"):
                clean_response = clean_response.split("```")[1]
                if clean_response.startswith("json"):
                    clean_response = clean_response[4:]
            clean_response = clean_response.strip()

            dates = json.loads(clean_response)

            # Optionally create calendar events
            created_events = []
            if auto_create_events and dates:
                logger.info(f"Auto-creating {len(dates)} calendar events")
                for item in dates:
                    result = await create_calendar_event(
                        title=item.get('description', 'Deadline'),
                        date=item['date'],
                        description=f"From email: {item.get('email_subject', 'Unknown')}",
                        category="Deadline"
                    )
                    if result.get('status') == 'success':
                        created_events.append(result['event_id'])

            return {
                "status": "success",
                "extracted_dates": dates,
                "count": len(dates),
                "created_events": created_events if auto_create_events else None
            }
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return {
                "status": "error",
                "error": "Failed to parse dates from LLM response",
                "raw_response": response
            }
    except Exception as e:
        logger.error(f"Error extracting dates: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def summarize_emails(query: str = "unread", email_account_id: Optional[int] = None, summary_type: str = "brief") -> dict:
    """
    Generate an AI summary of emails matching specific criteria.

    Args:
        query: Filter criteria - Gmail query syntax (default: "unread")
        email_account_id: Optional email account ID to filter emails (if not provided, uses semantic search across all)
        summary_type: Type of summary - "brief", "detailed", or "bullet_points" (default: "brief")

    Returns:
        AI-generated summary of matching emails
    """
    try:
        logger.info(f"Summarizing emails with query: {query}, type: {summary_type}")

        # Search for emails
        search_result = await search_emails(query=query, email_account_id=email_account_id, use_semantic=True, limit=20)

        if search_result.get('status') != 'success' or not search_result.get('results'):
            return {"status": "success", "summary": "No emails found matching the criteria."}

        emails = search_result['results']

        # Get current date context
        now = datetime.now()
        current_date = now.strftime("%B %d, %Y")
        current_year = now.year

        # Build context for LLM
        email_context = []
        for email in emails:
            email_context.append(
                f"From: {email['sender']}\n"
                f"Subject: {email['subject']}\n"
                f"Date: {email['date']}\n"
                f"Content: {email['snippet']}\n"
            )

        # Create prompt based on summary type
        date_context = f"TODAY'S DATE: {current_date}\nWhen mentioning dates or deadlines in your summary, interpret relative dates based on today's date.\n\n"

        if summary_type == "bullet_points":
            prompt = date_context + f"""Summarize these emails as a bullet-point list.
Group by category (work, academic, personal, etc.) if applicable.
If any deadlines are mentioned, include them with full dates (YYYY-MM-DD).

Emails:
{chr(10).join(email_context)}

Provide a concise bullet-point summary:
"""
        elif summary_type == "detailed":
            prompt = date_context + f"""Provide a detailed summary of these emails.
Include key information, action items, and any deadlines mentioned.
For deadlines without years, infer the year based on today's date ({current_date}).

Emails:
{chr(10).join(email_context)}

Detailed summary:
"""
        else:  # brief
            prompt = date_context + f"""Provide a brief summary of these emails in 2-3 sentences.
Focus on the most important information, especially any upcoming deadlines.

Emails:
{chr(10).join(email_context)}

Brief summary:
"""

        # Generate summary
        summary = llm_response(prompt)

        return {
            "status": "success",
            "summary": summary,
            "email_count": len(emails),
            "summary_type": summary_type
        }
    except Exception as e:
        logger.error(f"Error summarizing emails: {e}")
        return {"status": "error", "error": str(e)}


# ============================================================================
# MCP RESOURCES - Read-only data sources
# ============================================================================

@mcp.resource("mail://inbox/{email_account_id}")
async def get_inbox_resource(email_account_id: int) -> str:
    """
    Resource: Get inbox emails for a specific email account.

    URI: mail://inbox/{email_account_id}
    Example: mail://inbox/1

    Returns JSON string with list of emails for the specified email account.
    """
    try:
        emails = db_manager.get_email_account_emails(int(email_account_id), limit=50)

        email_list = [
            {
                "message_id": email.message_id,
                "subject": email.subject,
                "sender": email.sender,
                "date": email.date_sent.isoformat() if email.date_sent else None,
                "snippet": email.snippet
            }
            for email in emails
        ]

        return json.dumps({
            "email_account_id": int(email_account_id),
            "emails": email_list,
            "count": len(email_list)
        }, indent=2)
    except Exception as e:
        logger.error(f"Error getting inbox resource: {e}")
        return json.dumps({"error": str(e)})


@mcp.resource("mail://email/{message_id}")
async def get_email_resource(message_id: str) -> str:
    """
    Resource: Get full details of a specific email by message ID.

    URI: mail://email/{message_id}
    Example: mail://email/18f3a2b4c5d6e7f8

    Returns JSON string with complete email details.
    """
    try:
        with db_manager.get_session() as session:
            email = session.query(Email).filter_by(message_id=message_id).first()

            if not email:
                return json.dumps({"error": f"Email with message_id {message_id} not found"})

            email_data = {
                "message_id": email.message_id,
                "thread_id": email.thread_id,
                "subject": email.subject,
                "sender": email.sender,
                "recipient": email.recipient,
                "date_sent": email.date_sent.isoformat() if email.date_sent else None,
                "snippet": email.snippet,
                "body_text": email.body_text,
                "body_html": email.body_html,
                "email_account_id": email.email_account_id
            }

            return json.dumps(email_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting email resource: {e}")
        return json.dumps({"error": str(e)})


@mcp.resource("calendar://events")
async def get_calendar_events_resource() -> str:
    """
    Resource: Get calendar events for the current month from ALL calendars (primary + Moodle).

    URI: calendar://events

    Returns JSON string with list of events from all calendars (email account ID 1).
    """
    try:
        # Use the tool to get all events (which already merges primary + Moodle)
        result = await get_calendar_events()

        if result.get("status") == "error":
            return json.dumps({"error": result.get("error")})

        return json.dumps({
            "events": result.get("events", []),
            "count": result.get("count", 0),
            "primary_count": result.get("primary_count", 0),
            "sources": result.get("sources", []),
            "month": datetime.now().strftime("%B %Y")
        }, indent=2)
    except Exception as e:
        logger.error(f"Error getting calendar events resource: {e}")
        return json.dumps({"error": str(e)})


@mcp.resource("calendar://event/{event_id}")
async def get_calendar_event_resource(event_id: str) -> str:
    """
    Resource: Get details of a specific calendar event.

    URI: calendar://event/{event_id}
    Example: calendar://event/abc123def456

    Returns JSON string with event details from the main calendar (email account ID 1).
    """
    try:
        # Get calendar service (always uses CALENDAR_EMAIL_ACCOUNT_ID)
        service, error = get_calendar_service(CALENDAR_EMAIL_ACCOUNT_ID)
        if not service:
            return json.dumps({"error": error})

        # Get event
        event = service.events().get(calendarId='primary', eventId=event_id).execute()

        event_data = {
            "id": event['id'],
            "title": event.get('summary', 'No Title'),
            "start": event['start'].get('dateTime', event['start'].get('date')),
            "end": event['end'].get('dateTime', event['end'].get('date')),
            "description": event.get('description', ''),
            "link": event.get('htmlLink'),
            "created": event.get('created'),
            "updated": event.get('updated')
        }

        # Add category if exists
        if 'extendedProperties' in event and 'private' in event['extendedProperties']:
            event_data['category'] = event['extendedProperties']['private'].get('category')

        return json.dumps(event_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting calendar event resource: {e}")
        return json.dumps({"error": str(e)})


@mcp.resource("account://list")
async def get_accounts_resource() -> str:
    """
    Resource: Get list of all registered accounts in the system.

    URI: account://list

    Returns JSON string with all accounts.
    """
    try:
        accounts = db_manager.get_all_accounts()

        account_list = [
            {
                "id": account.id,
                "primary_email": account.primary_email,
                "created_at": account.created_at.isoformat() if account.created_at else None
            }
            for account in accounts
        ]

        return json.dumps({
            "accounts": account_list,
            "count": len(account_list)
        }, indent=2)
    except Exception as e:
        logger.error(f"Error getting accounts resource: {e}")
        return json.dumps({"error": str(e)})


@mcp.resource("mailbox://list")
async def get_email_accounts_resource() -> str:
    """
    Resource: Get list of all email accounts in the system.

    URI: mailbox://list

    Returns JSON string with all email accounts.
    """
    try:
        email_accounts = db_manager.get_all_email_accounts()

        email_account_list = [
            {
                "id": ea.id,
                "email": ea.email,
                "provider": ea.provider,
                "account_id": ea.account_id,
                "is_primary": bool(ea.is_primary),
                "created_at": ea.created_at.isoformat() if ea.created_at else None
            }
            for ea in email_accounts
        ]

        return json.dumps({
            "email_accounts": email_account_list,
            "count": len(email_account_list)
        }, indent=2)
    except Exception as e:
        logger.error(f"Error getting email accounts resource: {e}")
        return json.dumps({"error": str(e)})


@mcp.resource("account://info/{account_id}")
async def get_account_resource(account_id: int) -> str:
    """
    Resource: Get detailed information about a specific account.

    URI: account://info/{account_id}
    Example: account://info/1

    Returns JSON string with account details.
    """
    try:
        with db_manager.get_session() as session:
            account = session.query(Account).filter_by(id=int(account_id)).first()

            if not account:
                return json.dumps({"error": f"Account with ID {account_id} not found"})

            # Get email accounts for this account
            email_accounts = session.query(EmailAccount).filter_by(account_id=account.id).all()
            
            # Count total emails across all email accounts
            total_emails = 0
            for ea in email_accounts:
                email_count = session.query(Email).filter_by(email_account_id=ea.id).count()
                total_emails += email_count

            account_data = {
                "id": account.id,
                "primary_email": account.primary_email,
                "created_at": account.created_at.isoformat() if account.created_at else None,
                "updated_at": account.updated_at.isoformat() if account.updated_at else None,
                "email_accounts_count": len(email_accounts),
                "total_emails": total_emails,
                "email_accounts": [
                    {
                        "id": ea.id,
                        "email": ea.email,
                        "provider": ea.provider,
                        "is_primary": bool(ea.is_primary)
                    }
                    for ea in email_accounts
                ]
            }

            return json.dumps(account_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting account resource: {e}")
        return json.dumps({"error": str(e)})


@mcp.resource("mailbox://info/{email_account_id}")
async def get_email_account_resource(email_account_id: int) -> str:
    """
    Resource: Get detailed information about a specific email account.

    URI: mailbox://info/{email_account_id}
    Example: mailbox://info/1

    Returns JSON string with email account details.
    """
    try:
        with db_manager.get_session() as session:
            email_account = session.query(EmailAccount).filter_by(id=int(email_account_id)).first()

            if not email_account:
                return json.dumps({"error": f"Email account with ID {email_account_id} not found"})

            # Get parent account
            account = session.query(Account).filter_by(id=email_account.account_id).first()
            
            # Get email count for this email account
            email_count = session.query(Email).filter_by(email_account_id=email_account.id).count()

            email_account_data = {
                "id": email_account.id,
                "email": email_account.email,
                "provider": email_account.provider,
                "account_id": email_account.account_id,
                "is_primary": bool(email_account.is_primary),
                "account_primary_email": account.primary_email if account else None,
                "email_count": email_count,
                "created_at": email_account.created_at.isoformat() if email_account.created_at else None,
                "updated_at": email_account.updated_at.isoformat() if email_account.updated_at else None
            }

            return json.dumps(email_account_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting email account resource: {e}")
        return json.dumps({"error": str(e)})


@mcp.resource("system://status")
async def get_system_status_resource() -> str:
    """
    Resource: Get system status and statistics.

    URI: system://status

    Returns JSON string with system information including total accounts, email accounts, emails, last sync time.
    """
    try:
        with db_manager.get_session() as session:
            total_accounts = session.query(Account).count()
            total_email_accounts = session.query(EmailAccount).count()
            total_emails = session.query(Email).count()

            # Get most recent email date
            latest_email = session.query(Email).order_by(Email.date_sent.desc()).first()
            latest_email_date = latest_email.date_sent.isoformat() if latest_email and latest_email.date_sent else None

            status_data = {
                "total_accounts": total_accounts,
                "total_email_accounts": total_email_accounts,
                "total_emails": total_emails,
                "latest_email_date": latest_email_date,
                "last_sync_time": context.get("last_sync_time"),
                "calendar_email_account_id": CALENDAR_EMAIL_ACCOUNT_ID,
                "timestamp": datetime.now().isoformat()
            }

            return json.dumps(status_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting system status resource: {e}")
        return json.dumps({"error": str(e)})


# ============================================================================
# SERVER ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting Gmail Calendar Agent MCP Server (Updated Schema)...")
    mcp.run()

