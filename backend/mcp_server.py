"""
MCP Server for Gmail Calendar Agent
Exposes email and calendar functions as MCP tools for LLM integration
"""

# Run the testing UI env: npx @modelcontextprotocol/inspector python backend/mcp_server.py

"""
Known problems:
- Moodle events in get_calendar_events not showing up in UI (Possibly due to original, since moodle feature was added later and uses a different table)
"""

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

# Import existing backend components
from backend.databases.database import DatabaseManager, User, Email
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

# Constants
CALENDAR_USER_ID = 1  # Calendar operations always use user ID 1 (main account)

# Global context - only for tracking, not enforcing user restrictions
# Email operations can work across all users (LLM can specify user_id in queries)
# Calendar operations always use CALENDAR_USER_ID
context = {
    "last_sync_time": None
}


# ============================================================================
# MCP TOOLS - Functions the LLM can execute
# ============================================================================

# --- User Management Tools ---

@mcp.tool()
async def list_users() -> dict:
    """
    List all registered Gmail users in the system.
    Returns user information including id, email, and name.
    """
    try:
        users = db_manager.get_all_users()
        user_list = [
            {
                "id": user.id,
                "email": user.email,
                "name": user.name
            }
            for user in users
        ]
        logger.info(f"Listed {len(user_list)} users")
        return {
            "status": "success",
            "users": user_list,
            "count": len(user_list)
        }
    except Exception as e:
        logger.error(f"Error listing users: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def get_user_info(user_id: int) -> dict:
    """
    Get detailed information about a specific user by their ID.

    Args:
        user_id: The ID of the user to retrieve
    """
    try:
        with db_manager.get_session() as session:
            user = session.query(User).filter_by(id=user_id).first()
            if not user:
                return {"status": "error", "error": f"User with ID {user_id} not found"}

            return {
                "status": "success",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "name": user.name,
                    "created_at": user.created_at.isoformat() if user.created_at else None
                }
            }
    except Exception as e:
        logger.error(f"Error getting user info: {e}")
        return {"status": "error", "error": str(e)}


# --- Email Tools ---

