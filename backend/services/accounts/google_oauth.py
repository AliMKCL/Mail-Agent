"""
Google OAuth flows and credential storage for the Accounts service.

Rule **R2**: this service is the sole credential authority. Only code under
``backend/services/accounts/`` may read or write ``email_tokens``, run an OAuth
flow, or read ``credentials.json``. Everything here is reached over HTTP by the
other services; nothing else imports ``google_auth_oauthlib``.

Absorbed, per Spec step 4.1, from two legacy modules:

* ``backend/services/setup_calendar.py`` — ``SCOPES``,
  ``authenticate_google_calendar``, ``authenticate_calendar``.
* ``backend/utilities/reauth_user.py`` — ``OAUTH_HOST`` / ``OAUTH_PORT``,
  ``reauthenticate_user_token_failure``, ``force_reauth_for_email_account``,
  ``reauth_all_email_accounts``.

Three things changed in the move, and nothing else:

1. Both legacy modules constructed their **own** database-manager object
   against the hardcoded default ``sqlite:///gmail_agent.db``, which silently
   bypassed any configured database (hazard 3 in ``CONTRACT_FREEZE.md``). Every
   one of those accesses now goes through the Database service via the injected
   :class:`AsyncServiceClient` (**R1**).
2. Their ``sys.path``-mangling preamble is gone (**R9**); these run as modules
   from the repo root.
3. **S3**: ``InstalledAppFlow.run_local_server`` — which opens a browser and
   blocks until a human finishes consenting — now runs through
   ``run_in_threadpool``. Identical behavior; it just no longer freezes the
   Accounts event loop, so a hung re-auth cannot also take down sign-in (B7).

Every console string **in this module** is byte-identical to its original,
checked line by line against ``backend/utilities/reauth_user.py`` and
``backend/services/setup_calendar.py``. ``add_user`` and ``reauth_user`` are
operator CLIs whose stdout *is* their interface. The credential ladder's
console output is a separate matter and lives in
:mod:`backend.services.accounts.credentials`, which must branch on
``allow_interactive`` because ``gmail_read`` and ``setup_calendar`` word the
same events differently.
"""

from __future__ import annotations

import os

from fastapi.concurrency import run_in_threadpool
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow

from backend.libs.common.errors import UpstreamError
from backend.libs.common.http import AsyncServiceClient
from backend.libs.contracts.accounts import (
    CredentialsDTO,
    EmailAccountDTO,
    credentials_to_dto,
    dto_to_credentials,
)
from backend.services.accounts.config import (
    GOOGLE_CREDENTIALS_FILE,
    OAUTH_HOST,
    OAUTH_PORT,
    OAUTH_REDIRECT_URI,
)

# Scopes needed for Gmail and Calendar access
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]

#: The Calendar scope, spelled out once. ``setup_calendar`` compared against
#: this literal in three places.
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"

__all__ = [
    "SCOPES",
    "CALENDAR_SCOPE",
    "OAUTH_HOST",
    "OAUTH_PORT",
    "load_stored_credentials",
    "save_credentials",
    "all_email_accounts",
    "reauthenticate_user_token_failure",
    "force_reauth_for_email_account",
    "reauth_all_email_accounts",
    "authenticate_google_calendar",
    "authenticate_calendar",
]


# --------------------------------------------------------------------------
# Token storage — the Database service stands in for the old db_manager calls
# --------------------------------------------------------------------------


async def load_stored_credentials(
    db: AsyncServiceClient, email_account_id: int
) -> Credentials | None:
    """Replacement for ``db_manager.get_email_account_credentials(id)``.

    The Database service answers **404** when the email account has no token
    row. That is not an error in the credential ladder — it is the distinct
    "no credentials stored" state — so it maps back to ``None``, exactly what
    the manager method returned. Branch on the status code, never on the
    ``detail`` string (addendum A1).
    """
    try:
        response = await db.get(f"/email-accounts/{email_account_id}/token")
    except UpstreamError as err:
        if err.status == 404:
            return None
        raise
    return dto_to_credentials(CredentialsDTO(**response.json()))


async def save_credentials(
    db: AsyncServiceClient, email_account_id: int, creds: Credentials
) -> None:
    """Replacement for ``db_manager.save_email_token(id, creds)``."""
    await db.put(
        f"/email-accounts/{email_account_id}/token",
        json=credentials_to_dto(creds).model_dump(mode="json"),
    )


