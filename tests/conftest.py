"""
Pytest configuration and shared fixtures for Mail Agent tests.
Updated version with Account/EmailAccount structure.
"""

import pytest
import sys
import os
import hashlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from backend.databases.database import Base, Account, EmailAccount, Email, DatabaseManager


# ============================================================================
# DATABASE FIXTURES
# ============================================================================

@pytest.fixture(scope="function")
def test_db(tmp_path):
    """
    Create a fresh SQLite database file for each test.
    Uses a temp file because in-memory SQLite doesn't share across connections/threads.
    """
    from backend.app import db_manager

    # Create test database file
    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine)

    # Store original values
    original_engine = db_manager.engine
    original_session_local = db_manager.SessionLocal

    # Replace with test database
    db_manager.engine = test_engine
    db_manager.SessionLocal = TestSessionLocal

    yield db_manager

    # Restore original values
    db_manager.engine = original_engine
    db_manager.SessionLocal = original_session_local

    # Clean up (tmp_path handles file deletion automatically)


@pytest.fixture
def client(test_db):
    """Create a TestClient after test_db has set up the database."""
    from backend.app import app
    with TestClient(app) as c:
        yield c


def _hash_password(password: str) -> str:
    """Helper function to hash passwords for test accounts."""
    sha256_hash = hashlib.sha256()
    sha256_hash.update(password.encode("utf-8"))
    return sha256_hash.hexdigest()


@pytest.fixture
def test_account(test_db):
    """Create a test account with primary email account."""
    # Create account with hashed password
    password_hash = _hash_password("testpassword")
    account = test_db.get_or_create_account("testuser@gmail.com", password_hash)
    
    # Create primary email account for this account
    email_account = test_db.get_or_create_email_account(
        account_id=account.id,
        email="testuser@gmail.com",
        provider='gmail',
        is_primary=True
    )
    
    return account, email_account


@pytest.fixture
def test_user(test_db, test_account):
    """Create a test user (returns email_account for backward compatibility)."""
    account, email_account = test_account
    return email_account


@pytest.fixture
def second_account(test_db):
    """Create a second test account with primary email account."""
    # Create account with hashed password
    password_hash = _hash_password("testpassword2")
    account = test_db.get_or_create_account("seconduser@gmail.com", password_hash)
    
    # Create primary email account for this account
    email_account = test_db.get_or_create_email_account(
        account_id=account.id,
        email="seconduser@gmail.com",
        provider='gmail',
        is_primary=True
    )
    
    return account, email_account


@pytest.fixture
def second_user(test_db, second_account):
    """Create a second test user (returns email_account for backward compatibility)."""
    account, email_account = second_account
    return email_account


@pytest.fixture
def user_with_emails(test_db, test_user):
    """Create a user with sample emails."""
    emails = [
        {
            "message_id": "msg_001",
            "subject": "Test Email 1",
            "sender": "sender1@example.com",
            "recipient": "testuser@gmail.com",
            "date_sent": datetime.now() - timedelta(days=1),
            "snippet": "This is a test snippet",
            "body_text": "This is the full body of test email 1. It contains important information.",
            "body_html": "<p>This is the full body of test email 1.</p>"
        },
        {
            "message_id": "msg_002",
            "subject": "Meeting Tomorrow",
            "sender": "boss@company.com",
            "recipient": "testuser@gmail.com",
            "date_sent": datetime.now(),
            "snippet": "Meeting at 10am",
            "body_text": "Please join the meeting tomorrow at 10am in conference room A.",
            "body_html": None
        }
    ]
    # Use email_account_id instead of user_id
    test_db.save_emails(test_user.id, emails)
    return test_user


@pytest.fixture
def second_user_with_emails(test_db, second_user):
    """Create second user with different emails."""
    emails = [
        {
            "message_id": "msg_100",
            "subject": "Second User Email",
            "sender": "friend@example.com",
            "recipient": "seconduser@gmail.com",
            "date_sent": datetime.now(),
            "snippet": "Email for second user",
            "body_text": "This email belongs to the second user only.",
            "body_html": None
        }
    ]
    # Use email_account_id instead of user_id
    test_db.save_emails(second_user.id, emails)
    return second_user


