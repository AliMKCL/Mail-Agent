"""
``DatabaseManager`` — the SQLAlchemy access layer for the Gmail agent (Spec 2.4).

Lifted verbatim from ``backend/databases/database.py``. The **only** change is
the constructor default, which now reads ``settings.DATABASE_URL`` instead of a
hardcoded ``"sqlite:///gmail_agent.db"`` (Spec 2.1). Every quirk is preserved
deliberately, including:

- ``get_or_create_account`` returning a freshly constructed *detached* ``Account``
  that carries the **caller's** ``password_hash`` alongside the **existing** row's
  ``id`` (issue X1 — a real security bug, out of scope for this refactor).
- ``save_emails`` inserting only rows whose ``(email_account_id, message_id)`` pair
  is absent and returning **only** the newly created objects; callers use
  ``len()`` of that return value as their "new emails" count.
- The tab-indented lines after ``get_all_accounts`` / ``get_all_email_accounts``.

``get_db_manager`` at the bottom is the provider FastAPI handlers depend on, so
``app.dependency_overrides`` can repoint the service at a throwaway database in
tests.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from google.oauth2.credentials import Credentials

from backend.libs.common.config import settings
from backend.services.database.models import (
    Account,
    Base,
    Email,
    EmailAccount,
    EmailToken,
)

# Database utilities
class DatabaseManager:
    """Utility class for database operations

    Responsibilities and what it provides:
    - Initialize the SQLAlchemy engine and create tables when needed.
    - Expose a session factory via get_session() for transactional work.
    - Convenience methods that encapsulate common operations used by the
      Gmail agent, such as creating or finding a user, saving OAuth tokens,
      storing fetched emails, and querying a user's cached emails.

    Why it exists:
    - Centralizes DB access patterns, keeps SQLAlchemy setup code in one
      place, and provides simple, testable operations that higher-level
      modules (the Gmail reader, webhook service, scheduler, etc.) can call.

    Method summaries:
    - get_session(): return a new Session instance (context-managed usage)
    - get_or_create_user(email, name): find or insert a User row
    - save_user_token(user_id, credentials): create/update UserToken rows
      from google Credentials objects. Important to persist refresh tokens.
    - get_user_credentials(user_id): reconstruct a Credentials object from
      the stored token row (returns None if no token present).
    - save_emails(user_id, email_data): insert new Email rows for the user
      (idempotent for message_id) and return the list of created Email objects.
    - get_user_emails(user_id, limit): fetch recent emails from the DB.
    """
    def __init__(self, database_url: str = settings.DATABASE_URL):
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
    
    def get_session(self) -> Session:
        """Get a new database session (object to access the database)"""
        return self.SessionLocal()
    
    # Account management methods
    def get_or_create_account(self, primary_email: str, password_hash: str) -> Account:
        """Get existing account or create new one"""
        with self.get_session() as session:
            account = session.query(Account).filter(Account.primary_email == primary_email).first()
            if not account:
                account = Account(primary_email=primary_email, password_hash=password_hash)
                session.add(account)
                session.commit()
                session.refresh(account)
            # Access ID before session closes
            account_id = account.id
            account_email = account.primary_email
            account_created = account.created_at
        # Return a new detached object with the data
        result = Account(primary_email=account_email, password_hash=password_hash)
        result.id = account_id
        result.created_at = account_created
        return result
    
    def get_account_by_email(self, primary_email: str) -> Optional[Account]:
        """Get account by primary email"""
        with self.get_session() as session:
            return session.query(Account).filter(Account.primary_email == primary_email).first()
    
    def get_all_accounts(self) -> list[Account]:
        """Get all accounts from the database"""
        with self.get_session() as session:
            return session.query(Account).order_by(Account.primary_email).all()
	
    def get_all_email_accounts(self) -> list[EmailAccount]:
        """Get all email accounts from the database"""
        with self.get_session() as session:
            return session.query(EmailAccount).order_by(EmailAccount.email).all()
	
    # EmailAccount management methods
    def get_or_create_email_account(self, account_id: int, email: str, provider: str = 'gmail', is_primary: bool = False) -> EmailAccount:
        """Get existing email account or create new one"""
        with self.get_session() as session:
            email_account = session.query(EmailAccount).filter(EmailAccount.email == email).first()
            if not email_account:
                email_account = EmailAccount(
                    account_id=account_id,
                    email=email,
                    provider=provider,
                    is_primary=1 if is_primary else 0
                )
                session.add(email_account)
                session.commit()
                session.refresh(email_account)
            # Access data before session closes
            ea_id = email_account.id
            ea_account_id = email_account.account_id
            ea_email = email_account.email
            ea_provider = email_account.provider
            ea_is_primary = email_account.is_primary
            ea_created = email_account.created_at
        # Return detached object
        result = EmailAccount(
            account_id=ea_account_id,
            email=ea_email,
            provider=ea_provider,
            is_primary=ea_is_primary
        )
        result.id = ea_id
        result.created_at = ea_created
        return result
    
    def get_account_email_accounts(self, account_id: int) -> list[EmailAccount]:
        """Get all email accounts for a specific account"""
        with self.get_session() as session:
            return session.query(EmailAccount).filter(
                EmailAccount.account_id == account_id
            ).order_by(EmailAccount.is_primary.desc(), EmailAccount.email).all()
    
    def get_email_account_by_id(self, email_account_id: int) -> Optional[EmailAccount]:
        """Get email account by ID"""
        with self.get_session() as session:
            return session.query(EmailAccount).filter(EmailAccount.id == email_account_id).first()
    
    # OAuth token management methods
    def save_email_token(self, email_account_id: int, credentials: Credentials) -> EmailToken:
        """Save or update email account's OAuth token"""
        with self.get_session() as session:
            # Check if token exists
            token = session.query(EmailToken).filter(EmailToken.email_account_id == email_account_id).first()
            
            if token:
                # Update existing token
                token.access_token = credentials.token
                token.refresh_token = credentials.refresh_token
                token.token_uri = credentials.token_uri
                token.client_id = credentials.client_id
                token.client_secret = credentials.client_secret
                token.scopes = json.dumps(credentials.scopes) if credentials.scopes else None
                token.expiry = credentials.expiry
                token.updated_at = datetime.utcnow()
            else:
                # Create new token
                token = EmailToken.from_credentials(email_account_id, credentials)
                session.add(token)
            
            session.commit()
            session.refresh(token)
            return token
    
    def get_email_account_credentials(self, email_account_id: int) -> Optional[Credentials]:
        """Get email account's stored OAuth credentials"""
        with self.get_session() as session:
            token = session.query(EmailToken).filter(EmailToken.email_account_id == email_account_id).first()
            if token:
                return token.to_credentials()
            return None
    
    # Email management methods
    def save_emails(self, email_account_id: int, email_data: list) -> list[Email]:
        """Save email data to database"""
        with self.get_session() as session:
            emails = []
            for data in email_data:
                # Check if email already exists
                existing = session.query(Email).filter(
                    Email.email_account_id == email_account_id,
                    Email.message_id == data['message_id']
                ).first()
                
                if not existing:
                    email = Email(
                        email_account_id=email_account_id,
                        message_id=data['message_id'],
                        subject=data.get('subject'),
                        sender=data.get('sender'),
                        recipient=data.get('recipient'),
                        date_sent=data.get('date_sent'),
                        snippet=data.get('snippet'),
                        body_text=data.get('body_text'),
                        body_html=data.get('body_html')
                    )
                    session.add(email)
                    emails.append(email)
            
            session.commit()
            return emails
    
    def get_email_account_emails(self, email_account_id: int, limit: int = 50) -> list[Email]:
        """Get emails for a specific email account"""
        with self.get_session() as session:
            return session.query(Email).filter(
                Email.email_account_id == email_account_id
            ).order_by(Email.date_sent.desc()).limit(limit).all()

    def get_latest_email_date(self, email_account_id: int) -> Optional[datetime]:
        """Get the date of the most recent email stored for an email account"""
        with self.get_session() as session:
            latest_email = session.query(Email).filter(
                Email.email_account_id == email_account_id
            ).order_by(Email.date_sent.desc()).first()
            
            return latest_email.date_sent if latest_email else None

@lru_cache(maxsize=1)
def get_db_manager() -> DatabaseManager:
    """Provider for the process-wide ``DatabaseManager``.

    Built on first use rather than at import time, so importing this module
    never creates an engine or runs ``create_all``. Handlers take it via
    ``Depends(get_db_manager)``; tests replace it through
    ``app.dependency_overrides``.
    """
    return DatabaseManager()


__all__ = ["DatabaseManager", "get_db_manager"]
