"""
Database service — /accounts/* routes.

Spec section 3.2. Filled by agent D1.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.libs.contracts.accounts import AccountDTO, AccountWithSecretDTO
from backend.services.database.manager import DatabaseManager, get_db_manager
from backend.services.database.models import Account

router = APIRouter()


class AccountGetOrCreateRequest(BaseModel):
    """Body of ``POST /accounts/get-or-create``.

    Mirrors ``DatabaseManager.get_or_create_account(primary_email, password_hash)``
    one-for-one; the service does no hashing of its own.
    """

    primary_email: str
    password_hash: str


def _account_dto(account: Account) -> AccountDTO:
    return AccountDTO(
        id=account.id,
        primary_email=account.primary_email,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def _account_with_secret_dto(account: Account) -> AccountWithSecretDTO:
    return AccountWithSecretDTO(
        id=account.id,
        primary_email=account.primary_email,
        password_hash=account.password_hash,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


@router.post("/accounts/get-or-create", response_model=AccountDTO)
async def get_or_create_account(
    payload: AccountGetOrCreateRequest,
    db: DatabaseManager = Depends(get_db_manager),
) -> AccountDTO:
    """Get an existing account by ``primary_email`` or insert a new one.

    Issue **X1 is preserved deliberately**: when the email already exists the
    manager hands back a detached ``Account`` carrying the *caller's*
    ``password_hash`` and the *existing* row's ``id``, and the row itself is not
    modified. So signing up with someone else's email still returns their
    ``account_id``, exactly as the monolith does today. That is a real
    account-takeover bug with its own ticket; do not fix it here.

    ``updated_at`` is ``None`` on this response because the object the manager
    returns was never flushed, which is also today's behavior.
    """
    account = db.get_or_create_account(payload.primary_email, payload.password_hash)
    return _account_dto(account)


# R6: this route may emit ``password_hash`` under ``include_secrets=true`` and must
# NEVER be reachable through the Gateway. It exists for exactly one caller — the
# Accounts service's sign-in loop, which compares the stored hash itself, the way
# the monolith's login handler does today.
@router.get("/accounts", response_model=None)
async def list_accounts(
    primary_email: str | None = None,
    include_secrets: bool = False,
    db: DatabaseManager = Depends(get_db_manager),
) -> list[AccountDTO] | list[AccountWithSecretDTO]:
    """List accounts, or look one up by ``primary_email``.

    With ``primary_email`` this wraps ``get_account_by_email``, which matches at
    most one row, so the response is a list of zero or one element. Without it,
    ``get_all_accounts`` returns every account ordered by ``primary_email``.
    """
    if primary_email is not None:
        account = db.get_account_by_email(primary_email)
        accounts = [account] if account else []
    else:
        accounts = db.get_all_accounts()

    if include_secrets:
        return [_account_with_secret_dto(account) for account in accounts]
    return [_account_dto(account) for account in accounts]


@router.get("/accounts/{account_id}", response_model=AccountDTO)
async def get_account(
    account_id: int,
    db: DatabaseManager = Depends(get_db_manager),
) -> AccountDTO:
    """Fetch one account by id, including ``updated_at``.

    The manager has no by-id accessor, so this is a direct query (Spec 3.2).
    """
    with db.get_session() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        return _account_dto(account)
