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
    """User table - stores basic user information"""
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
    """User tokens table - stores OAuth2 credentials"""
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
    """Email table - stores Gmail message data"""
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
    """Utility class for database operations"""
    
    def __init__(self, database_url: str = "sqlite:///gmail_agent.db"):
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
    
    def get_session(self) -> Session:
        """Get a new database session"""
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
            
            session.commit()
            return emails
    
    def get_user_emails(self, user_id: int, limit: int = 50) -> list[Email]:
        """Get user's stored emails"""
        with self.get_session() as session:
            return session.query(Email).filter(
                Email.user_id == user_id
            ).order_by(Email.date_sent.desc()).limit(limit).all()
