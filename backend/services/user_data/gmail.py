"""
Reads Gmail for one email account.

Relocated from ``backend/services/gmail_read.py`` (Spec Phase 5.1). Everything
below ``get_service`` is **verbatim**, progress prints included.

``get_service`` keeps its exact synchronous signature and return contract
(Spec 3.4 / quirk X10) because existing tests patch it by name. Only its
innards changed: this service no longer owns credentials (R2), so instead of
loading tokens from the database and running an interactive OAuth flow itself it
asks the Accounts service for a resolved credential set
(``allow_interactive=true`` — the Gmail rung of the ladder in Spec 3.3.1) and
then builds the client locally. A 409 from Accounts is re-raised as an
``Exception`` carrying the upstream error string verbatim, so ``/api/sync``'s
error-string handlers keep matching.

The module-level database manager (R1), the ``reauth_user`` import (R2) and
the ``main()`` demo block (its behavior is covered by ``/api/sync``) are gone.
"""

from __future__ import annotations

# Standard library imports
import base64  # encoding/decoding (Gmail message bodies are base64-url encoded)
from datetime import datetime  # for parsing email dates
from typing import Dict, List, Optional  # type hints used throughout the module

# Google authentication and API client imports
from googleapiclient.discovery import build  # constructs API client objects (Gmail API client)

from backend.libs.common.errors import UpstreamError
from backend.libs.contracts.accounts import CredentialsDTO, dto_to_credentials
from backend.services.user_data.clients import get_accounts_sync_client

# Ask for Gmail and Calendar permissions
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar"
] # Ensure both scopes are selected in the OAuth client

# The fixed host/port MUST match the authorized redirect URI in your Web client.
OAUTH_HOST = "localhost"
OAUTH_PORT = 8080  # ensure http://localhost:8080/ is registered in the OAuth client


def get_service(email_account_id: int):
    """
    Create an authorized Gmail API client using credentials from the Accounts service.
    - Accounts reuses stored credentials (auto-refreshing the access token).
    - If no token or invalid, Accounts runs the OAuth flow and saves the result.

    Args:
        email_account_id: The ID of the EmailAccount (not the Account/user)
    """
    accounts = get_accounts_sync_client()

    # 1) Ask the sole credential authority (R2) to resolve usable credentials.
    #    allow_interactive=true is the Gmail rung of the ladder: on refresh
    #    failure or missing credentials, Accounts re-authenticates.
    try:
        response = accounts.get(
            f"/internal/email-accounts/{email_account_id}/credentials",
            params={"allow_interactive": "true"},
        )
    except UpstreamError as error:
        # 409 carries the same message the monolith raised from gmail_read.py:89,
        # e.g. "Authentication failed for email account 1. Cannot proceed
        # without valid credentials." Re-raise so /api/sync's error-string
        # handlers still match (Spec 3.3.1).
        body = error.body
        message = body["error"] if isinstance(body, dict) and "error" in body else str(body)
        raise Exception(message) from error

    creds = dto_to_credentials(CredentialsDTO(**response.json()))

    # 2) Build the Gmail client object with authorized credentials.
    return build("gmail", "v1", credentials=creds)


def list_message_ids(service, query: str = "", label_ids: Optional[List[str]] = None, max_results: int = 50) -> List[str]:
    """
    Return a list of message IDs using Gmail's search.
      Examples of 'query':
        - 'label:unread'
        - 'from:someone@example.com newer_than:7d'
        - 'subject:(invoice OR receipt) has:attachment'
    """
    label_ids = label_ids or []
    ids: List[str] = []
    page_token = None   # Holds gmail pagination token
    fetched = 0         # How many ids collected so far

    # Page through results until no nextPageToken remains or max_results reached.
    while fetched < max_results:
        remaining = max_results - fetched
        page_size = min(remaining, 100)  # Gmail API max per request

        # Gmail API call
        resp = service.users().messages().list(
            userId="me",
            q=query,
            labelIds=label_ids,
            maxResults=page_size,
            pageToken=page_token,
        ).execute()

        messages = resp.get("messages", [])
        ids.extend([m["id"] for m in messages])
        fetched += len(messages)

        page_token = resp.get("nextPageToken")
        if not page_token or len(messages) == 0:
            break

    return ids[:max_results]  # Ensure we don't exceed max_results


def get_message_metadata(service, msg_id: str, headers: Optional[List[str]] = None) -> Dict[str, str]:
    """
    Fetch common headers and the Gmail snippet (a short preview).
    'format="metadata"' is efficient and lets us choose which headers we want.
    """
    headers = headers or ["From", "To", "Subject", "Date"]
    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="metadata",
        metadataHeaders=headers,
    ).execute()

    hdrs = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    snippet = msg.get("snippet", "")
    return {"id": msg_id, **hdrs, "Snippet": snippet}


def _find_part(parts, mime_prefix: str) -> Optional[dict]:
    """
    Recursively search MIME parts for the first part whose type starts with mime_prefix
    (e.g., 'text/plain' or 'text/html').
    """
    if not parts:
        return None
    for p in parts:
        mime_type = p.get("mimeType", "")
        if mime_type.startswith(mime_prefix):
            return p
        if p.get("parts"):  # drill into multipart/alternative, etc.
            found = _find_part(p["parts"], mime_prefix)
            if found:
                return found
    return None


def get_message_body(service, msg_id: str, prefer_html: bool = False) -> str:
    """
    Download and decode the message body as text.
    - If prefer_html=True and 'text/html' exists, return HTML (as text).
    - Otherwise return the plain text part; if not found, fall back to the top-level body.
    """
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = msg.get("payload", {})
    parts = payload.get("parts")

    wanted = "text/html" if prefer_html else "text/plain"
    part = _find_part(parts, wanted)
    if not part:
        # Try the other variant if preferred one is missing
        alt = "text/plain" if prefer_html else "text/html"
        part = _find_part(parts, alt)

    body = (part or payload).get("body", {})
    data = body.get("data")
    if not data:
        return ""

    # Gmail uses URL-safe base64; decode and return as UTF-8 text
    decoded_bytes = base64.urlsafe_b64decode(data.encode("utf-8"))
    return decoded_bytes.decode(errors="replace")


def parse_email_date(date_str: str) -> Optional[datetime]:
    """Parse Gmail date string to datetime object"""
    if not date_str:
        return None
    try:
        # Gmail dates are typically in RFC 2822 format
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


def prepare_email_data(service, message_ids: List[str]) -> List[Dict]:
    """Prepare email data for database storage"""
    email_data = []
    total = len(message_ids)

    for i, msg_id in enumerate(message_ids, 1):
        try:
            print(f"Processing email {i}/{total}...")
            # Get metadata and body
            meta = get_message_metadata(service, msg_id)
            body_text = get_message_body(service, msg_id, prefer_html=False)
            body_html = get_message_body(service, msg_id, prefer_html=True)

            # Parse date
            date_sent = parse_email_date(meta.get('Date', ''))

            email_data.append({
                'message_id': msg_id,
                'subject': meta.get('Subject'),
                'sender': meta.get('From'),
                'recipient': meta.get('To'),
                'date_sent': date_sent,
                'snippet': meta.get('Snippet'),
                'body_text': body_text if body_text != body_html else body_text,
                'body_html': body_html if body_html != body_text else None
            })
        except Exception as e:
            print(f"Error processing message {msg_id}: {e}")
            continue

    return email_data
