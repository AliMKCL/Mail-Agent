"""
Accounts service — public /api/auth/* registration and sign-in routes.

Spec sections 3.1 and 3.3, step 4.3. Ported from ``backend/controllers/registration.py``
with every ``dependencies.db_manager.X()`` call replaced by a Database-service
call (**R1**). Paths, methods, status codes, ``detail`` strings and JSON keys are
unchanged (**R7**) — the golden suite asserts them.

Password hashing stays here. Sign-in reads ``GET /accounts?include_secrets=true``
and keeps the original linear scan comparing ``hashlib.sha256`` hexdigests;
there is deliberately no password-verify endpoint, because moving the comparison
would move the behavior.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import hashlib

from backend.libs.common.http import AsyncServiceClient
from backend.libs.contracts.accounts import (
    AccountDTO,
    AccountWithSecretDTO,
    EmailAccountDTO,
)
from backend.services.accounts.clients import get_database_client

router = APIRouter(tags=["registration"])


class SignInRequest(BaseModel):
    email: str
    password: str


class SignUpRequest(BaseModel):
    email: str
    password: str


# Authentication endpoints
@router.post("/api/auth/signin")
async def sign_in(
    credentials: SignInRequest,
    db: AsyncServiceClient = Depends(get_database_client),
):
    """
    Handle user sign-in from landing page
    Validates credentials and returns success/failure
    """
    try:
        # include_secrets=true is the only way password_hash leaves the
        # Database service, and this loop is its only consumer (R6).
        response = await db.get("/accounts", params={"include_secrets": "true"})
        accounts = [AccountWithSecretDTO(**row) for row in response.json()]

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
                email_accounts_response = await db.get(
                    "/email-accounts", params={"account_id": i.id}
                )
                primary_email_accounts = [
                    EmailAccountDTO(**row) for row in email_accounts_response.json()
                ]
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
async def sign_up(
    credentials: SignUpRequest,
    db: AsyncServiceClient = Depends(get_database_client),
):
    """
    Handle user sign-up from landing page
    Creates new account and email account

    🔴 **X1 is preserved deliberately.** ``get_or_create_account`` hands back,
    for an email that already exists, an object carrying the *caller's*
    ``password_hash`` and the *existing* row's ``id``. So signing up with
    someone else's email and any password returns
    ``{"status": "success", "account_id": <theirs>}``. The stored row is not
    modified, but the client receives a working session for another tenant.

    That is a real account-takeover bug. It is out of scope here, it is
    captured as a golden response
    (``tests/golden/post__api_auth_signup__existing_email_other_password.json``),
    and it has its own ticket. Do not fix it in this file — the golden suite
    will go red.
    """
    try:
        email = credentials.email
        password = credentials.password

        sha256_hash = hashlib.sha256()
        sha256_hash.update(password.encode("utf-8"))
        hashed_password = sha256_hash.hexdigest()

        # Create main account for login
        account_response = await db.post(
            "/accounts/get-or-create",
            json={"primary_email": email, "password_hash": hashed_password},
        )
        account = AccountDTO(**account_response.json())

        # Create email account for this Gmail address
        email_account_response = await db.post(
            "/email-accounts/get-or-create",
            json={
                "account_id": account.id,
                "email": email,
                "provider": "gmail",
                "is_primary": True,
            },
        )
        email_account = EmailAccountDTO(**email_account_response.json())

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