async def all_email_accounts(db: AsyncServiceClient) -> list[EmailAccountDTO]:
    """Replacement for ``db_manager.get_all_email_accounts()``.

    No ``account_id`` filter, so this is every email account across all
    tenants — issue X2, which ``reauth_all_email_accounts`` has always relied
    on and which is preserved here.
    """
    response = await db.get("/email-accounts")
    return [EmailAccountDTO(**row) for row in response.json()]


# --------------------------------------------------------------------------
# Interactive re-authentication (ex backend/utilities/reauth_user.py)
# --------------------------------------------------------------------------


async def reauthenticate_user_token_failure(
    email_account_id: int, db: AsyncServiceClient
) -> Credentials | None:
    """
    Re-authenticate an email account whose tokens have expired or been revoked.

    This function is triggered when token refresh fails or tokens are invalid.
    It will:
    1. Display a clear message about why re-authentication is needed
    2. Open the browser for OAuth consent flow
    3. Save the new credentials to the database
    4. Return the new credentials

    Args:
        email_account_id: The database ID of the email account to re-authenticate
        db: Database-service client used to persist the new credentials

    Returns:
        New Credentials object if successful, None if failed
    """
    print("\n" + "="*80)
    print("🔐 RE-AUTHENTICATION REQUIRED")
    print("="*80)
    print(f"Email Account ID: {email_account_id}")
    print("\nYour Google OAuth tokens have expired or been revoked.")
    print("This can happen when:")
    print("  • Tokens have been inactive for 6+ months")
    print("  • You revoked access in your Google Account settings")
    print("  • API scopes have changed")
    print("\nYou need to sign in again to grant access to Gmail and Calendar.")
    print("="*80)

    try:
        print(f"\nRe-authenticating email account ID: {email_account_id}")
        print("\n🌐 Opening browser for Google sign-in...")
        print("   Please complete the authorization in your browser.")
        print("   Make sure to:")
        print("   1. Sign in with the correct Google account")
        print("   2. Review and accept all requested permissions")
        print("   3. Wait for the success message before closing the browser")
        print("\n" + "-"*80)

        # Create OAuth flow
        flow = InstalledAppFlow.from_client_secrets_file(
            GOOGLE_CREDENTIALS_FILE,
            SCOPES
        )

        # Run the OAuth flow with browser.
        # S3: run_local_server blocks until a human finishes consenting, so it
        # goes to a worker thread. Same call, same arguments, same result.
        creds = await run_in_threadpool(
            flow.run_local_server,
            host=OAUTH_HOST,
            port=OAUTH_PORT,
            authorization_prompt_message="🔓 Opening browser for Google sign-in...",
            success_message="✅ Authorization successful! You may close this tab and return to the terminal.",
            open_browser=True,
            access_type='offline',  # Request refresh token
            prompt='consent'        # Force consent screen to ensure refresh token is issued
        )

        if not creds:
            print("\n❌ Failed to obtain credentials from OAuth flow.")
            return None

        # Verify we got a refresh token
        if not creds.refresh_token:
            print("\n⚠️  Warning: No refresh token received.")
            print("   This may cause issues in the future.")
            print("   Consider revoking app access in Google Account settings and trying again.")

        # Save the new credentials to the database
        await save_credentials(db, email_account_id, creds)

        print("\n" + "="*80)
        print("✅ RE-AUTHENTICATION SUCCESSFUL")
        print("="*80)
        print("New credentials have been saved to the database.")
        print("You can now access Gmail and Calendar APIs.")
        print("="*80 + "\n")

        return creds

    except KeyboardInterrupt:
        print("\n\n❌ Re-authentication cancelled by user.")
        print("   The application cannot continue without valid credentials.")
        return None

    except Exception as e:
        print(f"\n❌ Re-authentication failed: {e}")
        print("\nTroubleshooting:")
        print("  1. Check that credentials.json exists and is valid")
        print("  2. Verify http://localhost:8080/ is in authorized redirect URIs")
        print("  3. Ensure no other service is using port 8080")
        print("  4. Check your internet connection")
        print(f"\nError details: {type(e).__name__}: {e}")
        return None


async def force_reauth_for_email_account(
    email_account_id: int, db: AsyncServiceClient
) -> bool:
    """
    Manually trigger re-authentication for a specific email account.

    This can be called directly to force an email account to re-authenticate,
    even if their tokens appear valid.

    Args:
        email_account_id: The database ID of the email account to re-authenticate
        db: Database-service client used to persist the new credentials

    Returns:
        True if successful, False otherwise
    """
    print(f"\n🔄 Forcing re-authentication for email account ID: {email_account_id}")

    creds = await reauthenticate_user_token_failure(email_account_id, db)
    return creds is not None


