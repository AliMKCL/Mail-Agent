"""
SQLAlchemy models for the Gmail agent (Spec 2.4).

Lifted verbatim from ``backend/databases/database.py`` — ``Base`` plus the four
model classes. Nothing in this module was changed: every docstring, column
definition and the ``EmailToken.to_credentials`` / ``from_credentials`` pair is
byte-identical to the monolith's, including the tab-indented and
trailing-whitespace lines.

This module and ``manager.py`` are the only place in the Python backend allowed
to import SQLAlchemy or touch ``gmail_agent.db`` (R1).
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from google.oauth2.credentials import Credentials

# SQLAlchemy setup
Base = declarative_base()

class Account(Base):
    """Account table - stores user accounts for authentication
    
    What it stores:
    - id: primary key
    - primary_email: the email used for login
    - password_hash: hashed password for authentication
    - created_at, updated_at: timestamps
    
    What it's for:
    - Represents the actual person/user who logs into the system
    - One account can have multiple EmailAccounts (Gmail/Outlook)
    
    Why it exists:
    - Separates authentication (Account) from email management (EmailAccount)
    - Allows one user to manage multiple Gmail/Outlook accounts
    """
    __tablename__ = 'accounts'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    primary_email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    email_accounts = relationship("EmailAccount", back_populates="account")

class EmailAccount(Base):
    """EmailAccount table - stores Gmail/Outlook accounts
    
    What it stores:
    - id: primary key
    - account_id: foreign key to Account (the owner)
    - email: the Gmail/Outlook email address
    - provider: 'gmail' or 'outlook'
    - is_primary: whether this is the default email account to show
    - created_at, updated_at: timestamps
    
    What it's for:
    - Represents a Gmail or Outlook account that has been connected
    - Stores OAuth credentials via UserToken relationship
    - Links emails fetched from this account
    
    Why it exists:
    - Allows one user (Account) to manage multiple email accounts
    - Separates authentication from email account management
    """
    __tablename__ = 'email_accounts'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    provider = Column(String(50), nullable=False, default='gmail')  # 'gmail' or 'outlook'
    is_primary = Column(Integer, nullable=False, default=0)  # 0 = False, 1 = True (SQLite doesn't have boolean)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    account = relationship("Account", back_populates="email_accounts")
    token = relationship("EmailToken", back_populates="email_account", uselist=False)
    emails = relationship("Email", back_populates="email_account")


class EmailToken(Base):
    """EmailToken table - stores OAuth2 credentials for email accounts
    
    What it stores:
    - access_token: the short-lived OAuth access token
    - refresh_token: refresh token used to obtain new access tokens
    - token_uri, client_id, client_secret: OAuth client config
    - scopes: JSON-encoded list of scopes granted
    - expiry: datetime when the access token expires
    - timestamps: created_at/updated_at
    
    What it's for:
    - Persisting the credentials required to call Google/Microsoft APIs
      for a specific EmailAccount without relying on token.json files
    
    Why it exists:
    - Storing credentials in the DB centralizes token management
    - Each EmailAccount has its own OAuth token
    - Enables programmatic refresh and rotation of tokens
    
    Methods:
    - to_credentials(): Convert DB row to google.oauth2.credentials.Credentials
    - from_credentials(): Create DB model from Credentials object
    """
    __tablename__ = 'email_tokens'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email_account_id = Column(Integer, ForeignKey('email_accounts.id'), unique=True, nullable=False)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    token_uri = Column(String(255), nullable=True)
    client_id = Column(String(255), nullable=True)
    client_secret = Column(String(255), nullable=True)
    scopes = Column(Text, nullable=True)  # JSON array as string
    expiry = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    email_account = relationship("EmailAccount", back_populates="token")
    
    def to_credentials(self) -> Credentials:
        """Convert stored token data to Google Credentials object"""
        scopes_list = json.loads(self.scopes) if self.scopes else []
        
        return Credentials(
            token=self.access_token,
            refresh_token=self.refresh_token,
            token_uri=self.token_uri,
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=scopes_list,
            expiry=self.expiry
        )
    
    @classmethod
    def from_credentials(cls, email_account_id: int, creds: Credentials) -> 'EmailToken':
        """Create EmailToken from Google Credentials object"""
        return cls(
            email_account_id=email_account_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token,
            token_uri=creds.token_uri,
            client_id=creds.client_id,
            client_secret=creds.client_secret,
            scopes=json.dumps(creds.scopes) if creds.scopes else None,
            expiry=creds.expiry
        )

class Email(Base):
    """Email table - stores Gmail/Outlook message data
    
    What it stores:
    - message_id: Gmail's unique message identifier (used to avoid duplicates)
    - thread_id: Gmail thread identifier
    - subject, sender, recipient: header information useful for display/search
    - date_sent: parsed datetime for when the message was sent
    - snippet: Gmail-provided short preview text
    - body_text, body_html: cached message body content
    - created_at: when this row was inserted into the DB
    
    What it's for:
    - Caching and indexing messages locally so the application can present
      recent emails without re-fetching them from Gmail on every view
    
    Why it exists:
    - Local storage improves performance, enables offline reads, and
      provides a durable audit trail of messages the agent has seen
    - Each email belongs to a specific EmailAccount
    """
    __tablename__ = 'emails'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email_account_id = Column(Integer, ForeignKey('email_accounts.id'), nullable=False)
    message_id = Column(String(255), nullable=False)  # Gmail message ID
    thread_id = Column(String(255), nullable=True)
    subject = Column(Text, nullable=True)
    sender = Column(String(500), nullable=True)  # From header
    recipient = Column(String(500), nullable=True)  # To header
    date_sent = Column(DateTime, nullable=True)
    snippet = Column(Text, nullable=True)
    body_text = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    email_account = relationship("EmailAccount", back_populates="emails")

__all__ = ["Base", "Account", "EmailAccount", "EmailToken", "Email"]
