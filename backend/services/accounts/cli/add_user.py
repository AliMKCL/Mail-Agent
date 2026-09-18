#!/usr/bin/env python3
"""
Script to add a new Gmail user account to the database.
This script will:
1. Prompt for email and name
2. Look the mailbox up in the database
3. Trigger OAuth flow to authenticate with Gmail
4. Store credentials in database

Run it as a module from the repo root, with the Accounts service (``:8010``)
running::

    uv run python -m backend.services.accounts.cli.add_user

--------------------------------------------------------------------------
THIS IS A REVIVAL, NOT A FAITHFUL COPY — read before changing anything
--------------------------------------------------------------------------

Ported from ``backend/utilities/add_user.py`` (Spec step 4.7). Unlike its two
sibling CLIs, this one is **not** a line-for-line relocation, and the difference
is deliberate and signed off (tracked as **S4**).

The legacy script's step 2 was::

    user = db_manager.get_or_create_user(email, name)

The database manager class has **no** ``get_or_create_user`` method. It never
did after the ``users`` → ``accounts`` + ``email_accounts`` schema migration:
the closest surviving method is
``get_or_create_account(primary_email, password_hash)``, and there is no
``name`` column anywhere in the schema. So every single run of the legacy script
raised ``AttributeError`` on that line, had it swallowed by the script's own
``except Exception``, printed ``Error setting up user: '...' object has no
attribute 'get_or_create_user'`` and exited 1. It never reached the OAuth step.
The CLI has been dead since that migration, while the README continued to
advertise it.

Porting the dead call verbatim was rejected because there is nothing to port it
*to* — no Database-service route corresponds to it, and fabricating one (say,
``POST /accounts/get-or-create`` with an invented ``password_hash``) would create
real rows and be a worse change than this one. So:

* the mailbox is **looked up**, never created — no row writes, no fabricated
  password hash, no new Database route;
* the lookup goes through the Accounts service's ``GET /internal/email-accounts``
  and matches on ``email``;
* a mailbox that is not registered yet prints the same
  ``Error setting up user: ...`` line and exits 1, i.e. today's observable
  outcome for a fresh address;
* a mailbox that *is* registered now proceeds to OAuth and the
  ``users().getProfile()`` verification, which is the **behavior change**: the
  script used to exit 1 unconditionally.

Per Spec 4.7 the re-auth runs through the Accounts service's internal endpoint
(so the browser opens in the service process, and S3's threadpool wrapping
applies), and the Gmail client is built here **purely** to call
``users().getProfile()`` as a verification step. Nothing else in this process
touches the Gmail API.
"""

import asyncio
import sys

from googleapiclient.discovery import build

from backend.libs.common.config import settings
from backend.libs.common.errors import UpstreamError
from backend.libs.common.http import AsyncServiceClient
from backend.libs.contracts.accounts import (
    CredentialsDTO,
    EmailAccountDTO,
    dto_to_credentials,
)


async def _find_email_account(
    accounts: AsyncServiceClient, email: str
) -> EmailAccountDTO:
    """Resolve a registered mailbox by address, or raise.

    The raised message is what the caller's ``except Exception`` prints, so it
    lands on the same ``Error setting up user: ...`` line the legacy script
    produced for an unregistered address.
    """
    response = await accounts.get("/internal/email-accounts")
    for row in response.json():
        email_account = EmailAccountDTO(**row)
        if email_account.email == email:
            return email_account
    raise Exception(
        f"No email account found for {email}. Sign up through the web app first."
    )


async def _reauth(accounts: AsyncServiceClient, email_account_id: int) -> CredentialsDTO:
    """Force interactive re-auth through the Accounts internal endpoint.

    A credential failure comes back as 409 ``{"error": "<original string>"}``.
    Re-raising it as a plain ``Exception`` carrying that string reproduces what
    ``gmail_read.get_service`` used to raise here, so the printed error line is
    unchanged.
    """
    try:
        response = await accounts.post(
            f"/internal/email-accounts/{email_account_id}/reauth",
            # Interactive consent waits on a human.
            timeout=None,
        )
    except UpstreamError as err:
        body = err.body
        if isinstance(body, dict) and "error" in body:
            raise Exception(body["error"])
        raise
    return CredentialsDTO(**response.json())


async def main():
    accounts = AsyncServiceClient(settings.ACCOUNTS_URL)

    print("Gmail Account Setup")
    print("=" * 50)

    # Get user details
    email = input("Enter Gmail address: ").strip()
    if not email:
        print("Email is required!")
        sys.exit(1)

    name = input("Enter display name (optional): ").strip()
    if not name:
        name = None

    try:
        # Find the mailbox (see the module docstring: lookup, not create)
        user = await _find_email_account(accounts, email)
        print(f"\nUser created/found: {user.email} (ID: {user.id})")

        # Trigger OAuth flow
        print("\nStarting OAuth flow...")
        print("Your browser will open for Gmail authentication.")
        print("Please sign in and authorize the application.")

        credentials = await _reauth(accounts, user.id)

        # Build a Gmail client here for verification only (Spec 4.7)
        service = build("gmail", "v1", credentials=dto_to_credentials(credentials))

        # Test the connection
        profile = service.users().getProfile(userId='me').execute()
        print(f"\nSuccess! Connected to Gmail account: {profile.get('emailAddress')}")
        print(f"Total messages in account: {profile.get('messagesTotal', 'Unknown')}")

        print(f"\nUser {email} has been successfully added to the system!")
        print("You can now use the web interface to view emails for this account.")

    except Exception as e:
        print(f"\nError setting up user: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
