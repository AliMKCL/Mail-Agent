"""
Database service — /email-accounts/* routes (excluding the token sub-routes).

Spec section 3.2. Filled by agent D1.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.libs.contracts.accounts import EmailAccountDTO
from backend.services.database.manager import DatabaseManager, get_db_manager
from backend.services.database.models import EmailAccount

router = APIRouter()


class EmailAccountGetOrCreateRequest(BaseModel):
    """Body of ``POST /email-accounts/get-or-create``.

    Field names and defaults mirror
    ``DatabaseManager.get_or_create_email_account(account_id, email, provider, is_primary)``.
    ``is_primary`` is a bool here because that is what the manager takes; it
    stores 0/1 and the DTO carries the raw int back out.
    """

    account_id: int
    email: str
    provider: str = "gmail"
    is_primary: bool = False


def _email_account_dto(email_account: EmailAccount) -> EmailAccountDTO:
    return EmailAccountDTO(
        id=email_account.id,
        account_id=email_account.account_id,
        email=email_account.email,
        provider=email_account.provider,
        is_primary=email_account.is_primary,
        created_at=email_account.created_at,
        updated_at=email_account.updated_at,
    )


@router.post("/email-accounts/get-or-create", response_model=EmailAccountDTO)
async def get_or_create_email_account(
    payload: EmailAccountGetOrCreateRequest,
    db: DatabaseManager = Depends(get_db_manager),
) -> EmailAccountDTO:
    """Get an existing email account by ``email`` or insert a new one.

    The lookup is by ``email`` alone — an existing row is returned even when it
    belongs to a different ``account_id``, and the requested ``provider`` /
    ``is_primary`` are ignored in that case. That is the monolith's behavior.

    ``updated_at`` is ``None`` here because the manager returns a freshly built
    detached object that was never flushed, as it does today.
    """
    email_account = db.get_or_create_email_account(
        payload.account_id,
        payload.email,
        payload.provider,
        payload.is_primary,
    )
    return _email_account_dto(email_account)


@router.get("/email-accounts", response_model=list[EmailAccountDTO])
async def list_email_accounts(
    account_id: int | None = None,
    db: DatabaseManager = Depends(get_db_manager),
) -> list[EmailAccountDTO]:
    """List email accounts for one account, or every email account.

    With ``account_id`` this is ``get_account_email_accounts`` (ordered
    ``is_primary`` DESC, then ``email``). Without it, ``get_all_email_accounts``
    returns every email account across all tenants, ordered by ``email`` —
    issue X2, preserved deliberately.
    """
    if account_id is not None:
        email_accounts = db.get_account_email_accounts(account_id)
    else:
        email_accounts = db.get_all_email_accounts()
    return [_email_account_dto(email_account) for email_account in email_accounts]


@router.get("/email-accounts/{email_account_id}", response_model=EmailAccountDTO)
async def get_email_account(
    email_account_id: int,
    db: DatabaseManager = Depends(get_db_manager),
) -> EmailAccountDTO:
    """Fetch one email account by id."""
    email_account = db.get_email_account_by_id(email_account_id)
    if not email_account:
        raise HTTPException(status_code=404, detail="Email account not found")
    return _email_account_dto(email_account)
