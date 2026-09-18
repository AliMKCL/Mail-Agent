"""
Accounts service — /internal/* routes, including the credential ladder.

Spec sections 3.3 and 3.3.1, step 4.6.

**R6: none of these routes may be reachable through the Gateway.** The Gateway
registers explicit public paths only; nothing under ``/internal`` is mapped.

Credential failures surface as HTTP **409** with body ``{"error": "<string>"}``
— the key is ``error``, not ``detail``, and the string is the *original* error
text from ``gmail_read``/``setup_calendar``. That is what lets User_data map the
failure straight back onto today's behavior: ``gmail.get_service`` re-raises it
so ``/api/sync``'s error-string handlers still match, while
``google_calendar.get_calendar_service`` returns it as ``(None, error)``.
``JSONResponse`` is used rather than ``HTTPException`` precisely so FastAPI does
not wrap the body in ``{"detail": ...}``.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse

from backend.libs.common.errors import UpstreamError, raise_http_from_upstream
from backend.libs.common.http import AsyncServiceClient
from backend.libs.contracts.accounts import (
    AccountDTO,
    CredentialsDTO,
    EmailAccountDTO,
    credentials_to_dto,
    dto_to_credentials,
)
from backend.libs.contracts.stats import AccountStatsDTO
from backend.services.accounts.clients import get_database_client
from backend.services.accounts.credentials import (
    authentication_failed_error,
    resolve_valid_credentials,
)
from backend.services.accounts.google_oauth import (
    authenticate_google_calendar,
    reauthenticate_user_token_failure,
    save_credentials,
)

router = APIRouter(tags=["internal"])


def _credential_error(error: str) -> JSONResponse:
    """409 carrying the original error string under ``error``."""
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"error": error})


# --------------------------------------------------------------------------
# The credential ladder (Spec 3.3.1)
# --------------------------------------------------------------------------


@router.get("/internal/email-accounts/{email_account_id}/credentials", response_model=None)
async def get_credentials(
    email_account_id: int,
    require_scope: Literal["calendar"] | None = None,
    allow_interactive: bool = False,
    refresh: bool = True,
    db: AsyncServiceClient = Depends(get_database_client),
):
    """Resolve usable credentials — see ``credentials.resolve_valid_credentials``.

    Query params select the parity row of Spec 3.3.1:

    * Gmail: ``?allow_interactive=true``
    * Calendar: ``?require_scope=calendar`` (``allow_interactive`` stays false)
    * ``/api/users`` badge: ``?refresh=false``

    ``require_scope`` is a ``Literal`` so an unsupported alias is rejected by
    request validation instead of inventing a new error string.
    """
    creds, error = await resolve_valid_credentials(
        email_account_id,
        require_scope=require_scope,
        allow_interactive=allow_interactive,
        refresh=refresh,
        db=db,
    )
    if error is not None or creds is None:
        return _credential_error(error if error is not None else authentication_failed_error(email_account_id))
    return credentials_to_dto(creds)


@router.put(
    "/internal/email-accounts/{email_account_id}/credentials",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def put_credentials(
    email_account_id: int,
    payload: CredentialsDTO,
    db: AsyncServiceClient = Depends(get_database_client),
) -> Response:
    """Persist possibly-auto-refreshed credentials.

    Replaces ``save_calendar_credentials_after_use`` and the post-``build()``
    save in ``setup_calendar.get_calendar_service``: the caller builds its
    Google client, then PUTs the credentials back in case the Google library
    refreshed them underneath it.
    """
    await save_credentials(db, email_account_id, dto_to_credentials(payload))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/internal/email-accounts/{email_account_id}/reauth", response_model=None)
async def reauth(
    email_account_id: int,
    db: AsyncServiceClient = Depends(get_database_client),
):
    """Force an interactive ``InstalledAppFlow`` re-auth and return the creds.

    Unconditional: stored credentials are not consulted. On failure the 409
    carries ``gmail_read.py:89``'s wording, because the one caller that reports
    this to a human — ``cli/add_user.py`` — used to reach it through
    ``gmail_read.get_service`` and print exactly that text.
    """
    creds = await reauthenticate_user_token_failure(email_account_id, db)
    if not creds:
        return _credential_error(authentication_failed_error(email_account_id))
    return credentials_to_dto(creds)


@router.get("/internal/oauth/auth-url")
async def oauth_auth_url(email_account_id: int | None = None) -> dict:
    """``authenticate_google_calendar`` → ``{auth_url, state}``.

    Always 200. On failure the original returns ``(None, str(e))``, so
    ``auth_url`` is null and ``state`` carries the exception text; the public
    ``/api/auth/google`` handler is what turns that into its own 500.
    """
    auth_url, state = authenticate_google_calendar(email_account_id)
    return {"auth_url": auth_url, "state": state}


# --------------------------------------------------------------------------
# Account / email-account identity (Spec 3.3)
# --------------------------------------------------------------------------


@router.get("/internal/accounts", response_model=list[AccountDTO])
async def list_accounts(
    db: AsyncServiceClient = Depends(get_database_client),
) -> list[AccountDTO]:
    """Every account, without secrets."""
    response = await db.get("/accounts")
    return [AccountDTO(**row) for row in response.json()]


@router.get("/internal/accounts/{account_id}/detail")
async def account_detail(
    account_id: int,
    db: AsyncServiceClient = Depends(get_database_client),
) -> dict:
    """An account plus its email accounts, for ``get_account_info`` and
    ``account://info/{id}``.

    404 with the Database service's ``"Account not found"`` detail when the
    account does not exist.
    """
    try:
        account_response = await db.get(f"/accounts/{account_id}")
    except UpstreamError as err:
        raise_http_from_upstream(err)
    email_accounts_response = await db.get(
        "/email-accounts", params={"account_id": account_id}
    )
    return {
        "account": AccountDTO(**account_response.json()).model_dump(mode="json"),
        "email_accounts": [
            EmailAccountDTO(**row).model_dump(mode="json")
            for row in email_accounts_response.json()
        ],
    }


@router.get("/internal/email-accounts", response_model=list[EmailAccountDTO])
async def list_email_accounts(
    account_id: int | None = None,
    db: AsyncServiceClient = Depends(get_database_client),
) -> list[EmailAccountDTO]:
    """Email accounts for one account, or every one of them.

    Without ``account_id`` this is every email account across all tenants —
    issue X2, preserved.
    """
    params = {} if account_id is None else {"account_id": account_id}
    response = await db.get("/email-accounts", params=params)
    return [EmailAccountDTO(**row) for row in response.json()]


@router.get("/internal/email-accounts/{email_account_id}", response_model=EmailAccountDTO)
async def get_email_account(
    email_account_id: int,
    db: AsyncServiceClient = Depends(get_database_client),
) -> EmailAccountDTO:
    """One email account. 404 ``"Email account not found"`` when absent."""
    try:
        response = await db.get(f"/email-accounts/{email_account_id}")
    except UpstreamError as err:
        raise_http_from_upstream(err)
    return EmailAccountDTO(**response.json())


@router.get("/internal/email-accounts/{email_account_id}/detail")
async def email_account_detail(
    email_account_id: int,
    db: AsyncServiceClient = Depends(get_database_client),
) -> dict:
    """An email account plus its parent account's ``primary_email``.

    ``primary_email`` is ``None`` if the parent account row has vanished; the
    email account itself must exist or this is a 404.
    """
    try:
        response = await db.get(f"/email-accounts/{email_account_id}")
    except UpstreamError as err:
        raise_http_from_upstream(err)
    email_account = EmailAccountDTO(**response.json())

    primary_email = None
    try:
        account_response = await db.get(f"/accounts/{email_account.account_id}")
    except UpstreamError as err:
        if err.status != 404:
            raise
    else:
        primary_email = AccountDTO(**account_response.json()).primary_email

    return {
        "email_account": email_account.model_dump(mode="json"),
        "primary_email": primary_email,
    }


@router.get("/internal/email-accounts/{email_account_id}/primary")
async def primary_email_account(
    email_account_id: int,
    db: AsyncServiceClient = Depends(get_database_client),
) -> dict:
    """``calendar.py:get_primary_email_account_id`` moved verbatim.

    **All three fallbacks return the id that was passed in**, so this route is
    always 200 and never 404:

    1. the email account does not exist → input id
    2. it exists but its account has no primary mailbox → first mailbox, or the
       input id when the account has none at all
    3. anything raises → the ``except`` prints and returns the input id

    Callers read the ``email_account_id`` key, which is always an int.
    """
    return {"email_account_id": await _get_primary_email_account_id(db, email_account_id)}


async def _get_primary_email_account_id(
    db: AsyncServiceClient, email_account_id: int
) -> int:
    """Get the primary email account ID for the account that owns this email account"""
    try:
        # Get the email account
        try:
            response = await db.get(f"/email-accounts/{email_account_id}")
        except UpstreamError as err:
            if err.status == 404:
                return email_account_id  # Fallback to provided ID
            raise
        email_account = EmailAccountDTO(**response.json())

        # Get all email accounts for this account
        email_accounts_response = await db.get(
            "/email-accounts", params={"account_id": email_account.account_id}
        )
        email_accounts = [
            EmailAccountDTO(**row) for row in email_accounts_response.json()
        ]

        # Find the primary one
        for ea in email_accounts:
            if ea.is_primary:
                return ea.id

        # If no primary found, return the first one or the provided ID
        return email_accounts[0].id if email_accounts else email_account_id
    except Exception as e:
        print(f"Error getting primary email account: {e}")
        return email_account_id  # Fallback to provided ID


@router.get("/internal/stats", response_model=AccountStatsDTO)
async def stats(
    db: AsyncServiceClient = Depends(get_database_client),
) -> AccountStatsDTO:
    """``{total_accounts, total_email_accounts}``, straight from the Database service."""
    response = await db.get("/stats/accounts")
    return AccountStatsDTO(**response.json())


__all__ = ["router"]