@mcp.tool()
async def search_emails(query: str, user_id: Optional[int] = None, use_semantic: bool = False, limit: int = 10) -> dict:
    """
    Search emails using Gmail query syntax or semantic search across the vector database.

    Args:
        query: Search query (Gmail syntax like "from:example@gmail.com" or natural language for semantic search)
        user_id: Optional user ID to filter emails. If not provided, searches across all users
        use_semantic: If True, uses vector database semantic search; if False, uses Gmail API search
        limit: Maximum number of results to return (default: 10)

    Examples:
        - search_emails("deadline", user_id=2, use_semantic=True)
        - search_emails("from:professor@university.edu", user_id=1)
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
                # Filter by user_id if specified
                if user_id is not None:
                    # Get full email to check user_id
                    with db_manager.get_session() as session:
                        email = session.query(Email).filter_by(message_id=result["message_id"]).first()
                        if email and email.user_id == user_id:
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
            # Use Gmail API search - requires user_id
            if user_id is None:
                return {"status": "error", "error": "user_id required for Gmail API search"}

            logger.info(f"Gmail API search for user {user_id}: '{query}'")
            service = get_service(user_id)
            ids = list_message_ids(service, query=query, max_results=limit)

            # Get email details from database
            with db_manager.get_session() as session:
                emails = session.query(Email).filter(
                    Email.user_id == user_id,
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
async def sync_emails(user_id: int, max_results: int = 50) -> dict:
    """
    Fetch new emails from Gmail for a specific user and store them in the database and vector store.

    Args:
        user_id: The ID of the user whose emails to sync
        max_results: Maximum number of emails to fetch (default: 50)

    Returns:
        Status of sync operation including counts of fetched and new emails
    """
    try:
        logger.info(f"Syncing emails for user {user_id}, max_results: {max_results}")

        # Get Gmail service
        service = get_service(user_id)

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
        saved_emails = db_manager.save_emails(user_id, email_data)

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
                    "user_id": email.user_id
                }
            }
    except Exception as e:
        logger.error(f"Error getting email details: {e}")
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def get_user_emails(user_id: int, limit: int = 50) -> dict:
    """
    Get cached emails for a specific user from the database.

    Args:
        user_id: The ID of the user whose emails to retrieve
        limit: Maximum number of emails to return (default: 50)

    Returns:
        List of emails for the specified user
    """
    try:
        logger.info(f"Getting {limit} emails for user {user_id}")

        emails = db_manager.get_user_emails(user_id, limit=limit)

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
            "user_id": user_id
        }
    except Exception as e:
        logger.error(f"Error getting user emails: {e}")
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
    Create a new calendar event. Always uses the main calendar account (user ID 1).

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

        # Get calendar service (always uses CALENDAR_USER_ID)
        service, error = get_calendar_service(CALENDAR_USER_ID)
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
    Update an existing calendar event. Always uses the main calendar account (user ID 1).

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

        # Get calendar service (always uses CALENDAR_USER_ID)
        service, error = get_calendar_service(CALENDAR_USER_ID)
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
    Delete a calendar event. Always uses the main calendar account (user ID 1).

    Args:
        event_id: The Google Calendar event ID to delete

    Returns:
        Status of deletion operation
    """
    try:
        logger.info(f"Deleting calendar event: {event_id}")

        # Get calendar service (always uses CALENDAR_USER_ID)
        service, error = get_calendar_service(CALENDAR_USER_ID)
        if not service:
            return {"status": "error", "error": error}

        # Delete the event
        service.events().delete(calendarId='primary', eventId=event_id).execute()

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
    Always uses the main calendar account (user ID 1).
    If no dates provided, returns events for the current month.

    Args:
        start_date: Start date in YYYY-MM-DD format (optional, defaults to start of current month)
        end_date: End date in YYYY-MM-DD format (optional, defaults to end of current month)

    Returns:
        List of events in the specified date range from all calendars
    """
    try:
        # Get calendar service (always uses CALENDAR_USER_ID)
        service, error = get_calendar_service(CALENDAR_USER_ID)
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
            end_date_str = datetime.fromisoformat(end_date).isoformat() + 'Z'
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
                user_id=CALENDAR_USER_ID,
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
async def extract_dates_from_emails(user_id: int, limit: int = 20, auto_create_events: bool = False) -> dict:
    """
    Extract deadlines and important dates from recent emails using LLM.
    Optionally create calendar events automatically.

    Args:
        user_id: The ID of the user whose emails to analyze
        limit: Number of recent emails to analyze (default: 20)
        auto_create_events: If True, automatically creates calendar events for found dates (default: False)

    Returns:
        Extracted dates and optionally created event IDs
    """
    try:
        logger.info(f"Extracting dates from {limit} emails for user {user_id}")

        # Get recent emails
        emails = db_manager.get_user_emails(user_id, limit=limit)

        if not emails:
            return {"status": "success", "extracted_dates": [], "message": "No emails found"}

        # Build email text for LLM
        email_texts = []
        for email in emails:
            email_text = f"Subject: {email.subject}\nFrom: {email.sender}\nDate: {email.date_sent}\n"
            if email.body_text:
                email_text += f"Body: {email.body_text[:500]}\n"
            email_texts.append(email_text)

        # Create prompt for LLM
        prompt = f"""Extract all dates, deadlines, and time-sensitive information from these emails.
