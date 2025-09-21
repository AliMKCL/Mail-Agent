"""
Reads your Gmail using a WEB OAuth client.

This script:
  - Uses a fixed redirect URI (http://localhost:8080/) so it matches your Web client settings.
  - Runs a local web server to catch Google's OAuth redirect.
  - Stores tokens in the database (so you only sign in once).
  - Lists recent message IDs, fetches headers, and reads the plain-text body.

IMPORTANT:
  In Google Cloud Console → Credentials → (your Web client),
  add Authorized redirect URI EXACTLY: http://localhost:8080/
"""

from __future__ import annotations

# Standard library imports
import os  # interacting with the operating system (paths, checking files, environment variables)
import base64  # encoding/decoding (Gmail message bodies are base64-url encoded)
from datetime import datetime  # for parsing email dates
from typing import Dict, List, Optional  # type hints used throughout the module

# Google authentication and API client imports
from google.oauth2.credentials import Credentials  # represents stored OAuth2 credentials (access + refresh tokens)
from google_auth_oauthlib.flow import InstalledAppFlow  # handles the OAuth2 flow, opens browser and runs local server for redirect
from google.auth.transport.requests import Request  # helper for making HTTP requests to refresh tokens
from googleapiclient.discovery import build  # constructs API client objects (Gmail API client)

# Local database imports
from database import DatabaseManager, User  # database models and utilities

# Ask only for read permission (least privilege).
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"] # Ensure gmail.readonly scope is selected in the OAuth client

# The fixed host/port MUST match the authorized redirect URI in your Web client.
OAUTH_HOST = "localhost"
OAUTH_PORT = 8080  # ensure http://localhost:8080/ is registered in the OAuth client

# Database setup
db_manager = DatabaseManager()

# Hardcoded user for demo (replace with actual user management)
DEMO_USER_EMAIL = "demo@example.com"

def get_service(user_id: int):
    """
    Create an authorized Gmail API client using database-stored credentials.
    - Reuses stored credentials from database if present (auto-refreshes access token).
    - If no token or invalid, runs OAuth flow and saves to database.
    """
    creds: Optional[Credentials] = None

    # 1) Load previously saved user credentials from database.
    creds = db_manager.get_user_credentials(user_id)

    # 2) If no valid creds, do the OAuth dance.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refresh silently using the refresh token.
            creds.refresh(Request())
            # Save refreshed credentials back to database
            db_manager.save_user_token(user_id, creds)
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

            # 3) Save credentials to database instead of file.
            db_manager.save_user_token(user_id, creds)

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
    fetched = 0

    # Page through results until no nextPageToken remains or max_results reached.
    while fetched < max_results:
        remaining = max_results - fetched
        page_size = min(remaining, 100)  # Gmail API max per request
        
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

def main():
    # 1) Get or create demo user
    user = db_manager.get_or_create_user(DEMO_USER_EMAIL, "Demo User")
    print(f"Working with user: {user.email} (ID: {user.id})")

    # 2) Create the Gmail API client (handles OAuth if needed).
    service = get_service(user.id)

    # 3) Fetch email IDs from Gmail
    print("Fetching emails from Gmail...")
    ids = list_message_ids(service, label_ids=["INBOX"], max_results=50)  # Fetch top 50 emails
    
    # Alternative search query (uncomment to try):
    # ids = list_message_ids(service, query="label:unread newer_than:7d subject:(invoice OR receipt)", max_results=50)

    print(f"Found {len(ids)} messages from Gmail")

    # 4) Prepare and save email data to database
    if ids:
        print("Processing and saving emails to database...")
        email_data = prepare_email_data(service, ids)
        saved_emails = db_manager.save_emails(user.id, email_data)
        print(f"Saved {len(saved_emails)} new emails to database")

    # 5) Read and display emails from database
    print(f"\n{'='*60}")
    print("EMAILS FROM DATABASE:")
    print(f"{'='*60}")
    
    stored_emails = db_manager.get_user_emails(user.id, limit=50)
    
    for i, email in enumerate(stored_emails, start=1):
        print(f"\n[{i}] ID: {email.message_id}")
        print(f"Date:   {email.date_sent.strftime('%Y-%m-%d %H:%M:%S') if email.date_sent else 'Unknown'}")
        print(f"From:   {email.sender or 'Unknown'}")
        print(f"To:     {email.recipient or 'Unknown'}")
        print(f"Subj:   {email.subject or 'No Subject'}")
        
        snippet = email.snippet or ""
        print(f"Snippet:{snippet[:120]}{'...' if len(snippet) > 120 else ''}")
        
        print("Body preview:")
        body = email.body_text or email.body_html or ""
        print(body[:500] + ("..." if len(body) > 500 else ""))
    
    print(f"\nTotal emails in database for user {user.email}: {len(stored_emails)}")

if __name__ == "__main__":
    main()