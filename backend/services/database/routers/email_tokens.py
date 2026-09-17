"""
Database service — /email-accounts/{id}/token routes.

Spec section 3.2. Filled by agent D1.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from backend.libs.contracts.accounts import (
    CredentialsDTO,
    credentials_to_dto,
    dto_to_credentials,
)
from backend.services.database.manager import DatabaseManager, get_db_manager

router = APIRouter()


# R2: the Accounts service is the sole credential authority. These two routes are
# the storage it talks to, and must never be reachable through the Gateway (R6).
@router.put("/email-accounts/{email_account_id}/token", status_code=status.HTTP_204_NO_CONTENT)
async def save_email_token(
    email_account_id: int,
    payload: CredentialsDTO,
    db: DatabaseManager = Depends(get_db_manager),
) -> Response:
    """Create or update the OAuth token row for an email account.

    The DTO is rebuilt into a Google ``Credentials`` object so
    ``save_email_token`` keeps consuming exactly what it consumes today,
    including the JSON encoding of ``scopes`` it does itself.
    """
    db.save_email_token(email_account_id, dto_to_credentials(payload))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/email-accounts/{email_account_id}/token", response_model=CredentialsDTO)
async def get_email_token(
    email_account_id: int,
    db: DatabaseManager = Depends(get_db_manager),
) -> CredentialsDTO:
    """Return the stored credentials for an email account.

    404 means "no token row", which the Accounts service treats as a distinct,
    non-fatal state in the credential ladder.
    """
    credentials = db.get_email_account_credentials(email_account_id)
    if credentials is None:
        raise HTTPException(status_code=404, detail="Credentials not found")
    return credentials_to_dto(credentials)
