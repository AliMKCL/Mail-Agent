"""
Accounts service — public /oauth/callback and /api/auth/google OAuth routes.

Spec sections 3.1 and 3.3, step 4.4. Ported from ``backend/controllers/oauth.py``.

Two things to leave alone:

* The redirect URI is the literal ``http://localhost:8000/oauth/callback``
  (``settings.OAUTH_REDIRECT_URI``'s default) because that is what is registered
  in Google Cloud Console. **Do not touch Google Cloud configuration.**
* Both HTML bodies below are byte-identical to the originals — emoji, inline
  ``<script>`` and inline styles included. ``tests/golden/get__oauth_callback.json``
  asserts the success body exactly, and the Gateway must pass non-JSON bodies
  through unchanged.

``SCOPES`` is imported from ``google_oauth`` rather than re-declared; the legacy
controller kept its own copy of the same two scope URLs.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from google_auth_oauthlib.flow import Flow

from backend.libs.common.http import AsyncServiceClient
from backend.services.accounts.clients import get_database_client
from backend.services.accounts.config import (
    GOOGLE_CREDENTIALS_FILE,
    OAUTH_REDIRECT_URI,
)
from backend.services.accounts.google_oauth import (
    SCOPES,
    authenticate_google_calendar,
    save_credentials,
)

router = APIRouter(tags=["oauth"])


# Initiate OAuth flow for an email account
@router.get("/api/auth/google")
async def initiate_google_oauth(email_account_id: int):
    """Initiate OAuth flow for Google Calendar and Gmail"""
    try:
        # Use the google_oauth function to initiate OAuth
        auth_url, state = authenticate_google_calendar(email_account_id)

        if not auth_url:
            raise HTTPException(
                status_code=500, detail=f"Failed to generate auth URL: {state}"
            )

        return {"status": "success", "auth_url": auth_url, "state": state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error initiating OAuth: {e!s}")


# This endpoint is called during the OAuth callback after user authentication.
@router.get("/oauth/callback")
async def oauth_callback(
    code: str,
    state: str | None = None,
    db: AsyncServiceClient = Depends(get_database_client),
):
    """Handle OAuth callback for Google Calendar and Gmail"""
    try:
        flow = Flow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
        flow.redirect_uri = OAUTH_REDIRECT_URI

        # Exchange authorization code for credentials
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Save credentials to database
        # State contains the email_account_id
        if state and state.isdigit():
            email_account_id = int(state)
            await save_credentials(db, email_account_id, creds)
            print(f"✅ OAuth credentials saved for email account {email_account_id}")
        else:
            print(f"⚠️  Warning: No valid email_account_id in state: {state}")

        # Redirect back to main.html
        return HTMLResponse("""
        <html>
            <head>
                <title>Authorization Complete</title>
                <script>
                    // Redirect to main.html after 2 seconds
                    setTimeout(function() {
                        window.location.href = '/templates/main.html';
                    }, 2000);
                </script>
            </head>
            <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                <h2 style="color: #4CAF50;">✅ Authorization successful!</h2>
                <p>Your Gmail account has been connected.</p>
                <p>Redirecting you back to the app...</p>
            </body>
        </html>
        """)

    except Exception as e:
        return HTMLResponse(
            f"""
        <html>
            <head><title>Authorization Error</title></head>
            <body>
                <h2>Authorization failed</h2>
                <p>Error: {e!s}</p>
                <p>Please try again.</p>
            </body>
        </html>
        """,
            status_code=500,
        )