async def reauth_all_email_accounts(db: AsyncServiceClient) -> dict:
    """
    Re-authenticate all email accounts in the database.

    Useful for bulk re-authentication after scope changes or
    when multiple email accounts have expired tokens.

    Returns:
        Dictionary with email_account_id as keys and success status as values
    """
    email_accounts = await all_email_accounts(db)

    if not email_accounts:
        print("❌ No email accounts found in database.")
        return {}

    print(f"\n{'='*80}")
    print(f"🔄 RE-AUTHENTICATING {len(email_accounts)} EMAIL ACCOUNT(S)")
    print(f"{'='*80}\n")

    results = {}

    for i, email_account in enumerate(email_accounts, 1):
        print(f"\n[{i}/{len(email_accounts)}] Processing email account: {email_account.email} (ID: {email_account.id})")

        try:
            creds = await reauthenticate_user_token_failure(email_account.id, db)
            results[email_account.id] = creds is not None

            if creds:
                print(f"✅ Successfully re-authenticated {email_account.email}")
            else:
                print(f"❌ Failed to re-authenticate {email_account.email}")

        except Exception as e:
            print(f"❌ Error re-authenticating {email_account.email}: {e}")
            results[email_account.id] = False

    # Summary
    successful = sum(1 for success in results.values() if success)
    failed = len(results) - successful

    print(f"\n{'='*80}")
    print("📊 RE-AUTHENTICATION SUMMARY")
    print(f"{'='*80}")
    print(f"Total email accounts:  {len(results)}")
    print(f"Successful:            {successful}")
    print(f"Failed:                {failed}")
    print(f"{'='*80}\n")

    return results


# --------------------------------------------------------------------------
# Web OAuth consent URL (ex backend/services/setup_calendar.py)
# --------------------------------------------------------------------------


def authenticate_google_calendar(email_account_id=None):
    """Initiate OAuth flow for Google Calendar

    Stays synchronous and database-free: it only builds a consent URL. Returns
    ``(auth_url, state)`` on success and ``(None, str(e))`` on failure — the
    caller turns a falsy ``auth_url`` into its own HTTP error, as it does today.

    ``OAUTH_REDIRECT_URI`` defaults to the literal
    ``http://localhost:8000/oauth/callback`` that is registered in Google Cloud
    Console. Do not change it here.
    """
    try:
        flow = Flow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
        flow.redirect_uri = OAUTH_REDIRECT_URI  # FastAPI backend port

        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',  # Force consent to get refresh_token
            state=str(email_account_id) if email_account_id else 'default'
        )

        return auth_url, state

    except Exception as e:
        print(f"Error creating auth URL: {e}")
        return None, str(e)


# --------------------------------------------------------------------------
# Operator-facing calendar bootstrap (ex backend/services/setup_calendar.py)
# --------------------------------------------------------------------------


async def authenticate_calendar(email_account_id=1, *, db: AsyncServiceClient):
    """Authenticate email account for Google Calendar access

    Note the pre-check that ``get_calendar_service`` does **not** have: valid
    credentials that are missing the Calendar scope are discarded outright here
    and re-acquired interactively, rather than reported as an error.
    """
    creds = None

    # Check if we already have saved credentials in database
    creds = await load_stored_credentials(db, email_account_id)

    # Check if credentials are valid and include the required scopes
    if creds and creds.valid:
        if CALENDAR_SCOPE not in creds.scopes:
            print("⚠️ Calendar scope missing. Re-authenticating...")
            creds = None  # Force re-authentication

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                print(f"🔄 Attempting to refresh expired token for email account {email_account_id}...")
                creds.refresh(Request())
                print("✅ Credentials refreshed successfully!")
                # Save refreshed credentials back to database
                await save_credentials(db, email_account_id, creds)
            except Exception as e:
                print(f"❌ Error refreshing credentials: {e}")
                print("   Triggering re-authentication...")
                creds = None

        if not creds:
            if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
                print("❌ credentials.json file not found!")
                print("Please download it from Google Cloud Console and place it in this directory.")
                return False

            # Use the centralized re-authentication function

            creds = await reauthenticate_user_token_failure(email_account_id, db)

            if not creds:
                print("❌ Re-authentication failed. Cannot proceed without valid credentials.")
                return False

            print(f"✅ Credentials saved to database for email account ID {email_account_id}")

    return True
