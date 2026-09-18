"""
MCP tools backed by the User_data service: search_emails, sync_emails,
get_email_details, get_email_account_emails.

Spec section 3.6. Filled by agent M1.

Plain ``async def``s called in-process (never over HTTP per tool call). Each
body is the monolith's ``backend/mcp_server.py`` body with its database and
Gmail work replaced by one call to User_data's ``/internal/emails/*`` surface.
Return dicts are key-for-key identical to today's, error sentences included.

R4: the semantic branch of ``search_emails`` goes through
``/internal/emails/search/semantic``. MCP never imports chromadb and never
calls the Vector DB service.
"""

import logging
from datetime import datetime
from typing import Optional

from backend.libs.common.errors import UpstreamError
from backend.services.mcp import clients

logger = logging.getLogger(__name__)

# X9 (preserved): ``last_sync_time`` is MCP-process-local. It is set only by the
# ``sync_emails`` tool, read only by the ``system://status`` resource, reset on
# restart, and deliberately not shared with the public ``/api/sync``. The dict
# lives here — beside its only writer — and ``mcp_server.py`` / ``resources.py``
# bind this same object so the whole process shares one piece of state.
context = {
    "last_sync_time": None
}

# The MCP sync flavour walks a whole mailbox through the Gmail API and the
# embedding model, so it gets the same generous budget the public sync has
# (Spec 3.7 grants /api/sync 1200 s); the default 30 s client timeout would cut
# a real sync off mid-flight.
SYNC_TIMEOUT_SECONDS = 1200.0


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
        user_data_client = clients.get_user_data_client()
        if use_semantic:
            # Use vector database semantic search, via User_data (R4)
            logger.info(f"Semantic search: '{query}' (limit: {limit})")
            params = {"query": query, "top_k": limit}
            # Filter by email_account_id if specified
            if email_account_id is not None:
                params["email_account_id"] = email_account_id

            payload = (
                await user_data_client.get(
                    "/internal/emails/search/semantic", params=params
                )
            ).json()
            if payload.get("status") != "success":
                return payload

            # ``documents`` is for /api/query's retrieval context; the tool's
            # dict never carried it.
            return {
                "status": "success",
                "results": payload["results"],
                "count": payload["count"],
                "search_type": "semantic"
            }
        else:
            # Use Gmail API search - requires email_account_id
            if email_account_id is None:
                return {"status": "error", "error": "email_account_id required for Gmail API search"}

            logger.info(f"Gmail API search for email account {email_account_id}: '{query}'")
            return (
                await user_data_client.get(
                    "/internal/emails/search/gmail",
                    params={
                        "email_account_id": email_account_id,
                        "query": query,
                        "limit": limit,
                    },
                )
            ).json()
    except Exception as e:
        logger.error(f"Error searching emails: {e}")
        return {"status": "error", "error": str(e)}


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

        user_data_client = clients.get_user_data_client()
        payload = (
            await user_data_client.post(
                "/internal/emails/sync",
                params={
                    "email_account_id": email_account_id,
                    "max_results": max_results,
                },
                timeout=SYNC_TIMEOUT_SECONDS,
            )
        ).json()

        # "No new emails found" and the error envelope both return early, before
        # last_sync_time is touched — exactly as they did in the monolith.
        if payload.get("status") != "success" or "message" in payload:
            return payload

        context["last_sync_time"] = datetime.now().isoformat()

        logger.info(f"Sync complete: {payload['new_emails']} new emails")
        return {
            "status": "success",
            "total_fetched": payload["total_fetched"],
            "new_emails": payload["new_emails"],
            "last_sync": context["last_sync_time"]
        }
    except Exception as e:
        logger.error(f"Error syncing emails: {e}")
        return {"status": "error", "error": str(e)}


# Uses NORMAL DB
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

        user_data_client = clients.get_user_data_client()
        try:
            email = (
                await user_data_client.get(
                    f"/internal/emails/by-message-id/{message_id}"
                )
            ).json()
        except UpstreamError as err:
            if err.status == 404:
                return {"status": "error", "error": f"Email with message_id {message_id} not found"}
            raise

        return {
            "status": "success",
            "email": {
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
        }
    except Exception as e:
        logger.error(f"Error getting email details: {e}")
        return {"status": "error", "error": str(e)}


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

        user_data_client = clients.get_user_data_client()
        emails = (
            await user_data_client.get(
                "/internal/emails",
                params={"email_account_id": email_account_id, "limit": limit},
            )
        ).json()

        email_list = [
            {
                "message_id": email["message_id"],
                "subject": email["subject"],
                "sender": email["sender"],
                "recipient": email["recipient"],
                "date_sent": email["date_sent"],
                "snippet": email["snippet"]
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


__all__ = [
    "context",
    "search_emails",
    "sync_emails",
    "get_email_details",
    "get_email_account_emails",
]
