"""
SQLAlchemy database models and utilities for the Gmail agent.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
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
    def __init__(self, database_url: str = "sqlite:///gmail_agent.db"):
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
