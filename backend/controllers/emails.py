"""
Email retrieval and Gmail synchronization endpoints.
"""

from datetime import datetime

import requests
from fastapi import APIRouter, HTTPException

from backend import dependencies
from backend.databases.vector_database import embed_and_store, store_in_vector_db
from backend.services.gmail_read import (
    get_service,
    list_message_ids,
    prepare_email_data,
)

router = APIRouter(tags=["emails"])


# This endpoint is called when the user fetches emails for a specific email account.
@router.get("/api/emails")
async def get_emails(
    email_account_id: int | None = None, limit: int = 50
) -> list[dict]:
    """
    Get emails for a specific email account from database
    Returns email data formatted for frontend display
    """
    try:
        if email_account_id is None:
            raise HTTPException(
                status_code=400, detail="email_account_id parameter is required"
            )

        # ==================== RATE LIMITED EMAILS REFRESH / email account scope ====================

        result = dependencies.limiter.check(
            scope="global",
            identifier="all",
            endpoint="api/emails",
            tokens=1,
            capacity=10,  # Custom capacity   (Optional)
            refill_rate=10,  # Per hour          (Optional)
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

        # Fetch stored emails for the specified email account
        stored_emails = dependencies.db_manager.get_email_account_emails(
            email_account_id, limit=limit
        )

        # Format emails for API response
        emails = []
        for email in stored_emails:
            emails.append(
                {
                    "id": email.message_id,
                    "subject": email.subject or "No Subject",
                    "sender": email.sender or "Unknown",
                    "recipient": email.recipient or "Unknown",
                    "date_sent": email.date_sent.isoformat()
                    if email.date_sent
                    else None,
                    "snippet": email.snippet or "",
                    "body_text": email.body_text or "",
                    "body_html": email.body_html or "",
                    "created_at": email.created_at.isoformat(),
                }
            )

        return emails

    except HTTPException:
        raise  # Re-raise HTTPExceptions (including 429 rate limit errors) without modification
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching emails: {e!s}")


# This endpoint is called when the sync button is pressed to fetch new emails.
@router.get("/api/sync")
async def sync_emails(email_account_id: int | None = None):
    """
    Trigger email synchronization from Gmail
    This endpoint can be called to fetch new emails
    """

    # 50 Mails took 70 seconds (Fetch, clean, embed).
    try:
        # ==================== RATE LIMITED LLM QUERY / global scope ====================
        result = dependencies.limiter.check(
            scope="global",
            identifier="all",
            endpoint="api/sync",
            tokens=1,
            capacity=10,
            refill_rate=10,
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
        # ================================================================

        if email_account_id is None:
            raise HTTPException(
                status_code=400, detail="email_account_id parameter is required"
            )

        # Create Gmail service (this handles OAuth flow if needed)
        service = get_service(email_account_id)

        # Get the date of the most recent email we have stored
        latest_date = dependencies.db_manager.get_latest_email_date(email_account_id)

        flag = False
        # Build Gmail search query to fetch only newer emails
        query = ""
        if latest_date:
            # Calculate days since the latest email for newer_than syntax
            days_since = (datetime.now() - latest_date).days
            if days_since == 0 and flag == False:
                # If it's the same day, use hours
                hours_since = (datetime.now() - latest_date).total_seconds() / 3600
                if hours_since < 1:
                    query = "newer_than:1h"  # Search last hour if very recent
                else:
                    query = f"newer_than:{int(hours_since)}h"
            else:
                query = f"newer_than:{days_since}d"

            print(f"DEBUG: Latest email date: {latest_date}")
            print(f"DEBUG: Days since latest: {days_since}")
            print(f"DEBUG: Gmail query: '{query}'")
        else:
            print("DEBUG: No previous emails found, fetching recent emails")

        # Build query to fetch Primary inbox emails (matches Gmail dashboard)
        primary_query = "in:inbox category:primary"
        if query:
            # Combine time-based query with primary inbox filter
            final_query = f"{primary_query} {query}"
        else:
            final_query = primary_query

        # Fetch email IDs from Gmail Primary inbox only
        print(f"DEBUG: About to call list_message_ids with query='{final_query}'")
        ids = list_message_ids(service, query=final_query, max_results=60)
        print(f"DEBUG: Found {len(ids)} email IDs")

        if ids:
            # Prepare and save email data

            """
            currTime = datetime.now()
            email_data = prepare_email_data(service, ids)
            elapsedTime = datetime.now() - currTime
            print("Time taken to fetch and prepare email data: ", elapsedTime)
            """

            # Send IDs to a local Go server for faster, concurrent fetching and processing.

            try:
                response = requests.post(
                    "http://localhost:8001/fetch-emails",
                    json={"email_account_id": email_account_id, "mail_ids": ids},
                    timeout=1000,
                )

                if response.status_code == 200:
                    res = response.json()
                    if res.get("emails"):
                        email_data = res["emails"]

                        await store_in_vector_db(email_data)

                        print(f"Received {len(email_data)} mails from go server")
                    else:
                        print("No new emails received from Go server")
                        email_data = []
                else:
                    print(f"Go server error: {response.status_code} - {response.text}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error fetching emails from Go service: {response.text}",
                    )

            # Fall back to Python implementation
            except requests.exceptions.RequestException as e:
                print(f"Connection error to Go server: {e}")
                print("Using Python approach")
                email_data = prepare_email_data(service, ids)
                saved_emails = dependencies.db_manager.save_emails(
                    email_account_id, email_data
                )
                await embed_and_store(saved_emails)

            except Exception as e:
                print(f"Unexpected error calling Go server: {e}")
                raise HTTPException(
                    status_code=500, detail=f"Error processing emails: {e!s}"
                )

            return {
                "status": "success",
                "message": f"Synced {len(email_data)} new emails",
                "total_fetched": len(ids),
                "new_emails": len(email_data),
            }
        else:
            return {
                "status": "success",
                "message": "No new emails found",
                "total_fetched": 0,
                "new_emails": 0,
            }

    except HTTPException:
        raise  # Re-raise HTTPExceptions (including 429 rate limit errors) without modification
    except Exception as e:
        error_msg = str(e)
        if "credentials do not contain the necessary fields" in error_msg:
            return {
                "status": "error",
                "message": "OAuth credentials need to be refreshed. Please run the gmail_read.py script first to authenticate.",
                "error": "authentication_required",
            }
        elif "invalid_grant" in error_msg or "Token has been expired" in error_msg:
            return {
                "status": "error",
                "message": "OAuth token has expired. Please re-authenticate by running gmail_read.py.",
                "error": "token_expired",
            }
        else:
            raise HTTPException(
                status_code=500, detail=f"Error syncing emails: {error_msg}"
            )
