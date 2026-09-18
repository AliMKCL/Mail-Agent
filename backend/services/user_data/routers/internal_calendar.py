"""
User_data service — /internal/calendar/* routes consumed by MCP (flat sorted list, Moodle merged).

Spec section 3.4 (Phase 5.8). The four MCP calendar tools move here verbatim:
``create_calendar_event`` (``mcp_server.py:457-538``), ``update_calendar_event``
(``:541-615``), ``delete_calendar_event`` (``:618-650``) and
``get_calendar_events`` (``:653-770``), plus the ``calendar://event/{event_id}``
resource (``:1073-1110``) behind ``GET /internal/calendar/events/{event_id}``.

⚠️ **These are not the public ``/api/calendar/*`` routes and must never be
merged with them** (Spec 3.4's boxed warning). The two differ in three ways:

============  ====================================  ===============================
              public ``GET /api/calendar/events``   ``GET /internal/calendar/events``
============  ====================================  ===============================
shape         ``events`` is a **mapping** keyed by  ``events`` is a **flat list**,
              ``YYYY-MM-DD``                        sorted by ``start``
Moodle        excluded                              merged in, ``source: "moodle"``
date window   caller-driven                         defaults to the current month;
                                                    an explicit ``end_date`` gets
                                                    +1 day because Google's
                                                    ``timeMax`` is exclusive
============  ====================================  ===============================

Preserved quirks:

* **X4** — every route here operates on ``settings.CALENDAR_EMAIL_ACCOUNT_ID``
  (value 1) no matter who calls. The caller cannot choose a mailbox, and it is
  deliberately not parameterized.
* ``save_calendar_credentials_after_use`` (``mcp_server.py:60-73``) — Google's
  library may auto-refresh a token *during* an API call, so the possibly-new
  credentials are written back after every call. The local
  ``db_manager.save_email_token`` becomes an Accounts credential ``PUT`` (R2).
  Its swallow-everything ``except`` stays: a failed save never fails the
  operation that already succeeded.
* The Moodle merge's ``try``/``except`` logs a warning and continues, so a
  Moodle failure degrades the response instead of failing it. Approved fix
  **P2** (``moodle.get_moodle_events_for_api``'s parameter renamed
  ``user_id`` → ``email_account_id``) is what makes the merge actually work:
  the monolith called it with an ``email_account_id=`` keyword the function did
  not accept, so every request raised ``TypeError`` straight into that
  ``except`` and MCP has never returned a single Moodle event.

``context["last_sync_time"]`` (**X9**) is MCP-process-local and has no
equivalent here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.libs.common.http import AsyncServiceClient
from backend.libs.contracts.accounts import credentials_to_dto
from backend.services.user_data import google_calendar, moodle
from backend.services.user_data.clients import get_accounts_client
from backend.services.user_data.config import CALENDAR_EMAIL_ACCOUNT_ID

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal-calendar"])


class CreateEventRequest(BaseModel):
    """``create_calendar_event(title, date, time, description, category)``."""

    title: str
    date: str
    time: str = "All Day"
    description: str = ""
    category: str | None = None


class UpdateEventRequest(BaseModel):
    """``update_calendar_event(event_id, title, date, time, description, category)``.

    Every field is optional and ``description`` is checked with
    ``is not None`` rather than truthiness, so clearing a description with
    ``""`` works exactly as it does today.
    """

    title: str | None = None
    date: str | None = None
    time: str | None = None
    description: str | None = None
    category: str | None = None


async def save_calendar_credentials_after_use(
    service, email_account_id: int, accounts: AsyncServiceClient
) -> None:
    """
    Save potentially refreshed credentials after calendar service use.
    Google's library may auto-refresh tokens during API calls.
    """
    try:
        if hasattr(service, '_http') and hasattr(service._http, 'credentials'):
            creds = service._http.credentials
            if creds:
                await accounts.put(
                    f"/internal/email-accounts/{email_account_id}/credentials",
                    json=credentials_to_dto(creds).model_dump(mode="json"),
                )
                logger.debug(
                    f"Saved potentially refreshed credentials for email account {email_account_id}"
                )
    except Exception as e:
        logger.warning(f"Could not save credentials after calendar operation: {e}")


@router.post("/internal/calendar/events")
async def create_calendar_event(
    request_data: CreateEventRequest,
    accounts: AsyncServiceClient = Depends(get_accounts_client),
) -> dict:
    """
    Create a new calendar event. Always uses the main calendar email account (email account ID 1).

    Args:
        title: Event title/summary
        date: Event date in YYYY-MM-DD format
        time: Event time in "HH:MM AM/PM" format, or "All Day" for all-day events (default: "All Day")
        description: Event description/details (optional)
        category: Event category - one of: Academic, Career, Social, Deadline (optional)
    """
    title = request_data.title
    date = request_data.date
    time = request_data.time
    description = request_data.description
    category = request_data.category

    try:
        logger.info(f"Creating calendar event: {title} on {date}")

        # Get calendar service (always uses CALENDAR_EMAIL_ACCOUNT_ID)
        service, error = google_calendar.get_calendar_service(CALENDAR_EMAIL_ACCOUNT_ID)
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
        await save_calendar_credentials_after_use(
            service, CALENDAR_EMAIL_ACCOUNT_ID, accounts
        )

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


@router.patch("/internal/calendar/events/{event_id}")
async def update_calendar_event(
    event_id: str,
    request_data: UpdateEventRequest,
    accounts: AsyncServiceClient = Depends(get_accounts_client),
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
    title = request_data.title
    date = request_data.date
    time = request_data.time
    description = request_data.description
    category = request_data.category

    try:
        logger.info(f"Updating calendar event: {event_id}")

        # Get calendar service (always uses CALENDAR_EMAIL_ACCOUNT_ID)
        service, error = google_calendar.get_calendar_service(CALENDAR_EMAIL_ACCOUNT_ID)
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
        await save_calendar_credentials_after_use(
            service, CALENDAR_EMAIL_ACCOUNT_ID, accounts
        )

        logger.info(f"Event updated: {event_id}")
        return {
            "status": "success",
            "event_id": updated_event['id'],
            "event_link": updated_event.get('htmlLink')
        }
    except Exception as e:
        logger.error(f"Error updating calendar event: {e}")
        return {"status": "error", "error": str(e)}


@router.delete("/internal/calendar/events/{event_id}")
async def delete_calendar_event(
    event_id: str,
    accounts: AsyncServiceClient = Depends(get_accounts_client),
) -> dict:
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
        service, error = google_calendar.get_calendar_service(CALENDAR_EMAIL_ACCOUNT_ID)
        if not service:
            return {"status": "error", "error": error}

        # Delete the event
        service.events().delete(calendarId='primary', eventId=event_id).execute()

        # Save credentials in case they were refreshed during the API call
        await save_calendar_credentials_after_use(
            service, CALENDAR_EMAIL_ACCOUNT_ID, accounts
        )

        logger.info(f"Event deleted: {event_id}")
        return {
            "status": "success",
            "message": f"Event {event_id} deleted successfully"
        }
    except Exception as e:
        logger.error(f"Error deleting calendar event: {e}")
        return {"status": "error", "error": str(e)}


@router.get("/internal/calendar/events")
async def get_calendar_events(
    start_date: str | None = None,
    end_date: str | None = None,
    accounts: AsyncServiceClient = Depends(get_accounts_client),
) -> dict:
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
        service, error = google_calendar.get_calendar_service(CALENDAR_EMAIL_ACCOUNT_ID)
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
            moodle_result = moodle.get_moodle_events_for_api(
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
        await save_calendar_credentials_after_use(
            service, CALENDAR_EMAIL_ACCOUNT_ID, accounts
        )

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


@router.get("/internal/calendar/events/{event_id}")
async def get_calendar_event(event_id: str) -> dict:
    """
    Resource: Get details of a specific calendar event.

    URI: calendar://event/{event_id}
    Example: calendar://event/abc123def456

    Returns JSON string with event details from the main calendar (email account ID 1).

    The MCP resource serialized this dict with ``json.dumps``; that is the MCP
    service's job now, so the dict itself crosses the wire. The failure envelope
    is a bare ``{"error": ...}`` — deliberately *not* the tools'
    ``{"status": "error", ...}`` — because that is what the resource returned.
    """
    try:
        # Get calendar service (always uses CALENDAR_EMAIL_ACCOUNT_ID)
        service, error = google_calendar.get_calendar_service(CALENDAR_EMAIL_ACCOUNT_ID)
        if not service:
            return {"error": error}

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

        return event_data
    except Exception as e:
        logger.error(f"Error getting calendar event resource: {e}")
        return {"error": str(e)}


__all__ = ["router"]
