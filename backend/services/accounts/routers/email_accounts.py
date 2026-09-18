"""
Accounts service — public GET /api/users, POST /api/users and
GET /api/email-account/{email_account_id}.

Spec sections 3.1 and 3.3, step 4.5. Ported from ``backend/controllers/users.py``
with ``dependencies.db_manager.*`` and its two raw ``session.query(...)`` blocks
replaced by Database-service calls (**R1**). Paths, status codes, ``detail``
strings and JSON keys unchanged (**R7**).

Two preserved quirks:

* **X2** — ``GET /api/users`` without ``account_id`` still returns *every*
  email account across all tenants.
* The ``has_oauth_credentials`` badge still uses the **non-refreshing raw read**
  (``refresh=False`` on the credential ladder: no refresh, no re-auth, no scope
  check) and still swallows everything with a bare ``except: pass``.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.libs.common.errors import UpstreamError, raise_http_from_upstream
from backend.libs.common.http import AsyncServiceClient
from backend.libs.contracts.accounts import EmailAccountDTO
from backend.services.accounts.clients import get_database_client
from backend.services.accounts.credentials import resolve_valid_credentials

router = APIRouter(tags=["users"])


class UserCreateRequest(BaseModel):
    email: str
    name: str | None = None
    account_id: int | None = None  # Required when adding email account from dropdown


# This endpoint is called to retrieve all users from the database.
@router.get("/api/users")
async def get_users(
    account_id: int | None = None,
    db: AsyncServiceClient = Depends(get_database_client),
):
    """Get email accounts for a specific account (or all if no account_id provided)"""
    try:
        print(f"[/api/users] Received account_id parameter: {account_id}")
        if account_id:
            # Filter by specific account
            response = await db.get("/email-accounts", params={"account_id": account_id})
            users = [EmailAccountDTO(**row) for row in response.json()]
            print(
                f"[/api/users] Filtered email accounts for account_id={account_id}: {len(users)} found"
            )
        else:
            # Return all (for backward compatibility or admin use) — X2
            response = await db.get("/email-accounts")
            users = [EmailAccountDTO(**row) for row in response.json()]
            print(f"[/api/users] Returning ALL email accounts: {len(users)} found")

        result = []
        for user in users:
            # Check if user has OAuth credentials
            has_credentials = False
            try:
                creds, _ = await resolve_valid_credentials(
                    user.id, refresh=False, db=db
                )
                has_credentials = creds is not None and creds.valid
            except:  # noqa: E722 - bare except preserved from users.py:46
                pass

            result.append(
                {
                    "id": user.id,
                    "account_id": user.account_id,
                    "email": user.email,
                    "provider": user.provider,
                    "is_primary": user.is_primary,
                    "has_oauth_credentials": has_credentials,
                    "created_at": user.created_at.isoformat(),
                }
            )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching users: {e!s}")


# This endpoint is called to fetch detailed information about a specific email account.
@router.get("/api/email-account/{email_account_id}")
async def get_email_account_info(
    email_account_id: int,
    db: AsyncServiceClient = Depends(get_database_client),
):
    """Get specific email account information"""
    try:
        try:
            response = await db.get(f"/email-accounts/{email_account_id}")
        except UpstreamError as err:
            # The Database service already answers 404 with the detail string
            # "Email account not found", which is byte-identical to the one this
            # route raised when it queried the session directly.
            raise_http_from_upstream(err)
        email_account = EmailAccountDTO(**response.json())

        return {
            "id": email_account.id,
            "account_id": email_account.account_id,
            "email": email_account.email,
            "provider": email_account.provider,
            "is_primary": email_account.is_primary,
            "created_at": email_account.created_at.isoformat(),
        }
    except HTTPException:
        raise  # Re-raise HTTPExceptions
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching email account info: {e!s}"
        )


# This endpoint is called when creating a new email account via the dropdown menu.
@router.post("/api/users")
async def create_user_and_auth(
    user_data: UserCreateRequest,
    db: AsyncServiceClient = Depends(get_database_client),
):
    """Add a new email account to the logged-in user's account"""
    try:
        email = user_data.email
        name = user_data.name
        account_id = user_data.account_id

        if not email:
            raise HTTPException(status_code=400, detail="Email is required")

        if not account_id:
            raise HTTPException(
                status_code=400,
                detail="account_id is required. User must be logged in to add email accounts.",
            )

        # Verify the account exists
        try:
            await db.get(f"/accounts/{account_id}")
        except UpstreamError as err:
            if err.status == 404:
                # This route's own wording, not the Database service's
                # "Account not found" (R7).
                raise HTTPException(
                    status_code=404, detail=f"Account with id {account_id} not found"
                )
            raise

        # Create email account linked to the existing account
        # This does NOT create a new Account, only an EmailAccount
        email_account_response = await db.post(
            "/email-accounts/get-or-create",
            json={
                "account_id": account_id,
                "email": email,
                "provider": "gmail",
                "is_primary": False,  # Additional email accounts are not primary
            },
        )
        email_account = EmailAccountDTO(**email_account_response.json())

        # Return email account info (frontend expects this structure)
        return {
            "id": email_account.id,
            "email": email_account.email,
            "account_id": email_account.account_id,
            "is_primary": email_account.is_primary,
            "created_at": email_account.created_at.isoformat(),
            "message": "Email account added successfully. Please authenticate with Google.",
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in create_user_and_auth: {e!s}")
        import traceback

        traceback.print_exc()
        raise HTTPException(
            status_code=500, detail=f"Error creating email account: {e!s}"
        )
