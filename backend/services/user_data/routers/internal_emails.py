"""
User_data service — /internal/emails/* routes consumed by MCP.

Spec section 3.4 (Phase 5.8). Every handler below is a body lifted out of
``backend/mcp_server.py`` — ``sync_emails`` (``:311-372``) and the two branches
of ``search_emails`` (``:226-307``) — with the in-process ``db_manager`` /
``embed_and_store`` calls replaced by Database-service and Vector-DB-service
calls (R1, R4). Nothing else changed.

Three things that look like bugs but are load-bearing:

* The MCP sync flavour is **not** the public ``/api/sync`` flavour. It takes
  ``max_results``, queries ``in:inbox category:primary`` with **no**
  ``newer_than:`` window, never touches the Go sync server, and cleans each body
  with ``clean_email`` before embedding. That last part is why this path embeds
  correctly today while the public fallback needed fix S1.
* ``new_emails`` here really is the count of rows *newly inserted*
  (``len(db_manager.save_emails(...))``), which is exactly the ``count`` the
  Database service returns (addendum A1). This is the one place that wiring is
  right; do **not** copy it into ``/api/sync``, whose ``new_emails`` reports
  mails *fetched*.
* ``GET /internal/emails/by-message-id/{message_id}`` resolves on
  ``message_id`` alone with no account filter. ``emails.message_id`` has no
  unique constraint, so two mailboxes caching the same message make the answer
  arbitrary. That is issue **X3**, preserved deliberately.

Every handler keeps the MCP tools' ``{"status": "error", "error": str(e)}``
envelope on an unexpected exception, returned with HTTP 200, because that dict
is what the tool handed to the LLM and MCP passes it straight through in Wave 5.
The pure pass-through routes instead re-raise the Database service's status and
``detail`` verbatim (R7) so a missing email stays a 404.

``context["last_sync_time"]`` is **not** set here: it is MCP-process-local state
(issue **X9**) and stays in the MCP service.

Route registration order matters — FastAPI matches in order, so ``/count``,
``/stats`` and ``/search/*`` are declared before any path-parameter route.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from backend.libs.common.errors import UpstreamError, raise_http_from_upstream
from backend.libs.common.http import AsyncServiceClient
from backend.libs.contracts.emails import EmailDTO, EmailInputDTO
from backend.libs.contracts.vectors import (
    EmbedAndStoreRequest,
    QueryRequest,
    QueryResponse,
    VectorDocDTO,
)
from backend.services.user_data import gmail
from backend.services.user_data.clean_mails import clean_email
from backend.services.user_data.clients import (
    get_database_client,
    get_vector_db_client,
)
from backend.services.user_data.sync import SYNC_TIMEOUT

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal-emails"])


def _embed_and_store_request(mails: list[dict]) -> dict:
    """``embed_and_store(mails)`` transposed into ``EmbedAndStoreRequest`` (A2).

    ``date_sent`` is stringified because these mails come straight out of
    ``prepare_email_data`` and still carry ``datetime`` objects, which JSON
    cannot carry. ``str()`` is what ``store_in_vector_db`` applied anyway, so
    the metadata Chroma ends up with is byte-identical to today's.
    """
    return EmbedAndStoreRequest(
        texts=[mail.get("body_text", "") for mail in mails],
        metadatas=[
            {
                "message_id": mail.get("message_id", ""),
                "sender": mail.get("sender", ""),
                "subject": mail.get("subject", ""),
                "date_sent": str(mail.get("date_sent", "")),
            }
            for mail in mails
        ],
        ids=[mail.get("message_id", "") or "" for mail in mails],
    ).model_dump(mode="json")


def _search_result(email: EmailDTO) -> dict[str, Any]:
    """The per-email dict both ``search_emails`` branches return."""
    return {
        "message_id": email.message_id,
        "sender": email.sender,
        "subject": email.subject,
        "date": email.date_sent.isoformat() if email.date_sent else None,
        "snippet": email.snippet,
    }


# ---------------------------------------------------------------------------
# POST /internal/emails/sync — mcp_server.sync_emails (:311-372)
# ---------------------------------------------------------------------------


@router.post("/internal/emails/sync")
async def sync_emails(
    email_account_id: int,
    max_results: int = 50,
    database: AsyncServiceClient = Depends(get_database_client),
    vector_db: AsyncServiceClient = Depends(get_vector_db_client),
) -> dict:
    """
    Fetch new emails from Gmail for a specific email account and store them in the database and vector store.

    Args:
        email_account_id: The ID of the email account whose emails to sync
        max_results: Maximum number of emails to fetch (default: 50)

    Returns:
        Status of sync operation including counts of fetched and new emails
    """
    try:
        logger.info(
            f"Syncing emails for email account {email_account_id}, max_results: {max_results}"
        )

        # Get Gmail service
        service = gmail.get_service(email_account_id)

        # Fetch message IDs from Gmail
        ids = gmail.list_message_ids(
            service, query="in:inbox category:primary", max_results=max_results
        )

        if not ids:
            return {
                "status": "success",
                "message": "No new emails found",
                "total_fetched": 0,
                "new_emails": 0,
            }

        # Prepare email data
        email_data = gmail.prepare_email_data(service, ids)

        # Save to database. The bare-array body and the {"saved", "count"}
        # envelope are addendum A1; ``count`` is len(save_emails(...)), i.e.
        # exactly the ``len(saved_emails)`` this tool reported.
        save_response = await database.post(
            "/emails",
            params={"email_account_id": email_account_id},
            json=[EmailInputDTO(**mail).model_dump(mode="json") for mail in email_data],
            timeout=SYNC_TIMEOUT,
        )
        new_emails = save_response.json()["count"]

        # Clean and prepare emails for embedding. Built from ``email_data``,
        # never from the save response (addendum C1).
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

        # Embed in vector database
        if emails_for_embedding:
            await vector_db.post(
                "/embed-and-store",
                json=_embed_and_store_request(emails_for_embedding),
                timeout=SYNC_TIMEOUT,
            )

        logger.info(f"Sync complete: {new_emails} new emails")
        return {
            "status": "success",
            "total_fetched": len(ids),
            "new_emails": new_emails,
        }
    except Exception as e:
        logger.error(f"Error syncing emails: {e}")
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Literal paths first (see the route-ordering note in the module docstring)
# ---------------------------------------------------------------------------


@router.get("/internal/emails/count")
async def count_emails(
    email_account_id: int,
    database: AsyncServiceClient = Depends(get_database_client),
) -> dict:
    """Number of cached emails for one mailbox.

    Feeds the email counts in ``get_account_info`` / ``get_email_account_info``
    and the ``mailbox://info`` resource.
    """
    try:
        response = await database.get(
            "/emails/count", params={"email_account_id": email_account_id}
        )
    except UpstreamError as error:
        raise_http_from_upstream(error)
    return response.json()


@router.get("/internal/emails/stats")
async def email_stats(
    database: AsyncServiceClient = Depends(get_database_client),
) -> dict:
    """Total cached emails plus the newest ``date_sent`` across all mailboxes.

    Feeds the email fields of the ``system://status`` resource.
    """
    try:
        response = await database.get("/stats/emails")
    except UpstreamError as error:
        raise_http_from_upstream(error)
    return response.json()


@router.get("/internal/emails/search/gmail")
async def search_emails_gmail(
    query: str,
    email_account_id: int | None = None,
    limit: int = 10,
    database: AsyncServiceClient = Depends(get_database_client),
) -> dict:
    """The non-semantic branch of ``search_emails`` (``mcp_server.py:272-304``).

    Gmail answers which message ids match the query; the rows themselves come
    from the cache, so an id Gmail knows about but we never synced is simply
    absent — same as today.
    """
    try:
        # Use Gmail API search - requires email_account_id
        if email_account_id is None:
            return {
                "status": "error",
                "error": "email_account_id required for Gmail API search",
            }

        logger.info(
            f"Gmail API search for email account {email_account_id}: '{query}'"
        )
        service = gmail.get_service(email_account_id)
        ids = gmail.list_message_ids(service, query=query, max_results=limit)

        # Get email details from database
        response = await database.get(
            "/emails/search",
            params={
                "email_account_id": email_account_id,
                "message_ids": ",".join(ids),
                "limit": limit,
            },
        )
        email_results = [_search_result(EmailDTO(**row)) for row in response.json()]

        return {
            "status": "success",
            "results": email_results,
            "count": len(email_results),
            "search_type": "gmail_api",
        }
    except Exception as e:
        logger.error(f"Error searching emails: {e}")
        return {"status": "error", "error": str(e)}


@router.get("/internal/emails/search/semantic")
async def search_emails_semantic(
    query: str,
    top_k: int = 10,
    email_account_id: int | None = None,
    database: AsyncServiceClient = Depends(get_database_client),
    vector_db: AsyncServiceClient = Depends(get_vector_db_client),
) -> dict:
    """The semantic branch of ``search_emails`` (``mcp_server.py:242-271``).

    This is the **only** route to the Vector DB service for semantic retrieval:
    it serves both MCP's ``search_emails(use_semantic=True)`` and ``/api/query``
    retrieval, because MCP must never call Vector DB itself (R4). ``documents``
    carries the raw ``VectorDocDTO`` hits ``/api/query`` consumes; ``results``
    carries the flattened per-email dicts the tool returned.

    The account filter replaces the original's per-result
    ``filter_by(message_id=...).first()`` loop with the Database service's batch
    ``/emails/by-message-ids``. That route runs the very same per-id
    ``.first()`` queries in one session (addendum A1), so the arbitrary
    first-match semantics of X3 are identical rather than merely equivalent.
    """
    try:
        # Use vector database for semantic search
        logger.info(f"Semantic search: '{query}' (limit: {top_k})")
        response = await vector_db.post(
            "/query",
            json=QueryRequest(query=query, top_k=top_k).model_dump(mode="json"),
        )
        documents = QueryResponse(**response.json()).documents

        if email_account_id is not None:
            documents = await _filter_by_account(documents, email_account_id, database)

        email_results = [
            {
                "message_id": doc.metadata.get("message_id"),
                "sender": doc.metadata.get("sender"),
                "subject": doc.metadata.get("subject"),
                "date": doc.metadata.get("date_sent"),
                "snippet": doc.page_content[:200]
                if len(doc.page_content) > 200
                else doc.page_content,
            }
            for doc in documents
        ]

        return {
            "status": "success",
            "results": email_results,
            "count": len(email_results),
            "search_type": "semantic",
            "documents": [doc.model_dump(mode="json") for doc in documents],
        }
    except Exception as e:
        logger.error(f"Error searching emails: {e}")
        return {"status": "error", "error": str(e)}


async def _filter_by_account(
    documents: list[VectorDocDTO],
    email_account_id: int,
    database: AsyncServiceClient,
) -> list[VectorDocDTO]:
    """Keep only hits whose cached row belongs to ``email_account_id``.

    A hit whose metadata carries no ``message_id``, or whose ``message_id`` has
    no cached row, drops out — the original's ``if email and ...`` did the same.
    """
    wanted = [
        doc.metadata.get("message_id")
        for doc in documents
        if doc.metadata.get("message_id")
    ]
    if not wanted:
        return []

    rows = await database.get("/emails/by-message-ids", params={"ids": ",".join(wanted)})
    owned = {
        row["message_id"]
        for row in rows.json()
        if row["email_account_id"] == email_account_id
    }
    return [doc for doc in documents if doc.metadata.get("message_id") in owned]


# ---------------------------------------------------------------------------
# Pass-through reads
# ---------------------------------------------------------------------------


@router.get("/internal/emails")
async def list_emails(
    email_account_id: int,
    limit: int = 50,
    database: AsyncServiceClient = Depends(get_database_client),
) -> list[dict]:
    """Cached emails for one mailbox, newest first.

    Backs the ``get_email_account_emails`` tool and the ``mail://inbox/{id}``
    resource.
    """
    try:
        response = await database.get(
            "/emails", params={"email_account_id": email_account_id, "limit": limit}
        )
    except UpstreamError as error:
        raise_http_from_upstream(error)
    return response.json()


@router.get("/internal/emails/by-message-id/{message_id}")
async def email_by_message_id(
    message_id: str,
    database: AsyncServiceClient = Depends(get_database_client),
) -> dict:
    """One cached email by Gmail ``message_id``, with **no** account filter.

    Issue **X3**, preserved: ``emails.message_id`` has no unique constraint, so
    when two mailboxes cached the same message this returns an arbitrary one of
    them. Backs the ``get_email_details`` tool and ``mail://email/{id}``.
    """
    try:
        response = await database.get(f"/emails/by-message-id/{message_id}")
    except UpstreamError as error:
        raise_http_from_upstream(error)
    return response.json()


__all__ = ["router"]