# ============================================================================
# MOCK GOOGLE CALENDAR SERVICE
# ============================================================================

@pytest.fixture
def mock_calendar_service():
    """Mock Google Calendar API service."""
    service = MagicMock()

    # Store events in memory for CRUD testing
    events_store = {
        "event_001": {
            "id": "event_001",
            "summary": "Existing Event",
            "description": "Test description",
            "start": {"dateTime": "2025-11-23T10:00:00Z"},
            "end": {"dateTime": "2025-11-23T11:00:00Z"},
            "extendedProperties": {"private": {"category": "Academic"}}
        }
    }

    # Mock events().list()
    def list_events(**kwargs):
        mock_list = MagicMock()
        mock_list.execute.return_value = {"items": list(events_store.values())}
        return mock_list

    service.events().list = list_events

    # Mock events().insert()
    def insert_event(**kwargs):
        mock_insert = MagicMock()
        new_id = f"event_{len(events_store) + 1:03d}"
        body = kwargs.get("body", {})
        new_event = {
            "id": new_id,
            "summary": body.get("summary", ""),
            "description": body.get("description", ""),
            "start": body.get("start", {}),
            "end": body.get("end", {}),
            "htmlLink": f"https://calendar.google.com/event?id={new_id}"
        }
        events_store[new_id] = new_event
        mock_insert.execute.return_value = new_event
        return mock_insert

    service.events().insert = insert_event

    # Mock events().get()
    def get_event(**kwargs):
        mock_get = MagicMock()
        event_id = kwargs.get("eventId", "event_001")
        if event_id in events_store:
            mock_get.execute.return_value = events_store[event_id]
        else:
            mock_get.execute.side_effect = Exception("Event not found")
        return mock_get

    service.events().get = get_event

    # Mock events().update()
    def update_event(**kwargs):
        mock_update = MagicMock()
        event_id = kwargs.get("eventId")
        body = kwargs.get("body", {})
        if event_id in events_store:
            events_store[event_id].update(body)
            events_store[event_id]["htmlLink"] = f"https://calendar.google.com/event?id={event_id}"
            mock_update.execute.return_value = events_store[event_id]
        return mock_update

    service.events().update = update_event

    # Mock events().delete()
    def delete_event(**kwargs):
        mock_delete = MagicMock()
        event_id = kwargs.get("eventId")
        if event_id in events_store:
            del events_store[event_id]
        mock_delete.execute.return_value = None
        return mock_delete

    service.events().delete = delete_event

    return service, events_store


# ============================================================================
# MOCK VECTOR DATABASE
# ============================================================================

@pytest.fixture
def mock_vector_db():
    """Mock vector database query function."""
    mock_doc1 = MagicMock()
    mock_doc1.page_content = "Meeting tomorrow at 10am in conference room"
    mock_doc1.metadata = {
        "message_id": "msg_002",
        "sender": "boss@company.com",
        "subject": "Meeting Tomorrow",
        "date_sent": "2025-11-22"
    }

    mock_doc2 = MagicMock()
    mock_doc2.page_content = "Project deadline is next Friday"
    mock_doc2.metadata = {
        "message_id": "msg_003",
        "sender": "pm@company.com",
        "subject": "Project Deadline",
        "date_sent": "2025-11-21"
    }

    async def mock_query(query, top_k=3):
        # Return relevant docs based on query keywords
        if "meeting" in query.lower():
            return [mock_doc1]
        elif "deadline" in query.lower():
            return [mock_doc2]
        return [mock_doc1, mock_doc2]

    return mock_query


@pytest.fixture
def mock_llm():
    """Mock LLM response function."""
    def mock_response(query):
        return "Based on your emails, you have a meeting tomorrow at 10am."

    return mock_response

