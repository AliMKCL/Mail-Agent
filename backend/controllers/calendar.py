"""
Google Calendar event CRUD, status check, and Moodle-calendar passthrough endpoints.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from backend import dependencies
from backend.services.moodle_calendar import get_moodle_events_for_api
from backend.services.setup_calendar import (
    authenticate_google_calendar,
    get_calendar_service,
)

router = APIRouter(tags=["calendar"])

EMAIL_ACCOUNT_ID_FOR_CALENDAR = 1  # Default email account for calendar operations


class CalendarEventData(BaseModel):
    title: str
    description: str | None = ""
    date: str  # Format: YYYY-MM-DD
    time: str | None = "All Day"  # Format: HH:MM AM/PM
    category: str | None = None


class CreateCalendarEventRequest(BaseModel):
    email_account_id: int
    event_data: CalendarEventData


class UpdateCalendarEventRequest(BaseModel):
    email_account_id: int
    event_data: CalendarEventData


# Helper function to get primary email account for an account
def get_primary_email_account_id(email_account_id: int) -> int:
    """Get the primary email account ID for the account that owns this email account"""
    try:
        # Get the email account
        email_account = dependencies.db_manager.get_email_account_by_id(
            email_account_id
        )
        if not email_account:
            return email_account_id  # Fallback to provided ID

        # Get all email accounts for this account
        email_accounts = dependencies.db_manager.get_account_email_accounts(
            email_account.account_id
        )

        # Find the primary one
        for ea in email_accounts:
            if ea.is_primary:
                return ea.id

        # If no primary found, return the first one or the provided ID
        return email_accounts[0].id if email_accounts else email_account_id
    except Exception as e:
        print(f"Error getting primary email account: {e}")
        return email_account_id  # Fallback to provided ID


# This endpoint is called to fetch calendar events for a user within a date range.
@router.get("/api/calendar/events")
async def get_calendar_events(
    email_account_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
):
    """
    Get Google Calendar events for a specific email account within a date range
    Calendar is always fetched from the primary email account of the Account
    """
    try:
        if email_account_id is None:
            raise HTTPException(
                status_code=400, detail="email_account_id parameter is required"
            )

        # Get the primary email account ID for calendar access
        primary_email_account_id = get_primary_email_account_id(email_account_id)
        print(
            f"[GET /api/calendar/events] Using primary email account {primary_email_account_id} for calendar access"
        )

        # Get calendar service for the primary email account
        try:
            service, error = get_calendar_service(primary_email_account_id)
        except Exception as e:
            print(f"Exception in get_calendar_service: {e}")
            raise HTTPException(
                status_code=500, detail=f"Calendar service error: {e!s}"
            )

        if not service:
            if "Authentication required" in str(error):
                auth_url, state = authenticate_google_calendar(email_account_id)
                if auth_url:
                    return {
                        "status": "auth_required",
                        "auth_url": auth_url,
                        "message": "Please authenticate with Google Calendar",
                    }
            raise HTTPException(
                status_code=500, detail=f"Failed to get calendar service: {error}"
            )

        if not start_date:
            # Display events from X months ago to now.
            start_date = (datetime.now() - timedelta(days=180)).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat() + "Z"
        if not end_date:
            # End 2 years from now (730 days)
            end_date = (datetime.now() + timedelta(days=730)).replace(
                hour=23, minute=59, second=59
            ).isoformat() + "Z"

        # Fetch events from Google Calendar
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start_date,
                timeMax=end_date,
                maxResults=2500,  # Increase from default 250 to 2500 (API maximum)
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = events_result.get("items", [])
        print(
            f"[GET /api/calendar/events] Fetched {len(events)} events from Google Calendar API"
        )
        print(f"[GET /api/calendar/events] Date range: {start_date} to {end_date}")

        # Format events for frontend
        formatted_events = {}
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            if start:
                # Parse date
                if "T" in start:
                    event_date = datetime.fromisoformat(start.replace("Z", "+00:00"))
                else:
                    event_date = datetime.fromisoformat(start)
                date_key = event_date.strftime("%Y-%m-%d")
                if date_key not in formatted_events:
                    formatted_events[date_key] = []

                # Get category from extendedProperties if it exists
                category = None
                ext_props = event.get("extendedProperties", {})
                private_props = ext_props.get("private", {})
                if private_props and "category" in private_props:
                    category = private_props["category"]

                # Format time
                if "dateTime" in event["start"]:
                    time_str = event_date.strftime("%I:%M %p")
                else:
                    time_str = "All Day"

                formatted_events[date_key].append(
                    {
                        "id": event["id"],
                        "title": event.get("summary", "No Title"),
                        "category": category,
                        "time": time_str,
                        "description": event.get("description", ""),
                        "start": start,
                        "end": event["end"].get("dateTime", event["end"].get("date")),
                    }
                )

        total_formatted = sum(len(events) for events in formatted_events.values())
        print(
            f"[GET /api/calendar/events] Returning {total_formatted} formatted events across {len(formatted_events)} dates"
        )
        print(f"[GET /api/calendar/events] Date keys: {list(formatted_events.keys())}")

        return {
            "status": "success",
            "events": formatted_events,
            "message": "Calendar events retrieved successfully",
        }
    except HTTPException:
        raise  # Re-raise HTTPExceptions
    except HttpError as e:
        raise HTTPException(status_code=500, detail=f"Google Calendar API error: {e!s}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching calendar events: {e!s}"
        )


# This endpoint is called when creating a new calendar event via the modal.
@router.post("/api/calendar/events")
async def create_calendar_event(request_data: CreateCalendarEventRequest):
    """
    Create a new calendar event
    Calendar event is always created on the primary email account of the Account
    """
    try:
        # ==================== RATE LIMITED EMAILS REFRESH / user scope ====================
        result = dependencies.limiter.check(
            scope="global",
            identifier="all",
            endpoint="api/calendar/events",
            tokens=1,
            capacity=100,
            refill_rate=100,
        )

        if not result["allowed"]:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded! You can only view emails {result['limit']} times per hour. Wait {result['retry_after_seconds']} seconds.",
                headers={
                    "X-RateLimit-Limit": str(result["limit"]),
                    "X-RateLimit-Remaining": "0",
                    "Retry-After": str(result["retry_after_seconds"]),
                },
            )
        # =====================================================================

        email_account_id = request_data.email_account_id
        event_data = request_data.event_data

        # Get the primary email account ID for calendar access
        primary_email_account_id = get_primary_email_account_id(email_account_id)
        print(
            f"[POST /api/calendar/events] Using primary email account {primary_email_account_id} for calendar access"
        )

        # Get calendar service for the primary email account
        try:
            service, error = get_calendar_service(primary_email_account_id)
        except Exception as e:
            print(f"Exception in get_calendar_service (POST): {e}")
            raise HTTPException(
                status_code=500, detail=f"Calendar service error: {e!s}"
            )

        if not service:
            raise HTTPException(
                status_code=500, detail=f"Failed to get calendar service: {error}"
            )

        # Parse event data
        title = event_data.title or "New Event"
        description = event_data.description or ""
        date = event_data.date  # Format: YYYY-MM-DD
        time = event_data.time or ""  # Format: HH:MM AM/PM
        category = event_data.category  # User-selected category

        # Create event object
        if time and time != "All Day":
            # Parse time and create datetime
            try:
                time_obj = datetime.strptime(time, "%I:%M %p").time()
                start_datetime = datetime.combine(
                    datetime.fromisoformat(date).date(), time_obj
                )
                end_datetime = start_datetime + timedelta(
                    hours=1
                )  # Default 1 hour duration

                event = {
                    "summary": title,
                    "description": description,
                    "start": {
                        "dateTime": start_datetime.isoformat(),
                        "timeZone": "UTC",
                    },
                    "end": {
                        "dateTime": end_datetime.isoformat(),
                        "timeZone": "UTC",
                    },
                }
            except ValueError:
                # If time parsing fails, create all-day event
                event = {
                    "summary": title,
                    "description": description,
                    "start": {"date": date},
                    "end": {"date": date},
                }
        else:
            # All-day event
            event = {
                "summary": title,
                "description": description,
                "start": {"date": date},
                "end": {"date": date},
            }

        # Store category in extendedProperties so it persists with the event
        # Google API expects metadata for events in extendedProperties["private/shared"]
        if category:
            event["extendedProperties"] = {"private": {"category": category}}

        # [DEBUG] Print details when eventaddition
        """
        print(f"[POST /api/calendar/events] Creating event with body: {event}")
        print(f"[POST /api/calendar/events] Successfully created event ID: {created_event.get('id')}")
        print(f"[POST /api/calendar/events] Created event start: {created_event.get('start')}")
        print(f"[POST /api/calendar/events] Event details: title='{title}', date='{date}', time='{time}', category='{category}'")
        """

        created_event = (
            service.events().insert(calendarId="primary", body=event).execute()
        )
        return {
            "status": "success",
            "message": "Event created successfully",
            "event_id": created_event["id"],
            "event_link": created_event.get("htmlLink", ""),
        }

    except HTTPException:
        raise  # Re-raise HTTPExceptions (including 429 rate limit errors and 400 validation errors) without modification
    except HttpError as e:
        raise HTTPException(status_code=500, detail=f"Google Calendar API error: {e!s}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error creating calendar event: {e!s}"
        )


# This endpoint is called when updating an existing calendar event.
@router.put("/api/calendar/events/{event_id}")
async def update_calendar_event(
    event_id: str, request_data: UpdateCalendarEventRequest
):
    """
    Update an existing calendar event
    Calendar is always on the primary email account of the Account
    """
    try:
        email_account_id = request_data.email_account_id
        event_data = request_data.event_data

        # Get the primary email account ID for calendar access
        primary_email_account_id = get_primary_email_account_id(email_account_id)
        print(
            f"[PUT /api/calendar/events] Using primary email account {primary_email_account_id} for calendar access"
        )

        # Get calendar service for the primary email account
        service, error = get_calendar_service(primary_email_account_id)
        if not service:
            raise HTTPException(
                status_code=500, detail=f"Failed to get calendar service: {error}"
            )

        # Get existing event
        event = service.events().get(calendarId="primary", eventId=event_id).execute()

        # Update event fields
        if event_data.title:
            event["summary"] = event_data.title
        if event_data.description is not None:
            event["description"] = event_data.description

        # Update category if provided (Create extendedProperties if not exist)
        if event_data.category:
            if "extendedProperties" not in event:
                event["extendedProperties"] = {"private": {}}
            if "private" not in event["extendedProperties"]:
                event["extendedProperties"]["private"] = {}
            event["extendedProperties"]["private"]["category"] = event_data.category

        # Handle time updates
        if event_data.date:
            date = event_data.date
            time = event_data.time or "All Day"

            if time and time != "All Day":
                try:
                    time_obj = datetime.strptime(time, "%I:%M %p").time()
                    start_datetime = datetime.combine(
                        datetime.fromisoformat(date).date(), time_obj
                    )
                    end_datetime = start_datetime + timedelta(hours=1)

                    event["start"] = {
                        "dateTime": start_datetime.isoformat(),
                        "timeZone": "UTC",
                    }
                    event["end"] = {
                        "dateTime": end_datetime.isoformat(),
                        "timeZone": "UTC",
                    }
                except ValueError:
                    event["start"] = {"date": date}
                    event["end"] = {"date": date}
            else:
                event["start"] = {"date": date}
                event["end"] = {"date": date}

        # Update event in Google Calendar
        updated_event = (
            service.events()
            .update(calendarId="primary", eventId=event_id, body=event)
            .execute()
        )

        return {
            "status": "success",
            "message": "Event updated successfully",
            "event_link": updated_event.get("htmlLink", ""),
        }

    except HttpError as e:
        if e.resp.status == 404:
            raise HTTPException(status_code=404, detail="Event not found")
        raise HTTPException(status_code=500, detail=f"Google Calendar API error: {e!s}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error updating calendar event: {e!s}"
        )


# This endpoint is called when deleting a calendar event.
@router.delete("/api/calendar/events/{event_id}")
async def delete_calendar_event(event_id: str, email_account_id: int):
    """Delete a Google Calendar event
    Calendar is always on the primary email account of the Account
    """
    try:
        if not email_account_id:
            raise HTTPException(status_code=400, detail="email_account_id is required")

        # Get the primary email account ID for calendar access
        primary_email_account_id = get_primary_email_account_id(email_account_id)
        print(
            f"[DELETE /api/calendar/events] Using primary email account {primary_email_account_id} for calendar access"
        )

        # Get calendar service for the primary email account
        service, error = get_calendar_service(primary_email_account_id)
        if not service:
            raise HTTPException(
                status_code=500, detail=f"Failed to get calendar service: {error}"
            )

        # Delete event from Google Calendar
        service.events().delete(calendarId="primary", eventId=event_id).execute()

        return {"status": "success", "message": "Event deleted successfully"}

    except HttpError as e:
        if e.resp.status == 404:
            raise HTTPException(status_code=404, detail="Event not found")
        raise HTTPException(status_code=500, detail=f"Google Calendar API error: {e!s}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error deleting calendar event: {e!s}"
        )


# Diagnostic endpoint to check calendar service status
@router.get("/api/calendar/status")
async def check_calendar_status(email_account_id: int | None = None):
    """Check if calendar service is available"""
    try:
        # Use provided email_account_id or default to 1 for backward compatibility
        account_id = (
            email_account_id
            if email_account_id is not None
            else EMAIL_ACCOUNT_ID_FOR_CALENDAR
        )

        service, error = get_calendar_service(account_id)
        if service:
            return {
                "status": "success",
                "message": f"Calendar service is working for email account {account_id}",
                "email_account_id": account_id,
            }
        else:
            return {
                "status": "error",
                "message": f"Calendar service failed: {error}",
                "email_account_id": account_id,
                "error": error,
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Exception: {e!s}",
            "email_account_id": email_account_id,
        }


# This endpoint is called to fetch Moodle calendar events from the subscribed calendar.
@router.get("/api/calendar/moodle")
async def get_moodle_calendar_events(
    email_account_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
):
    """
    Get events from the subscribed Moodle calendar.
    Returns events grouped by date, marked with category 'Moodle'.
    """
    try:
        if email_account_id is None:
            email_account_id = EMAIL_ACCOUNT_ID_FOR_CALENDAR

        result = get_moodle_events_for_api(email_account_id, start_date, end_date)

        if "error" in result and result.get("events") == {}:
            raise HTTPException(status_code=500, detail=result["error"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching Moodle events: {e!s}"
        )
