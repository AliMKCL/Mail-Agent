"""
MCP tools backed by the User_data service: create_calendar_event,
update_calendar_event, delete_calendar_event, get_calendar_events.

Spec section 3.6. Filled by agent M1.

Plain ``async def``s called in-process. Each is a thin client over one
``/internal/calendar/*`` route, which holds the moved body of the corresponding
monolith tool: the Google Calendar calls, the ``"All Day"`` / ``%I:%M %p``
parsing, the ``timeMax``-is-exclusive +1 day, the credential re-save after every
API call, and the Moodle merge. The routes are hardwired to calendar email
account 1 (**X4**) exactly as the tools were, so these functions take no
account argument — just as today.

The dicts below are whatever the route returned, unchanged: the routes already
emit the tools' own ``{"status": "success", ...}`` / ``{"status": "error",
"error": ...}`` envelopes verbatim at HTTP 200.
"""

import logging
from typing import Optional

from backend.services.mcp import clients

logger = logging.getLogger(__name__)


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

        user_data_client = clients.get_user_data_client()
        return (
            await user_data_client.post(
                "/internal/calendar/events",
                json={
                    "title": title,
                    "date": date,
                    "time": time,
                    "description": description,
                    "category": category,
                },
            )
        ).json()
    except Exception as e:
        logger.error(f"Error creating calendar event: {e}")
        return {"status": "error", "error": str(e)}


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

        user_data_client = clients.get_user_data_client()
        return (
            await user_data_client.patch(
                f"/internal/calendar/events/{event_id}",
                json={
                    "title": title,
                    "date": date,
                    "time": time,
                    "description": description,
                    "category": category,
                },
            )
        ).json()
    except Exception as e:
        logger.error(f"Error updating calendar event: {e}")
        return {"status": "error", "error": str(e)}


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

        user_data_client = clients.get_user_data_client()
        return (
            await user_data_client.delete(f"/internal/calendar/events/{event_id}")
        ).json()
    except Exception as e:
        logger.error(f"Error deleting calendar event: {e}")
        return {"status": "error", "error": str(e)}


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
        # Omitted dates are omitted from the query too, so the route applies the
        # current-month default window itself.
        params = {}
        if start_date is not None:
            params["start_date"] = start_date
        if end_date is not None:
            params["end_date"] = end_date

        user_data_client = clients.get_user_data_client()
        return (
            await user_data_client.get("/internal/calendar/events", params=params)
        ).json()
    except Exception as e:
        logger.error(f"Error getting calendar events: {e}")
        return {"status": "error", "error": str(e)}


__all__ = [
    "create_calendar_event",
    "update_calendar_event",
    "delete_calendar_event",
    "get_calendar_events",
]
