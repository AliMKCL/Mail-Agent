"""
Google Calendar client construction for one email account.

Relocated from ``backend/services/setup_calendar.py::get_calendar_service``
(Spec Phase 5.2) — that function **only**. The OAuth flow, the token store and
the scope ladder now live in the Accounts service (R2); the local database
manager this function used to build is gone (R1, hazard 3).

``get_calendar_service`` keeps its exact synchronous signature and its
``(service, error)`` return contract: callers match on the *error strings*
("Authentication required", "Authentication required - Calendar scope missing",
...) and ``moodle.py`` calls it from synchronous functions. Unlike
``gmail.get_service``, it never raises on an authentication failure — it returns
``(None, error)``. That asymmetry is deliberate (Spec 3.3.1) and is reproduced
here exactly.

After the move there is exactly **one** binding of this function, in this
module (Wave 0 hazard 1: the monolith had two).
"""

from __future__ import annotations

from googleapiclient.discovery import build

from backend.libs.common.errors import UpstreamError
from backend.libs.contracts.accounts import (
    CredentialsDTO,
    credentials_to_dto,
    dto_to_credentials,
)
from backend.services.user_data.clients import get_accounts_sync_client


def get_calendar_service(email_account_id=None):
    """Get Google Calendar service for the email account"""
    try:
        if email_account_id is None:
            return None, "Email account ID is required"

        accounts = get_accounts_sync_client()

        # The calendar rung of the credential ladder (Spec 3.3.1): Accounts
        # refreshes and re-authenticates on refresh failure, but never opens a
        # browser for *missing* credentials, and it enforces the calendar scope.
        try:
            response = accounts.get(
                f"/internal/email-accounts/{email_account_id}/credentials",
                params={"require_scope": "calendar", "allow_interactive": "false"},
            )
        except UpstreamError as error:
            # 409 body is {"error": "<one of the strings callers match on>"}.
            body = error.body
            if isinstance(body, dict) and "error" in body:
                return None, body["error"]
            return None, str(body)

        creds = dto_to_credentials(CredentialsDTO(**response.json()))

        # Build calendar service
        service = build('calendar', 'v3', credentials=creds)

        # Save credentials after building service in case they were auto-refreshed
        # Google's library may refresh tokens when building the service
        try:
            accounts.put(
                f"/internal/email-accounts/{email_account_id}/credentials",
                json=credentials_to_dto(creds).model_dump(mode="json"),
            )
        except Exception as save_error:
            print(f"⚠️  Warning: Could not save potentially refreshed credentials: {save_error}")

        return service, None

    except Exception as e:
        print(f"Error getting calendar service: {e}")
        return None, str(e)
