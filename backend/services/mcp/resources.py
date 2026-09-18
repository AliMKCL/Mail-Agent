"""
MCP resources — read-only data sources (Spec 6.3).

Nine resources, the same URI templates and the same
``json.dumps(..., indent=2)`` output as the monolith's
``backend/mcp_server.py``. Note the deliberate asymmetry that is preserved
here: the success payloads are indented, while the ``{"error": ...}`` envelopes
are **not** (``json.dumps({"error": ...})`` with no ``indent``), and they carry
no ``"status"`` key — unlike the tools.

Downstream owners:

- ``mail://...``                 → User_data ``/internal/emails/*``
- ``calendar://events``          → this service's own ``get_calendar_events`` tool
- ``calendar://event/{id}``      → User_data ``/internal/calendar/events/{id}``
- ``account://...``/``mailbox://...`` → Accounts ``/internal/*`` (+ User_data counts)
- ``system://status``            → Accounts ``/internal/stats`` + User_data
  ``/internal/emails/stats`` + the process-local ``context`` (X9)
"""

import json
import logging
from datetime import datetime

from backend.libs.common.errors import UpstreamError
from backend.services.mcp import clients
from backend.services.mcp.config import settings
from backend.services.mcp.tools.calendar import get_calendar_events
from backend.services.mcp.tools.emails import context

logger = logging.getLogger(__name__)

# X4 (preserved): calendar work is hardwired to email account 1.
CALENDAR_EMAIL_ACCOUNT_ID = settings.CALENDAR_EMAIL_ACCOUNT_ID


