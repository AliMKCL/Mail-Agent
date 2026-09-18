#!/usr/bin/env python3
"""
Script to list all Gmail email accounts in the database.

Ported from ``backend/utilities/list_users.py`` (Spec step 4.7). Run it as a
module from the repo root::

    uv run python -m backend.services.accounts.cli.list_users

Only the data access changed: the script's own database-manager object is gone
(**R1**) and every read now goes through the Database service, with credentials
read via the Accounts service's own storage helper (**R2**). Every printed line
is byte-identical to the original, including the literal ``\\n`` in the
"no email accounts" message — that backslash was escaped twice in the source, so
the line really does print ``\\nTo add an email account...``, and it stays that
way.
"""

import asyncio

from backend.libs.contracts.emails import EmailDTO
from backend.services.accounts.clients import get_database_client
from backend.services.accounts.google_oauth import (
    all_email_accounts,
    load_stored_credentials,
)


async def main():
    db_manager = get_database_client()

    print("Gmail Email Accounts in Database")
    print("=" * 50)

    email_accounts = await all_email_accounts(db_manager)

    if not email_accounts:
        print("No email accounts found in database.")
        print("\\nTo add an email account, sign up through the web app.")
        return

    print(f"Found {len(email_accounts)} email account(s):")
    print()

    for email_account in email_accounts:
        print(f"ID: {email_account.id}")
        print(f"Account ID: {email_account.account_id}")
        print(f"Email: {email_account.email}")
        print(f"Provider: {email_account.provider}")
        print(f"Is Primary: {'Yes' if email_account.is_primary else 'No'}")
        print(f"Created: {email_account.created_at}")

        # Check if email account has stored credentials
        creds = await load_stored_credentials(db_manager, email_account.id)
        if creds:
            print(f"OAuth Status: ✓ Authenticated")
        else:
            print(f"OAuth Status: ✗ Not authenticated")

        # Check email count
        emails = await _email_account_emails(db_manager, email_account.id, limit=1)
        email_count = len(
            await _email_account_emails(db_manager, email_account.id, limit=10000)
        )  # Get all to count
        print(f"Stored Emails: {email_count}")

        print("-" * 30)


async def _email_account_emails(db_manager, email_account_id: int, limit: int):
    """Replacement for ``db_manager.get_email_account_emails(id, limit=...)``.

    The ``limit=1`` call above is dead — its result is never read — but it was
    dead in the original too, and this is a relocation, so it stays. The count
    still comes from a ``limit=10000`` read rather than a ``COUNT(*)``, so an
    account with more than 10000 cached emails under-reports exactly as it does
    today.
    """
    response = await db_manager.get(
        "/emails", params={"email_account_id": email_account_id, "limit": limit}
    )
    return [EmailDTO(**row) for row in response.json()]


if __name__ == "__main__":
    asyncio.run(main())