For each date found, provide:
- date: in YYYY-MM-DD format
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
async def summarize_emails(query: str = "unread", user_id: Optional[int] = None, summary_type: str = "brief") -> dict:
    """
    Generate an AI summary of emails matching specific criteria.

    Args:
        query: Filter criteria - Gmail query syntax (default: "unread")
        user_id: Optional user ID to filter emails (if not provided, uses semantic search across all)
        summary_type: Type of summary - "brief", "detailed", or "bullet_points" (default: "brief")

    Returns:
        AI-generated summary of matching emails
    """
    try:
        logger.info(f"Summarizing emails with query: {query}, type: {summary_type}")

        # Search for emails
        search_result = await search_emails(query=query, user_id=user_id, use_semantic=True, limit=20)

        if search_result.get('status') != 'success' or not search_result.get('results'):
            return {"status": "success", "summary": "No emails found matching the criteria."}

        emails = search_result['results']

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
        if summary_type == "bullet_points":
            prompt = f"""Summarize these emails as a bullet-point list.
Group by category (work, academic, personal, etc.) if applicable.

Emails:
{chr(10).join(email_context)}

Provide a concise bullet-point summary:
"""
        elif summary_type == "detailed":
            prompt = f"""Provide a detailed summary of these emails.
Include key information, action items, and any deadlines mentioned.

Emails:
{chr(10).join(email_context)}

Detailed summary:
"""
        else:  # brief
            prompt = f"""Provide a brief summary of these emails in 2-3 sentences.
Focus on the most important information.

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

@mcp.resource("mail://inbox/{user_id}")
async def get_inbox_resource(user_id: int) -> str:
    """
    Resource: Get inbox emails for a specific user.

    URI: mail://inbox/{user_id}
    Example: mail://inbox/1

    Returns JSON string with list of emails for the specified user.
    """
    try:
        emails = db_manager.get_user_emails(int(user_id), limit=50)

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
            "user_id": int(user_id),
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
                "user_id": email.user_id
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

    Returns JSON string with list of events from all calendars (user ID 1).
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

    Returns JSON string with event details from the main calendar (user ID 1).
    """
    try:
        # Get calendar service (always uses CALENDAR_USER_ID)
        service, error = get_calendar_service(CALENDAR_USER_ID)
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


@mcp.resource("user://list")
async def get_users_resource() -> str:
    """
    Resource: Get list of all registered users in the system.

    URI: user://list

    Returns JSON string with all users.
    """
    try:
        users = db_manager.get_all_users()

        user_list = [
            {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "created_at": user.created_at.isoformat() if user.created_at else None
            }
            for user in users
        ]

        return json.dumps({
            "users": user_list,
            "count": len(user_list)
        }, indent=2)
    except Exception as e:
        logger.error(f"Error getting users resource: {e}")
        return json.dumps({"error": str(e)})


