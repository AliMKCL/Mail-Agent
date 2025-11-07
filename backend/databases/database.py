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

class User(Base):
    """User table - stores basic user information

    What it stores:
    - id, email, name: basic identity fields
    - created_at, updated_at: timestamps for bookkeeping
    - relationships to the user's token (UserToken) and emails (Email)

    What it's for:
    - Represents a person/account in the local system for which we store
      OAuth credentials and cached email data.

    Why it exists:
    - We need a stable DB entity to associate tokens and email records with
      a particular owner. Using a User table lets the system support
      multiple Gmail accounts in the same database and simplifies lookups
      and joins (e.g. find all emails for a given user).
    """
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    token = relationship("UserToken", back_populates="user", uselist=False)
    emails = relationship("Email", back_populates="user")

class UserToken(Base):
    """User tokens table - stores OAuth2 credentials

    What it stores:
    - access_token: the short-lived OAuth access token
    - refresh_token: refresh token used to obtain new access tokens
    - token_uri, client_id, client_secret: OAuth client config
    - scopes: JSON-encoded list of scopes granted
    - expiry: datetime when the access token expires
    - timestamps: created_at/updated_at

    What it's for:
    - Persisting the credentials required to call Google APIs on behalf of
      a User without relying on token.json files on disk.

    Why it exists:
    - Storing credentials in the DB centralizes token management, makes the
      tokens available to any process that can access the DB (useful when
      running in containers or on a remote server), and enables programmatic
      refresh and rotation of tokens.

    Methods (brief):
    - to_credentials(): Convert the DB row into a google.oauth2.credentials.Credentials
      object usable by Google client libraries.
    - from_credentials(): Class helper to construct the DB model from a
      Credentials object (useful when saving freshly-obtained credentials).
    """
    __tablename__ = 'user_tokens'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True, nullable=False)
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
    user = relationship("User", back_populates="token")
    
    def to_credentials(self) -> Credentials:
        """Convert stored token data to Google Credentials object"""
        scopes_list = json.loads(self.scopes) if self.scopes else []
        
        return Credentials(
            token=self.access_token,
            refresh_token=self.refresh_token,
            token_uri=self.token_uri,
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=scopes_list
        )
    
    @classmethod
    def from_credentials(cls, user_id: int, creds: Credentials) -> 'UserToken':
        """Create UserToken from Google Credentials object"""
        return cls(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token,
            token_uri=creds.token_uri,
            client_id=creds.client_id,
            client_secret=creds.client_secret,
            scopes=json.dumps(creds.scopes) if creds.scopes else None,
            expiry=creds.expiry
        )

class Email(Base):
    """Email table - stores Gmail message data

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
      recent emails without re-fetching them from Gmail on every view.

    Why it exists:
    - Local storage improves performance, enables offline reads, and
      provides a durable audit trail of messages the agent has seen.
    """
    __tablename__ = 'emails'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
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
    user = relationship("User", back_populates="emails")

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
    
    def get_or_create_user(self, email: str, name: Optional[str] = None) -> User:
        """Get existing user or create new one"""
        with self.get_session() as session:
            user = session.query(User).filter(User.email == email).first()
            if not user:
                user = User(email=email, name=name)
                session.add(user)
                session.commit()
                session.refresh(user)
            return user
    
    def save_user_token(self, user_id: int, credentials: Credentials) -> UserToken:
        """Save or update user's OAuth token"""
        with self.get_session() as session:
            # Check if token exists
            token = session.query(UserToken).filter(UserToken.user_id == user_id).first()
            
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
                token = UserToken.from_credentials(user_id, credentials)
                session.add(token)
            
            session.commit()
            session.refresh(token)
            return token
    
    def get_user_credentials(self, user_id: int) -> Optional[Credentials]:
        """Get user's stored credentials"""
        with self.get_session() as session:
            token = session.query(UserToken).filter(UserToken.user_id == user_id).first()
            if token:
                return token.to_credentials()
            return None
    
    def save_emails(self, user_id: int, email_data: list) -> list[Email]:
        """Save email data to database"""
        with self.get_session() as session:
            emails = []
            for data in email_data:
                # Check if email already exists
                existing = session.query(Email).filter(
                    Email.user_id == user_id,
                    Email.message_id == data['message_id']
                ).first()
                
                if not existing:
                    email = Email(
                        user_id=user_id,
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
            
            session.commit()    # Commit addition to the database (persist the changes)
            return emails
    
    def get_user_emails(self, user_id: int, limit: int = 50) -> list[Email]:
        """Get user's stored emails"""
        with self.get_session() as session:
            return session.query(Email).filter(
                Email.user_id == user_id
            ).order_by(Email.date_sent.desc()).limit(limit).all()

    def get_latest_email_date(self, user_id: int) -> Optional[datetime]:
        """Get the date of the most recent email stored for a user"""
        with self.get_session() as session:
            latest_email = session.query(Email).filter(
                Email.user_id == user_id
            ).order_by(Email.date_sent.desc()).first()
            
            return latest_email.date_sent if latest_email else None

    def get_all_users(self) -> list[User]:
        """Get all users from the database"""
        with self.get_session() as session:
            return session.query(User).order_by(User.email).all()
