"""
User_data service — public /api/emails and /api/sync routes.

Spec sections 3.1 and 3.4. Relocated from ``backend/controllers/emails.py``
(Phase 5.6). Literal paths, methods, status codes, ``detail`` strings and JSON
keys are unchanged (R7), and the ``limiter.check`` blocks are verbatim (R8) —
same scope, identifier, endpoint, tokens, capacity, refill_rate, the same 429
``detail`` string and the same three headers.

``dependencies.db_manager`` became the Database service (R1) and
``dependencies.limiter`` became ``clients.get_limiter``, resolved through
``Depends`` so tests can override it.

``/api/sync``'s body — including its own ``limiter.check`` block — lives in
``backend.services.user_data.sync``. It is kept whole there because the
monolith had the rate-limit check inside the same ``try`` that produces the
friendly ``authentication_required`` / ``token_expired`` bodies; splitting them
across two modules would have changed which handler sees a limiter failure.
"""

from fastapi import APIRouter, Depends, HTTPException
from ratelimiter.client.ratelimiter_client import RateLimiterClient

from backend.libs.common.http import AsyncServiceClient
from backend.libs.contracts.emails import EmailDTO
from backend.services.user_data import sync
from backend.services.user_data.clients import (
    get_database_client,
    get_limiter,
    get_vector_db_client,
)

router = APIRouter(tags=["emails"])


# This endpoint is called when the user fetches emails for a specific email account.
@router.get("/api/emails")
async def get_emails(
    email_account_id: int | None = None,
    limit: int = 50,
    limiter: RateLimiterClient = Depends(get_limiter),
    database: AsyncServiceClient = Depends(get_database_client),
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

        result = limiter.check(
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
        response = await database.get(
            "/emails", params={"email_account_id": email_account_id, "limit": limit}
        )
        stored_emails = [EmailDTO(**row) for row in response.json()]

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
async def sync_emails(
    email_account_id: int | None = None,
    limiter: RateLimiterClient = Depends(get_limiter),
    database: AsyncServiceClient = Depends(get_database_client),
    vector_db: AsyncServiceClient = Depends(get_vector_db_client),
):
    """
    Trigger email synchronization from Gmail
    This endpoint can be called to fetch new emails
    """
    return await sync.sync_emails(
        email_account_id,
        limiter=limiter,
        database=database,
        vector_db=vector_db,
    )
