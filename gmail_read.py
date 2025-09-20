"""
Reads your Gmail using a WEB OAuth client.

This script:
  - Uses a fixed redirect URI (http://localhost:8080/) so it matches your Web client settings.
  - Runs a local web server to catch Google's OAuth redirect.
  - Stores tokens in token.json (so you only sign in once).
  - Lists recent message IDs, fetches headers, and reads the plain-text body.

IMPORTANT:
  In Google Cloud Console → Credentials → (your Web client),
  add Authorized redirect URI EXACTLY: http://localhost:8080/
"""

from __future__ import annotations

# Standard library imports
import os  # interacting with the operating system (paths, checking files, environment variables)
import base64  # encoding/decoding (Gmail message bodies are base64-url encoded)
from typing import Dict, List, Optional  # type hints used throughout the module

# Google authentication and API client imports
from google.oauth2.credentials import Credentials  # represents stored OAuth2 credentials (access + refresh tokens)
from google_auth_oauthlib.flow import InstalledAppFlow  # handles the OAuth2 flow, opens browser and runs local server for redirect
from google.auth.transport.requests import Request  # helper for making HTTP requests to refresh tokens
from googleapiclient.discovery import build  # constructs API client objects (Gmail API client)

# Ask only for read permission (least privilege).
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"] # Ensure gmail.readonly scope is selected in the OAuth client

# The fixed host/port MUST match the authorized redirect URI in your Web client.
OAUTH_HOST = "localhost"
OAUTH_PORT = 8080  # ensure http://localhost:8080/ is registered in the OAuth client

def get_service():
    """
    Create an authorized Gmail API client.
    - Reuses token.json if present (auto-refreshes access token with the refresh token).
    - If no token or invalid, runs a local server on http://localhost:8080/ to complete OAuth.
    """
    creds: Optional[Credentials] = None

    # 1) Load previously saved user credentials (if any).
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # 2) If no valid creds, do the OAuth dance.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refresh silently using the refresh token.
            creds.refresh(Request())
        else:
            # Start the Installed App flow, but bind to a fixed localhost port.
            # This is compatible with WEB clients as long as the redirect URI matches.
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)

            # run_local_server starts a tiny HTTP server on localhost and opens the browser.
            # It uses http://<host>:<port>/ as the redirect URI internally.
            creds = flow.run_local_server(
                host=OAUTH_HOST,
                port=OAUTH_PORT,
                authorization_prompt_message="Your browser will open for Google sign-in…",
                success_message="You may close this tab and return to the app.",
                open_browser=True,
            )

        # 3) Save credentials for next time.
        with open("token.json", "w") as f:
            f.write(creds.to_json())

    # 4) Build the Gmail client with authorized credentials.
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
    page_token = None

    # Page through results until no nextPageToken remains.
    while True:
        resp = service.users().messages().list(
            userId="me",
            q=query,
            labelIds=label_ids,
            maxResults=max_results,
            pageToken=page_token,
        ).execute()

        ids.extend([m["id"] for m in resp.get("messages", [])])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return ids

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

def main():
    # 1) Create the Gmail API client (handles OAuth if needed).
    service = get_service()

    # 2) Choose what you want to fetch:
    #    A) last 50 INBOX messages
    ids = list_message_ids(service, label_ids=["INBOX"], max_results=10)

    #    B) OR use a Gmail search query (uncomment to try):
    # ids = list_message_ids(service, query="label:unread newer_than:7d subject:(invoice OR receipt)", max_results=50)

    print(f"Found {len(ids)} messages")
    for i, mid in enumerate(ids[:10], start=1):  # limit output for demo
        meta = get_message_metadata(service, mid)
        body = get_message_body(service, mid, prefer_html=False)

        print(f"\n[{i}] ID: {meta['id']}")
        print(f"Date:   {meta.get('Date', '')}")
        print(f"From:   {meta.get('From', '')}")
        print(f"To:     {meta.get('To', '')}")
        print(f"Subj:   {meta.get('Subject', '')}")
        print(f"Snippet:{meta.get('Snippet', '')[:120]}{'...' if len(meta.get('Snippet',''))>120 else ''}")
        print("Body preview:")
        print(body[:500] + ("..." if len(body) > 500 else ""))

if __name__ == "__main__":
    main()