async def get_inbox_resource(email_account_id: int) -> str:
    """
    Resource: Get inbox emails for a specific email account.

    URI: mail://inbox/{email_account_id}
    Example: mail://inbox/1

    Returns JSON string with list of emails for the specified email account.
    """
    try:
        user_data_client = clients.get_user_data_client()
        emails = (
            await user_data_client.get(
                "/internal/emails",
                params={"email_account_id": int(email_account_id), "limit": 50},
            )
        ).json()

        email_list = [
            {
                "message_id": email["message_id"],
                "subject": email["subject"],
                "sender": email["sender"],
                "date": email["date_sent"],
                "snippet": email["snippet"]
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


async def get_email_resource(message_id: str) -> str:
    """
    Resource: Get full details of a specific email by message ID.

    URI: mail://email/{message_id}
    Example: mail://email/18f3a2b4c5d6e7f8

    Returns JSON string with complete email details.
    """
    try:
        user_data_client = clients.get_user_data_client()
        try:
            email = (
                await user_data_client.get(
                    f"/internal/emails/by-message-id/{message_id}"
                )
            ).json()
        except UpstreamError as err:
            if err.status == 404:
                return json.dumps({"error": f"Email with message_id {message_id} not found"})
            raise

        email_data = {
            "message_id": email["message_id"],
            "thread_id": email["thread_id"],
            "subject": email["subject"],
            "sender": email["sender"],
            "recipient": email["recipient"],
            "date_sent": email["date_sent"],
            "snippet": email["snippet"],
            "body_text": email["body_text"],
            "body_html": email["body_html"],
            "email_account_id": email["email_account_id"]
        }

        return json.dumps(email_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting email resource: {e}")
        return json.dumps({"error": str(e)})


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


async def get_calendar_event_resource(event_id: str) -> str:
    """
    Resource: Get details of a specific calendar event.

    URI: calendar://event/{event_id}
    Example: calendar://event/abc123def456

    Returns JSON string with event details from the main calendar (email account ID 1).
    """
    try:
        user_data_client = clients.get_user_data_client()
        event = (
            await user_data_client.get(f"/internal/calendar/events/{event_id}")
        ).json()

        # The route emits this resource's own bare error envelope when the
        # calendar service is unavailable.
        if "error" in event:
            return json.dumps({"error": event["error"]})

        event_data = {
            "id": event["id"],
            "title": event["title"],
            "start": event["start"],
            "end": event["end"],
            "description": event["description"],
            "link": event["link"],
            "created": event["created"],
            "updated": event["updated"]
        }

        # Add category if exists
        if "category" in event:
            event_data["category"] = event["category"]

        return json.dumps(event_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting calendar event resource: {e}")
        return json.dumps({"error": str(e)})


async def get_accounts_resource() -> str:
    """
    Resource: Get list of all registered accounts in the system.

    URI: account://list

    Returns JSON string with all accounts.
    """
    try:
        accounts_client = clients.get_accounts_client()
        accounts = (await accounts_client.get("/internal/accounts")).json()

        account_list = [
            {
                "id": account["id"],
                "primary_email": account["primary_email"],
                "created_at": account["created_at"]
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


async def get_email_accounts_resource() -> str:
    """
    Resource: Get list of all email accounts in the system.

    URI: mailbox://list

    Returns JSON string with all email accounts.
    """
    try:
        accounts_client = clients.get_accounts_client()
        email_accounts = (
            await accounts_client.get("/internal/email-accounts")
        ).json()

        email_account_list = [
            {
                "id": ea["id"],
                "email": ea["email"],
                "provider": ea["provider"],
                "account_id": ea["account_id"],
                "is_primary": bool(ea["is_primary"]),
                "created_at": ea["created_at"]
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


async def get_account_resource(account_id: int) -> str:
    """
    Resource: Get detailed information about a specific account.

    URI: account://info/{account_id}
    Example: account://info/1

    Returns JSON string with account details.
    """
    try:
        accounts_client = clients.get_accounts_client()
        try:
            detail = (
                await accounts_client.get(
                    f"/internal/accounts/{int(account_id)}/detail"
                )
            ).json()
        except UpstreamError as err:
            if err.status == 404:
                return json.dumps({"error": f"Account with ID {account_id} not found"})
            raise

        account = detail["account"]
        # Email accounts for this account
        email_accounts = detail["email_accounts"]

        # Count total emails across all email accounts
        user_data_client = clients.get_user_data_client()
        total_emails = 0
        for ea in email_accounts:
            email_count = (
                await user_data_client.get(
                    "/internal/emails/count",
                    params={"email_account_id": ea["id"]},
                )
            ).json()["count"]
            total_emails += email_count

        account_data = {
            "id": account["id"],
            "primary_email": account["primary_email"],
            "created_at": account["created_at"],
            "updated_at": account["updated_at"],
            "email_accounts_count": len(email_accounts),
            "total_emails": total_emails,
            "email_accounts": [
                {
                    "id": ea["id"],
                    "email": ea["email"],
                    "provider": ea["provider"],
                    "is_primary": bool(ea["is_primary"])
                }
                for ea in email_accounts
            ]
        }

        return json.dumps(account_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting account resource: {e}")
        return json.dumps({"error": str(e)})


async def get_email_account_resource(email_account_id: int) -> str:
    """
    Resource: Get detailed information about a specific email account.

    URI: mailbox://info/{email_account_id}
    Example: mailbox://info/1

    Returns JSON string with email account details.
    """
    try:
        accounts_client = clients.get_accounts_client()
        try:
            detail = (
                await accounts_client.get(
                    f"/internal/email-accounts/{int(email_account_id)}/detail"
                )
            ).json()
        except UpstreamError as err:
            if err.status == 404:
                return json.dumps({"error": f"Email account with ID {email_account_id} not found"})
            raise

        email_account = detail["email_account"]
        # The parent account's primary email travels with the detail route
        account_primary_email = detail["primary_email"]

        # Get email count for this email account
        user_data_client = clients.get_user_data_client()
        email_count = (
            await user_data_client.get(
                "/internal/emails/count",
                params={"email_account_id": email_account["id"]},
            )
        ).json()["count"]

        email_account_data = {
            "id": email_account["id"],
            "email": email_account["email"],
            "provider": email_account["provider"],
            "account_id": email_account["account_id"],
            "is_primary": bool(email_account["is_primary"]),
            "account_primary_email": account_primary_email,
            "email_count": email_count,
            "created_at": email_account["created_at"],
            "updated_at": email_account["updated_at"]
        }

        return json.dumps(email_account_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting email account resource: {e}")
        return json.dumps({"error": str(e)})


async def get_system_status_resource() -> str:
    """
    Resource: Get system status and statistics.

    URI: system://status

    Returns JSON string with system information including total accounts, email accounts, emails, last sync time.
    """
    try:
        accounts_client = clients.get_accounts_client()
        user_data_client = clients.get_user_data_client()

        account_stats = (await accounts_client.get("/internal/stats")).json()
        email_stats = (await user_data_client.get("/internal/emails/stats")).json()

        status_data = {
            "total_accounts": account_stats["total_accounts"],
            "total_email_accounts": account_stats["total_email_accounts"],
            "total_emails": email_stats["total_emails"],
            "latest_email_date": email_stats["latest_email_date"],
            "last_sync_time": context.get("last_sync_time"),
            "calendar_email_account_id": CALENDAR_EMAIL_ACCOUNT_ID,
            "timestamp": datetime.now().isoformat()
        }

        return json.dumps(status_data, indent=2)
    except Exception as e:
        logger.error(f"Error getting system status resource: {e}")
        return json.dumps({"error": str(e)})


__all__ = [
    "CALENDAR_EMAIL_ACCOUNT_ID",
    "get_inbox_resource",
    "get_email_resource",
    "get_calendar_events_resource",
    "get_calendar_event_resource",
    "get_accounts_resource",
    "get_email_accounts_resource",
    "get_account_resource",
    "get_email_account_resource",
    "get_system_status_resource",
]
