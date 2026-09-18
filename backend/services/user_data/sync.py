"""
The ``/api/sync`` pipeline.

Relocated from ``backend/controllers/emails.py:88-247`` (Spec Phase 5.5). The
body below is that endpoint's body, kept as close to verbatim as the service
boundaries allow, including the dead ``flag = False``, every ``DEBUG:`` print,
the ``max_results=60`` cap, the ``in:inbox category:primary`` filter and all
three error-string matches with their friendly JSON bodies.

What changed, and only this:

* ``dependencies.db_manager`` calls become Database-service calls
  (``GET /emails/latest-date``, ``POST /emails``) — R1.
* ``store_in_vector_db`` / ``embed_and_store`` become Vector DB service calls
  (``POST /store``, ``POST /embed-and-store``) — R4. Both request models are
  parallel-list shaped (addendum A2), so the ``mails: list[dict]`` shape is
  transposed here and rebuilt on the other side.
* **S1** — the approved fix. The Python fallback used to do
  ``await embed_and_store(db.save_emails(...))``, which could never work: those
  ORM objects are expired by ``commit()`` and detached by ``close()``
  (addendum C1), and ``embed_and_store`` calls ``.get()`` on them, which ORM
  instances do not have. So the fallback always 500ed. The embedding payload is
  now built from ``email_data`` — the dicts we already hold — with
  ``body_text = clean_email(body_text, body_html)``, mirroring the already
  correct pipeline at ``mcp_server.py:337-352``.

The rate-limit block stays inside the same ``try`` that produces the friendly
error bodies, exactly where the monolith had it, so that ordering and the
"429 before 400" precedence are preserved byte for byte (R8). The route itself
lives in ``routers/emails.py`` and resolves ``limiter`` through
``clients.get_limiter``.

``gmail`` is used as a module (``gmail.get_service(...)``) rather than
from-imported, so ``backend.services.user_data.gmail.get_service`` is the single
patch point for tests.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests
from fastapi import HTTPException

from backend.libs.common.config import settings
from backend.libs.common.http import AsyncServiceClient
from backend.libs.contracts.emails import EmailInputDTO
from backend.libs.contracts.vectors import EmbedAndStoreRequest, StoreRequest
from backend.services.user_data import gmail
from backend.services.user_data.clean_mails import clean_email

#: Vector DB and Database writes used to be in-process calls with no timeout at
#: all. Embedding 60 mails takes over a minute, so the 30 s client default would
#: turn a working sync into a failure.
SYNC_TIMEOUT = 1200.0


def _metadata(mail: dict) -> dict[str, Any]:
    """The metadata dict ``vector_database.py`` builds for every mail.

    ``date_sent`` is *not* stringified here for the Go path: the Vector DB
    service applies ``str()`` on receipt, exactly as the store function always
    did, so a JSON ``null`` still becomes the literal ``"None"`` it becomes
    today.
    """
    return {
        "message_id": mail.get("message_id", ""),
        "sender": mail.get("sender", ""),
        "subject": mail.get("subject", ""),
        "date_sent": mail.get("date_sent", ""),
    }


def _store_request(mails: list[dict]) -> dict:
    """``store_in_vector_db(mails)`` transposed into ``StoreRequest`` (A2)."""
    return StoreRequest(
        texts=[mail.get("body_text", "") for mail in mails],
        embeddings=[mail.get("embedding", []) for mail in mails],
        metadatas=[_metadata(mail) for mail in mails],
        ids=[mail.get("message_id", "") for mail in mails],
    ).model_dump(mode="json")


def _embed_and_store_request(mails: list[dict]) -> dict:
    """``embed_and_store(mails)`` transposed into ``EmbedAndStoreRequest`` (A2).

    ``date_sent`` *is* stringified here because the mails come straight out of
    ``prepare_email_data`` and still carry ``datetime`` objects, which JSON
    cannot carry. ``str()`` is what the store applies anyway, so the metadata
    Chroma ends up with is identical to the MCP path's.
    """
    metadatas = []
    for mail in mails:
        metadata = _metadata(mail)
        metadata["date_sent"] = str(mail.get("date_sent", ""))
        metadatas.append(metadata)

    return EmbedAndStoreRequest(
        texts=[mail.get("body_text", "") for mail in mails],
        metadatas=metadatas,
        ids=[mail.get("message_id", "") for mail in mails],
    ).model_dump(mode="json")


async def sync_emails(
    email_account_id: int | None,
    *,
    limiter,
    database: AsyncServiceClient,
    vector_db: AsyncServiceClient,
):
    """
    Trigger email synchronization from Gmail
    This endpoint can be called to fetch new emails
    """

    # 50 Mails took 70 seconds (Fetch, clean, embed).
    try:
        # ==================== RATE LIMITED LLM QUERY / global scope ====================
        result = limiter.check(
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
        service = gmail.get_service(email_account_id)

        # Get the date of the most recent email we have stored
        latest_response = await database.get(
            "/emails/latest-date", params={"email_account_id": email_account_id}
        )
        raw_latest_date = latest_response.json()["date_sent"]
        latest_date = (
            datetime.fromisoformat(raw_latest_date) if raw_latest_date else None
        )

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
        ids = gmail.list_message_ids(service, query=final_query, max_results=60)
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
                    f"{settings.GO_SYNC_URL}/fetch-emails",
                    json={"email_account_id": email_account_id, "mail_ids": ids},
                    timeout=1000,
                )

                if response.status_code == 200:
                    res = response.json()
                    if res.get("emails"):
                        email_data = res["emails"]

                        await vector_db.post(
                            "/store",
                            json=_store_request(email_data),
                            timeout=SYNC_TIMEOUT,
                        )

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
                email_data = gmail.prepare_email_data(service, ids)
                await database.post(
                    "/emails",
                    params={"email_account_id": email_account_id},
                    json=[
                        EmailInputDTO(**mail).model_dump(mode="json")
                        for mail in email_data
                    ],
                    timeout=SYNC_TIMEOUT,
                )

                # [S1] Clean and prepare emails for embedding. Built from
                # email_data, never from the save response (addendum C1).
                emails_for_embedding = []
                for email in email_data:
                    cleaned_content = clean_email(
                        email.get("body_text", ""), email.get("body_html")
                    )
                    emails_for_embedding.append(
                        {
                            "message_id": email.get("message_id"),
                            "sender": email.get("sender"),
                            "subject": email.get("subject"),
                            "date_sent": email.get("date_sent"),
                            "body_text": cleaned_content,
                        }
                    )

                if emails_for_embedding:
                    await vector_db.post(
                        "/embed-and-store",
                        json=_embed_and_store_request(emails_for_embedding),
                        timeout=SYNC_TIMEOUT,
                    )

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
