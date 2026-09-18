#!/usr/bin/env python3
"""
Command-line re-authentication for email accounts with expired or invalid
OAuth tokens.

Ported from ``backend/utilities/reauth_user.py`` (Spec step 4.7). The flow
implementations themselves now live in
:mod:`backend.services.accounts.google_oauth`; this module is only the CLI
wrapper around them.

Run it as a module from the repo root::

    uv run python -m backend.services.accounts.cli.reauth_user                    # all
    uv run python -m backend.services.accounts.cli.reauth_user <email_account_id> # one

It drives the flows **in this process**, not over HTTP, so the browser opens on
the operator's own machine exactly as the original script did. Only the data
access moved: the module-level database-manager object is gone (**R1**) and the
``sys.path``-mangling preamble is gone (**R9**). Console output is unchanged.
"""

import asyncio
import sys

from backend.services.accounts.clients import get_database_client
from backend.services.accounts.google_oauth import (
    force_reauth_for_email_account,
    reauth_all_email_accounts,
)


async def main():
    """
    Command-line interface for re-authenticating email accounts.

    Usage:
        python -m backend.services.accounts.cli.reauth_user                      # Re-auth all email accounts
        python -m backend.services.accounts.cli.reauth_user <email_account_id>   # Re-auth specific email account
    """
    db = get_database_client()

    if len(sys.argv) > 1:
        # Re-authenticate specific email account
        try:
            email_account_id = int(sys.argv[1])
            success = await force_reauth_for_email_account(email_account_id, db)
            sys.exit(0 if success else 1)
        except ValueError:
            print(f"❌ Error: Invalid email account ID '{sys.argv[1]}'. Must be an integer.")
            sys.exit(1)
    else:
        # Re-authenticate all email accounts
        results = await reauth_all_email_accounts(db)

        # Exit with error code if any re-auth failed
        if not all(results.values()):
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
