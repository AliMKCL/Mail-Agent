"""
Authentication endpoints: user sign-up and sign-in (password-based).
"""

import hashlib

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import dependencies

router = APIRouter(tags=["registration"])


class SignInRequest(BaseModel):
    email: str
    password: str


class SignUpRequest(BaseModel):
    email: str
    password: str


# Authentication endpoints
@router.post("/api/auth/signin")
async def sign_in(credentials: SignInRequest):
    """
    Handle user sign-in from landing page
    Validates credentials and returns success/failure
    """
    try:
        accounts = dependencies.db_manager.get_all_accounts()

        password = credentials.password

        sha256_hash = hashlib.sha256()

        sha256_hash.update(
            password.encode("utf-8")
        )  # hashlib requires bytes, not a plain string
        hashed_password = sha256_hash.hexdigest()

        for i in accounts:
            if (
                i.primary_email == credentials.email
                and i.password_hash == hashed_password
            ):
                # Get primary email account for this account
                primary_email_accounts = (
                    dependencies.db_manager.get_account_email_accounts(i.id)
                )
                primary_email_account_id = None
                if primary_email_accounts and len(primary_email_accounts) > 0:
                    # Find the primary one or use first
                    for ea in primary_email_accounts:
                        if ea.is_primary:
                            primary_email_account_id = ea.id
                            break
                    if not primary_email_account_id:
                        primary_email_account_id = primary_email_accounts[0].id

                print(
                    f"[/api/auth/signin] Sign-in successful for account_id={i.id}, email_account_id={primary_email_account_id}"
                )
                return {
                    "status": "success",
                    "message": "Sign-in successful",
                    "account_id": i.id,
                    "email_account_id": primary_email_account_id,
                }

        # If we get here, no account matched
        raise HTTPException(status_code=401, detail="Invalid email or password")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sign-in error: {e!s}")


@router.post("/api/auth/signup")
async def sign_up(credentials: SignUpRequest):
    """
    Handle user sign-up from landing page
    Creates new account and email account
    """
    try:
        email = credentials.email
        password = credentials.password

        sha256_hash = hashlib.sha256()
        sha256_hash.update(password.encode("utf-8"))
        hashed_password = sha256_hash.hexdigest()

        # Create main account for login
        account = dependencies.db_manager.get_or_create_account(email, hashed_password)

        # Create email account for this Gmail address
        email_account = dependencies.db_manager.get_or_create_email_account(
            account_id=account.id, email=email, provider="gmail", is_primary=True
        )

        # Note: OAuth flow happens separately after sign-up
        # User will connect their Gmail account from the dashboard

        return {
            "status": "success",
            "message": "Sign-up successful",
            "account_id": account.id,
            "email_account_id": email_account.id,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sign-up error: {e!s}")
