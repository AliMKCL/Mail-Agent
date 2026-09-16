"""
Google OAuth endpoints: initiate the consent flow and handle the callback.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from google_auth_oauthlib.flow import Flow

from backend import dependencies
from backend.services.setup_calendar import authenticate_google_calendar

router = APIRouter(tags=["oauth"])

# Scopes needed for Gmail and Calendar access
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]


# Initiate OAuth flow for an email account
@router.get("/api/auth/google")
async def initiate_google_oauth(email_account_id: int):
    """Initiate OAuth flow for Google Calendar and Gmail"""
    try:
        # Use the setup_calendar function to initiate OAuth
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
async def oauth_callback(code: str, state: str | None = None):
    """Handle OAuth callback for Google Calendar and Gmail"""
    try:
        flow = Flow.from_client_secrets_file("credentials.json", SCOPES)
        flow.redirect_uri = "http://localhost:8000/oauth/callback"

        # Exchange authorization code for credentials
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Save credentials to database
        # State contains the email_account_id
        if state and state.isdigit():
            email_account_id = int(state)
            dependencies.db_manager.save_email_token(email_account_id, creds)
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
