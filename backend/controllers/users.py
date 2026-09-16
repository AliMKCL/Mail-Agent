"""
Account/email-account management endpoints: list, inspect, and link mailboxes.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import dependencies

router = APIRouter(tags=["users"])


class UserCreateRequest(BaseModel):
    email: str
    name: str | None = None
    account_id: int | None = None  # Required when adding email account from dropdown


# This endpoint is called to retrieve all users from the database.
@router.get("/api/users")
async def get_users(account_id: int | None = None):
    """Get email accounts for a specific account (or all if no account_id provided)"""
    try:
        print(f"[/api/users] Received account_id parameter: {account_id}")
        if account_id:
            # Filter by specific account
            users = dependencies.db_manager.get_account_email_accounts(account_id)
            print(
                f"[/api/users] Filtered email accounts for account_id={account_id}: {len(users)} found"
            )
        else:
            # Return all (for backward compatibility or admin use)
            users = dependencies.db_manager.get_all_email_accounts()
            print(f"[/api/users] Returning ALL email accounts: {len(users)} found")

        result = []
        for user in users:
            # Check if user has OAuth credentials
            has_credentials = False
            try:
                creds = dependencies.db_manager.get_email_account_credentials(user.id)
                has_credentials = creds is not None and creds.valid
            except:
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
async def get_email_account_info(email_account_id: int):
    """Get specific email account information"""
    try:
        from backend.databases.database import EmailAccount

        with dependencies.db_manager.get_session() as session:
            email_account = (
                session.query(EmailAccount).filter_by(id=email_account_id).first()
            )
            if not email_account:
                raise HTTPException(status_code=404, detail="Email account not found")

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
async def create_user_and_auth(user_data: UserCreateRequest):
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
        with dependencies.db_manager.get_session() as session:
            from backend.databases.database import Account

            account = session.query(Account).filter_by(id=account_id).first()
            if not account:
                raise HTTPException(
                    status_code=404, detail=f"Account with id {account_id} not found"
                )

        # Create email account linked to the existing account
        # This does NOT create a new Account, only an EmailAccount
        email_account = dependencies.db_manager.get_or_create_email_account(
            account_id=account_id,
            email=email,
            provider="gmail",
            is_primary=False,  # Additional email accounts are not primary
        )

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
