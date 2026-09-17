"""
Account, email-account and OAuth-credential DTOs.

Field names and types mirror the SQLAlchemy columns in the Database service
exactly. Note ``EmailAccount.is_primary`` is an ``int`` (0/1) in SQLite, not a
bool; it stays an ``int`` here because some handlers return the raw int and
others call ``bool()`` on it themselves.

This is the one contracts module allowed to import
``google.oauth2.credentials.Credentials``, because ``dto_to_credentials`` must
return that concrete type.
"""

from __future__ import annotations

from datetime import datetime

from google.oauth2.credentials import Credentials
from pydantic import BaseModel


class AccountDTO(BaseModel):
    """An ``accounts`` row, without its secret."""

    id: int
    primary_email: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AccountWithSecretDTO(AccountDTO):
    """An ``accounts`` row including ``password_hash``.

    Only ever produced by the Database service under
    ``include_secrets=true``, and only ever consumed by the Accounts service's
    sign-in loop. Never routed through the Gateway (R6).
    """

    password_hash: str


class EmailAccountDTO(BaseModel):
    """An ``email_accounts`` row."""

    id: int
    account_id: int
    email: str
    provider: str
    is_primary: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CredentialsDTO(BaseModel):
    """An ``email_tokens`` row expressed as Google OAuth2 credential fields."""

    token: str | None = None
    refresh_token: str | None = None
    token_uri: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scopes: list[str] | None = None
    expiry: datetime | None = None


def credentials_to_dto(creds: Credentials) -> CredentialsDTO:
    """Flatten a Google ``Credentials`` object into a wire DTO.

    Mirrors ``EmailToken.from_credentials``: ``scopes`` travels as a list (the
    Database service is responsible for the JSON encoding it does today), and
    ``expiry`` travels as a datetime.
    """
    return CredentialsDTO(
        token=creds.token,
        refresh_token=creds.refresh_token,
        token_uri=creds.token_uri,
        client_id=creds.client_id,
        client_secret=creds.client_secret,
        scopes=list(creds.scopes) if creds.scopes else None,
        expiry=creds.expiry,
    )


def dto_to_credentials(dto: CredentialsDTO) -> Credentials:
    """Rebuild a Google ``Credentials`` object from a wire DTO.

    Mirrors ``EmailToken.to_credentials``, including its quirk that a missing
    scope list becomes ``[]`` rather than ``None``.
    """
    return Credentials(
        token=dto.token,
        refresh_token=dto.refresh_token,
        token_uri=dto.token_uri,
        client_id=dto.client_id,
        client_secret=dto.client_secret,
        scopes=dto.scopes if dto.scopes else [],
        expiry=dto.expiry,
    )


__all__ = [
    "AccountDTO",
    "AccountWithSecretDTO",
    "EmailAccountDTO",
    "CredentialsDTO",
    "credentials_to_dto",
    "dto_to_credentials",
]