@mcp.resource("user://info/{user_id}")
async def get_user_resource(user_id: int) -> str:
    """
    Resource: Get detailed information about a specific user.

    URI: user://info/{user_id}
    Example: user://info/1

    Returns JSON string with user details.
    """
    try:
        with db_manager.get_session() as session:
            user = session.query(User).filter_by(id=int(user_id)).first()

            if not user:
                return json.dumps({"error": f"User with ID {user_id} not found"})

            user_data = {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "updated_at": user.updated_at.isoformat() if user.updated_at else None
            }

            # Get email count for this user
            email_count = session.query(Email).filter_by(user_id=user.id).count()
            user_data["email_count"] = email_count

            return json.dumps(user_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting user resource: {e}")
        return json.dumps({"error": str(e)})


@mcp.resource("system://status")
async def get_system_status_resource() -> str:
    """
    Resource: Get system status and statistics.

    URI: system://status

    Returns JSON string with system information including total users, emails, last sync time.
    """
    try:
        with db_manager.get_session() as session:
            total_users = session.query(User).count()
            total_emails = session.query(Email).count()

            # Get most recent email date
            latest_email = session.query(Email).order_by(Email.date_sent.desc()).first()
            latest_email_date = latest_email.date_sent.isoformat() if latest_email and latest_email.date_sent else None

            status_data = {
                "total_users": total_users,
                "total_emails": total_emails,
                "latest_email_date": latest_email_date,
                "last_sync_time": context.get("last_sync_time"),
                "calendar_user_id": CALENDAR_USER_ID,
                "timestamp": datetime.now().isoformat()
            }

            return json.dumps(status_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting system status resource: {e}")
        return json.dumps({"error": str(e)})


# ============================================================================
# MCP PROMPTS - Guided workflows
# ============================================================================

@mcp.prompt()
def email_triage_prompt(priority_keywords: str = "urgent, important, deadline, ASAP") -> str:
    """
    Prompt: Guide LLM to triage and prioritize emails.

    This prompt helps the LLM analyze unread emails, categorize them by priority,
    and provide actionable recommendations.

    Args:
        priority_keywords: Comma-separated keywords to look for (default: "urgent, important, deadline, ASAP")

    Returns:
        Formatted instructions for the LLM
    """
    return f"""You are an email triage assistant. Your task is to analyze and prioritize emails.

**Step-by-step workflow:**

1. **Get all users** using the `list_users` tool to see available email accounts
2. **For each user**, use `search_emails` with semantic search to find recent emails
3. **Analyze each email** for urgency based on these keywords: {priority_keywords}
4. **Categorize emails** into:
   - 🔴 HIGH PRIORITY: Requires immediate action (deadlines, urgent requests)
   - 🟡 MEDIUM PRIORITY: Important but not urgent
   - 🟢 LOW PRIORITY: FYI, can wait
   - ⚪ CAN SKIP: Newsletters, promotions

5. **For HIGH PRIORITY emails**:
   - Extract any deadlines or action items
   - Check if related calendar events exist using `get_calendar_events`
   - Suggest creating calendar events if needed

6. **Format your response** as:
   ```
   📧 EMAIL TRIAGE SUMMARY

   🔴 HIGH PRIORITY:
   - [Subject] from [Sender] - [Why it's urgent] - [Suggested action]

   🟡 MEDIUM PRIORITY:
   - [Subject] from [Sender] - [Brief description]

   🟢 LOW PRIORITY:
   - [Subject] from [Sender]

   💡 RECOMMENDATIONS:
   - Create calendar event for [deadline]
   - Respond to [sender] about [topic]
   ```

**Available tools:**
- `list_users` - Get all email accounts
- `search_emails` - Search emails (use semantic=True for better results)
- `get_email_details` - Get full email content
- `get_calendar_events` - Check existing calendar events
- `create_calendar_event` - Create event for deadline (if needed)

**Available resources:**
- `mail://inbox/{{user_id}}` - Quick view of user's inbox
- `calendar://events` - Current calendar events

Start by listing users and analyzing their emails.
"""


@mcp.prompt()
def deadline_tracker_prompt(user_id: int = 1, days_to_search: int = 7, auto_create: bool = True) -> str:
    """
    Prompt: Guide LLM to find deadlines in emails and create calendar events.

    This prompt helps automate deadline tracking by extracting dates from emails
    and optionally creating calendar events.

    Args:
        user_id: Which user's emails to analyze (default: 1)
        days_to_search: How many days of emails to search (default: 7)
        auto_create: Whether to automatically create calendar events (default: True)

    Returns:
        Formatted instructions for the LLM
    """
    return f"""You are a deadline tracking assistant. Your task is to find all deadlines in recent emails and manage them in the calendar.

**Step-by-step workflow:**

1. **Sync recent emails** for user {user_id}:
   - Use `sync_emails` with user_id={user_id} to fetch latest emails

2. **Extract deadlines** from emails:
   - Use `extract_dates_from_emails` tool with:
     - user_id={user_id}
     - limit={days_to_search * 10} (approximate emails for {days_to_search} days)
     - auto_create_events={str(auto_create).lower()}

3. **Review extracted deadlines**:
   - List all found deadlines with dates and descriptions
   - Show which emails they came from

4. **Check calendar** to avoid duplicates:
   - Use `get_calendar_events` to see existing events
   - Identify if any deadlines are already scheduled

5. **{"Create calendar events" if auto_create else "Suggest calendar events"}**:
   - {"Events will be auto-created with category 'Deadline'" if auto_create else "Provide list of events user should create"}

6. **Format your response** as:
   ```
   📅 DEADLINE TRACKER REPORT

   🔍 FOUND {'{count}'} DEADLINES:

   1. [Date] - [Description]
      From: [Email subject]
      {"✅ Calendar event created" if auto_create else "⏳ Suggested action: Create calendar event"}

   2. [Date] - [Description]
      From: [Email subject]
      {"✅ Calendar event created" if auto_create else "⏳ Suggested action: Create calendar event"}

   📊 SUMMARY:
   - Total deadlines found: {'{count}'}
   - {"Calendar events created: {created_count}" if auto_create else "Events to create: {count}"}
   - Earliest deadline: [Date]
   ```

**Available tools:**
- `sync_emails` - Fetch latest emails from Gmail
- `extract_dates_from_emails` - AI-powered deadline extraction
- `get_calendar_events` - Check existing calendar
- `create_calendar_event` - Manually create event (if auto_create=False)

**Available resources:**
- `mail://inbox/{user_id}` - User's inbox
- `calendar://events` - Current calendar

Start by syncing emails for user {user_id}.
"""


@mcp.prompt()
def daily_digest_prompt(user_id: int = 1, summary_type: str = "detailed") -> str:
    """
    Prompt: Guide LLM to create a daily email digest.

    This prompt helps generate a comprehensive summary of the day's emails,
    organized by category and priority.

    Args:
        user_id: Which user's emails to summarize (default: 1)
        summary_type: Type of summary - "brief", "detailed", or "bullet_points" (default: "detailed")

    Returns:
        Formatted instructions for the LLM
    """
    return f"""You are a daily digest assistant. Your task is to create a comprehensive summary of today's emails.

**Step-by-step workflow:**

1. **Sync latest emails**:
   - Use `sync_emails` with user_id={user_id} to ensure we have the latest

2. **Categorize emails** by type:
   - Use `search_emails` with semantic search to find emails in categories:
     - Academic (from professors, assignments, courses)
     - Work/Career (internships, job applications, professional contacts)
     - Social (events, meetings, personal)
     - Administrative (services, notifications, receipts)

3. **Generate summaries** for each category:
   - Use `summarize_emails` tool with summary_type="{summary_type}"
   - Focus on actionable items and important information

4. **Check for deadlines and events**:
   - Identify any time-sensitive information
   - Cross-reference with calendar using `get_calendar_events`

5. **Format your response** as:
   ```
   📬 DAILY EMAIL DIGEST - {{date}}
   User: {{user_email}} (ID: {user_id})

   {"=" * 60}

   📚 ACADEMIC ({'{count}'}):
   {'{summary}'}

   💼 WORK/CAREER ({'{count}'}):
   {'{summary}'}

   👥 SOCIAL ({'{count}'}):
   {'{summary}'}

   🔔 ADMINISTRATIVE ({'{count}'}):
   {'{summary}'}

   {"=" * 60}

   ⚡ ACTION ITEMS:
   - [ ] {'{action_1}'}
   - [ ] {'{action_2}'}

   📅 UPCOMING DEADLINES:
   - {'{deadline_1}'}
   - {'{deadline_2}'}

   📊 STATISTICS:
   - Total emails: {'{total}'}
   - Emails requiring response: {'{response_needed}'}
   - Deadlines identified: {'{deadline_count}'}
   ```

**Available tools:**
- `sync_emails` - Fetch latest emails
- `search_emails` - Find emails by category (use semantic=True)
- `summarize_emails` - Generate AI summaries
- `get_calendar_events` - Check calendar
- `get_user_info` - Get user details

**Available resources:**
- `mail://inbox/{user_id}` - User's inbox
- `user://info/{user_id}` - User information
- `calendar://events` - Calendar events

Start by syncing emails and getting user info.
"""


@mcp.prompt()
def meeting_scheduler_prompt(duration_minutes: int = 60, preferred_times: str = "mornings") -> str:
    """
    Prompt: Guide LLM to find optimal meeting times and schedule events.

    This prompt helps analyze calendar availability and suggest or schedule meetings.

    Args:
        duration_minutes: Meeting duration in minutes (default: 60)
        preferred_times: When to prefer meetings - "mornings", "afternoons", "evenings", or "any" (default: "mornings")

    Returns:
        Formatted instructions for the LLM
    """
    return f"""You are a meeting scheduling assistant. Your task is to find optimal meeting times based on calendar availability.

**Step-by-step workflow:**

1. **Review calendar availability**:
   - Use `get_calendar_events` to see existing events for current month
   - Identify busy time slots

2. **Find free time slots**:
   - Look for {duration_minutes}-minute gaps in the calendar
   - Prefer {preferred_times} if possible
   - Consider work hours (9 AM - 6 PM on weekdays)

3. **Propose meeting times**:
   - Suggest 3-5 optimal time slots
   - Prioritize based on:
     - Preference for {preferred_times}
     - Avoiding back-to-back meetings (leave 15-min buffer)
     - Spreading meetings throughout the week

4. **Check email context** (if applicable):
   - Use `search_emails` to find related email threads
   - Identify meeting attendees and topics from emails

5. **Format your response** as:
   ```
   📅 MEETING SCHEDULING ASSISTANT

   ⏱️  MEETING DETAILS:
   - Duration: {duration_minutes} minutes
   - Preference: {preferred_times}

   ✅ AVAILABLE TIME SLOTS:

   1. [Day, Date] at [Time]
      ⭐ Recommended - {'{reason}'}

   2. [Day, Date] at [Time]
      {'{why_this_slot}'}

   3. [Day, Date] at [Time]
      {'{why_this_slot}'}

   📊 CALENDAR ANALYSIS:
   - Busy hours this week: [hours]
   - Best meeting windows: {preferred_times}
   - Existing meetings: {'{count}'}

   💡 NEXT STEPS:
   Once you select a time, I can create the calendar event for you.
   ```

6. **If user confirms a time**, create the event:
   - Use `create_calendar_event` with selected date/time
   - Set appropriate category (Career, Social, Academic)

**Available tools:**
- `get_calendar_events` - Review calendar
- `create_calendar_event` - Schedule the meeting
- `search_emails` - Find meeting-related emails (optional)

**Available resources:**
- `calendar://events` - Current calendar state

Start by reviewing the calendar and analyzing availability.
"""


@mcp.prompt()
def smart_search_prompt(search_query: str, max_results: int = 10) -> str:
    """
    Prompt: Guide LLM to perform intelligent email search across all users.

    This prompt helps conduct sophisticated searches using both semantic
    and keyword-based approaches.

    Args:
        search_query: What to search for (natural language or keywords)
        max_results: Maximum number of results to return (default: 10)

    Returns:
        Formatted instructions for the LLM
    """
    return f"""You are a smart email search assistant. Your task is to find relevant emails using advanced search techniques.

**Search query:** "{search_query}"

**Step-by-step workflow:**

1. **Understand the query**:
   - Analyze if the query is:
     - Keyword-based (e.g., "from:professor deadline")
     - Semantic (e.g., "emails about project deadlines")
     - Time-based (e.g., "recent emails from last week")

2. **Perform semantic search** first:
   - Use `search_emails` with use_semantic=True, query="{search_query}", limit={max_results}
   - This uses vector embeddings to find conceptually similar emails

3. **If semantic search yields < 3 results**, try keyword search:
   - Extract key terms from "{search_query}"
   - Use `search_emails` with Gmail query syntax

4. **For each result**:
   - Get full email details with `get_email_details` if needed
   - Extract relevant snippets that match the query
   - Identify the user who received the email

5. **Analyze and rank results** by relevance:
   - Most relevant to query
   - Most recent
   - From important senders

6. **Format your response** as:
   ```
   🔍 SMART SEARCH RESULTS
   Query: "{search_query}"
   Found: {'{count}'} results

   {"=" * 60}

   1. ⭐ [Subject]
      From: [Sender] | To: [User] | Date: [Date]
      Relevance: {'{why_relevant}'}

      Preview: "{'{snippet}'}"

      📧 [Link to full email via message_id]

   2. [Subject]
      From: [Sender] | To: [User] | Date: [Date]
      Relevance: {'{why_relevant}'}

      Preview: "{'{snippet}'}"

   {"=" * 60}

   💡 INSIGHTS:
   - Common theme: {'{identified_theme}'}
   - Time range: {'{date_range}'}
   - Key senders: {'{top_senders}'}

   📊 SEARCH ANALYTICS:
   - Search method: Semantic + Keyword
   - Total matches: {'{count}'}
   - Confidence: {'{high/medium/low}'}
   ```

**Available tools:**
- `search_emails` - Primary search (both semantic and keyword)
- `get_email_details` - Get full email content
- `list_users` - See which users have emails

**Available resources:**
- `mail://email/{{message_id}}` - Direct access to specific email
- `user://list` - All users

Start by performing semantic search for "{search_query}".
"""


# ============================================================================
# SERVER ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting Gmail Calendar Agent MCP Server...")
    mcp.run()
