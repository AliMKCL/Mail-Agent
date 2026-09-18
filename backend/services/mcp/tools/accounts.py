"""
MCP tools backed by the Accounts service: list_accounts, list_email_accounts,
get_account_info, get_email_account_info.

Spec section 3.6. Filled by agent M1.

These are plain ``async def``s, called **in-process** by ``mcp_server.py`` and
``llm_integration.py`` — never over HTTP per tool call. The bodies are the
monolith's ``backend/mcp_server.py`` bodies with every ``db_manager`` /
``session.query(...)`` access replaced by one call to the owning service. Every
returned dict is key-for-key identical to today's, including the error
sentences, because those strings are what the LLM sees.

Downstream: Accounts ``/internal/*`` for identity, plus User_data
``/internal/emails/count`` for ``get_email_account_info``'s ``email_count``
(the Database service is not reachable from here — only Accounts and User_data
are).
"""

import logging
from typing import Optional

from backend.libs.common.errors import UpstreamError
from backend.services.mcp import clients

logger = logging.getLogger(__name__)


async def list_accounts() -> dict:
    """
    List all registered accounts in the system.
    Returns account information including id, primary_email for each account.
    """
    try:
        accounts_client = clients.get_accounts_client()
        accounts = (await accounts_client.get("/internal/accounts")).json()
        account_list = [
            {
                "id": account["id"],
                "primary_email": account["primary_email"],
                "created_at": account["created_at"]
            }
            for account in accounts
        ]
        logger.info(f"Listed {len(account_list)} accounts")
        return {
            "status": "success",
            "accounts": account_list,
            "count": len(account_list)
        }
    except Exception as e:
        logger.error(f"Error listing accounts: {e}")
        return {"status": "error", "error": str(e)}


async def list_email_accounts(account_id: Optional[int] = None) -> dict:
    """
    List all email accounts (Gmail/Outlook) in the system.

    Args:
        account_id: Optional - if provided, only returns email accounts for this account

    Returns account information including id, email, provider, account_id.
    """
    try:
        accounts_client = clients.get_accounts_client()
        if account_id is not None:
            params = {"account_id": account_id}
        else:
            params = None

        email_accounts = (
            await accounts_client.get("/internal/email-accounts", params=params)
        ).json()

        email_account_list = [
            {
                "id": ea["id"],
                "email": ea["email"],
                "provider": ea["provider"],
                "account_id": ea["account_id"],
                "is_primary": bool(ea["is_primary"])
            }
            for ea in email_accounts
        ]
        logger.info(f"Listed {len(email_account_list)} email accounts")
        return {
            "status": "success",
            "email_accounts": email_account_list,
            "count": len(email_account_list)
        }
    except Exception as e:
        logger.error(f"Error listing email accounts: {e}")
        return {"status": "error", "error": str(e)}


async def get_account_info(account_id: int) -> dict:
    """
    Get detailed information about a specific account by their ID.

    Args:
        account_id: The ID of the account to retrieve
    """
    try:
        accounts_client = clients.get_accounts_client()
        try:
            detail = (
                await accounts_client.get(f"/internal/accounts/{account_id}/detail")
            ).json()
        except UpstreamError as err:
            if err.status == 404:
                return {"status": "error", "error": f"Account with ID {account_id} not found"}
            raise

        account = detail["account"]
        # Email accounts for this account, resolved by the same route.
        email_accounts = detail["email_accounts"]

        return {
            "status": "success",
            "account": {
                "id": account["id"],
                "primary_email": account["primary_email"],
                "created_at": account["created_at"],
                "email_accounts_count": len(email_accounts),
                "email_accounts": [
                    {
                        "id": ea["id"],
                        "email": ea["email"],
                        "provider": ea["provider"],
                        "is_primary": bool(ea["is_primary"])
                    }
                    for ea in email_accounts
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error getting account info: {e}")
        return {"status": "error", "error": str(e)}


async def get_email_account_info(email_account_id: int) -> dict:
    """
    Get detailed information about a specific email account by ID.

    Args:
        email_account_id: The ID of the email account to retrieve
    """
    try:
        accounts_client = clients.get_accounts_client()
        try:
            detail = (
                await accounts_client.get(
                    f"/internal/email-accounts/{email_account_id}/detail"
                )
            ).json()
        except UpstreamError as err:
            if err.status == 404:
                return {
                    "status": "error",
                    "error": f"Email account with ID {email_account_id} not found",
                }
            raise

        email_account = detail["email_account"]
        # The parent account's primary email travels with the detail route.
        account_primary_email = detail["primary_email"]

        # Count emails for this email account (owned by User_data).
        user_data_client = clients.get_user_data_client()
        email_count = (
            await user_data_client.get(
                "/internal/emails/count",
                params={"email_account_id": email_account_id},
            )
        ).json()["count"]

        return {
            "status": "success",
            "email_account": {
                "id": email_account["id"],
                "email": email_account["email"],
                "provider": email_account["provider"],
                "account_id": email_account["account_id"],
                "is_primary": bool(email_account["is_primary"]),
                "account_primary_email": account_primary_email,
                "email_count": email_count,
                "created_at": email_account["created_at"]
            }
        }
    except Exception as e:
        logger.error(f"Error getting email account info: {e}")
        return {"status": "error", "error": str(e)}


__all__ = [
    "list_accounts",
    "list_email_accounts",
    "get_account_info",
    "get_email_account_info",
]
