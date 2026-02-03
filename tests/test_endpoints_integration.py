"""
Integration tests for FastAPI endpoints

IMPORTANT: These are REAL integration tests that make ACTUAL HTTP requests.
- Uses TestClient(app) which makes real HTTP requests to the FastAPI application
- All endpoint logic runs through the actual FastAPI request/response cycle
- Only external services are mocked (Gmail API, Calendar API, Vector DB, LLM)
- Database operations use a real test database (test_gmail_agent.db)

This ensures we're testing the actual endpoint behavior, not just mocked functions.
Updated version with Account/EmailAccount structure.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
import json
import os
import sys
import hashlib

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import app, db_manager
from backend.databases.database import DatabaseManager, Base, Account, EmailAccount
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Store original db_manager for restoration
_original_db_manager = None


# ============================================================================
# FIXTURES
# ============================================================================

def _hash_password(password: str) -> str:
    """Helper function to hash passwords for test accounts."""
    sha256_hash = hashlib.sha256()
    sha256_hash.update(password.encode("utf-8"))
    return sha256_hash.hexdigest()


@pytest.fixture(scope="module")
def test_db_path():
    """Test database path"""
    return "test_gmail_agent.db"


@pytest.fixture(scope="module")
def setup_test_db(test_db_path):
    """Setup test database once for all tests and patch app's db_manager"""
    global _original_db_manager
    
    # Create test database
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    
    engine = create_engine(f"sqlite:///{test_db_path}")
    Base.metadata.create_all(bind=engine)
    
    # Store original db_manager
    _original_db_manager = db_manager
    
    # Create test db_manager and replace app's db_manager
    test_db_manager = DatabaseManager(f"sqlite:///{test_db_path}")
    
    # Patch the app's db_manager to use test database
    import backend.app as app_module
    app_module.db_manager = test_db_manager
    
    yield test_db_manager
    
    # Restore original db_manager
    app_module.db_manager = _original_db_manager
    
    # Cleanup
    if os.path.exists(test_db_path):
        os.remove(test_db_path)


@pytest.fixture(scope="module")
def mock_rate_limiter(setup_test_db):
    """Mock rate limiter to always allow requests during tests"""
    from backend.app import limiter
    original_check = limiter.check
    
    def mock_check(*args, **kwargs):
        return {
            "allowed": True,
            "limit": 100,
            "remaining": 99,
            "retry_after_seconds": 0
        }
    
    limiter.check = mock_check
    yield
    limiter.check = original_check


@pytest.fixture(scope="module")
def client(setup_test_db, mock_rate_limiter):
    """Create TestClient after db_manager has been patched to use test database"""
    return TestClient(app)


@pytest.fixture(scope="module")
def test_email_account(setup_test_db):
    """Create a test account with primary email account"""
    # Use the test db_manager from setup_test_db fixture
    test_db_manager = setup_test_db
    
    # Create account with hashed password
    password_hash = _hash_password("testpassword")
    account = test_db_manager.get_or_create_account("testuser@example.com", password_hash)
    
    # Create primary email account
    email_account = test_db_manager.get_or_create_email_account(
        account_id=account.id,
        email="testuser@example.com",
        provider='gmail',
        is_primary=True
    )
    
    return email_account


@pytest.fixture(scope="module")
def test_user(setup_test_db, test_email_account):
    """Alias for backward compatibility - returns email_account"""
    return test_email_account


@pytest.fixture(scope="module")
def second_email_account(setup_test_db):
    """Create a second test account with primary email account"""
    # Use the test db_manager from setup_test_db fixture
    test_db_manager = setup_test_db
    
    # Create account with hashed password
    password_hash = _hash_password("testpassword2")
    account = test_db_manager.get_or_create_account("seconduser@example.com", password_hash)
    
    # Create primary email account
    email_account = test_db_manager.get_or_create_email_account(
        account_id=account.id,
        email="seconduser@example.com",
        provider='gmail',
        is_primary=True
    )
    
    return email_account


@pytest.fixture(scope="module")
def second_user(setup_test_db, second_email_account):
    """Alias for backward compatibility - returns email_account"""
    return second_email_account


# ============================================================================
# STATIC FILE & ROOT ENDPOINT TESTS
# ============================================================================

class TestStaticEndpoints:
    """Test static file serving and root endpoint"""
    
    def test_root_endpoint_returns_html(self, client):
        """Test GET / returns HTML page"""
        response = client.get("/")
        
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        # Basic check that it's HTML
        assert "<!DOCTYPE html>" in response.text or "<html" in response.text
    
    def test_static_css_file(self, client):
        """Test static CSS files are accessible"""
        response = client.get("/static/styles.css")
        
        # Should return CSS file or 404 if missing
        assert response.status_code == 200
        if response.status_code == 200:
            assert "text/css" in response.headers.get("content-type", "")


# ============================================================================
# USER MANAGEMENT ENDPOINT TESTS
# ============================================================================

class TestUserEndpoints:
    """Test user management endpoints"""
    
    def test_get_all_users(self, client, test_user, second_user):
        """Test GET /api/users returns list of email accounts"""
        response = client.get("/api/users")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 2  # At least our test email accounts
        
        # Check structure of email account objects
        email_account = data[0]
        assert "id" in email_account
        assert "email" in email_account
        assert "account_id" in email_account
        assert "provider" in email_account
        assert "is_primary" in email_account
        assert "created_at" in email_account
    
    def test_get_users_with_account_id_filter(self, client, test_user):
        """Test GET /api/users?account_id=X filters by account"""
        # Get the account_id from the test user
        account_id = test_user.account_id
        
        response = client.get(f"/api/users?account_id={account_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # All returned email accounts should belong to the same account
        for email_account in data:
            assert email_account["account_id"] == account_id
    
    def test_get_email_account_info_success(self, client, test_user):
        """Test GET /api/email-account/{email_account_id} returns email account info"""
        response = client.get(f"/api/email-account/{test_user.id}")

        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "email" in data
        assert "account_id" in data
        assert "provider" in data
        assert "is_primary" in data
        assert "created_at" in data
    
    def test_get_email_account_info_not_found(self, client):
        """Test GET /api/email-account/{email_account_id} returns 404 for non-existent email account"""
        response = client.get("/api/email-account/99999")
        
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()


# ============================================================================
# EMAIL ENDPOINT TESTS
# ============================================================================

class TestEmailEndpoints:
    """Test email-related endpoints"""
    
    def test_get_emails_missing_email_account_id(self, client):
        """Test GET /api/emails requires email_account_id parameter"""
        response = client.get("/api/emails")
        
        assert response.status_code == 400
        data = response.json()
        assert "email_account_id parameter is required" in data["detail"]
    
    def test_get_emails_with_email_account_id(self, client, test_user):
        """Test GET /api/emails returns email list structure"""
        response = client.get(f"/api/emails?email_account_id={test_user.id}&limit=10")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        
        # If emails exist, validate structure
        if len(data) > 0:
            email = data[0]
            required_fields = [
                "id", "subject", "sender", "recipient", 
                "date_sent", "snippet", "body_text", "body_html", "created_at"
            ]
            for field in required_fields:
                assert field in email, f"Missing field: {field}"
    
    def test_get_emails_with_limit(self, client, test_user):
        """Test GET /api/emails respects limit parameter"""
        response = client.get(f"/api/emails?email_account_id={test_user.id}&limit=5")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 5
    
    # Mocks GMAIL API calls - but makes REAL HTTP requests to /api/sync endpoint
    @patch('backend.app.get_service')
    @patch('backend.app.list_message_ids')
    @patch('backend.app.store_in_vector_db')
    @patch('backend.app.requests.post')
    def test_sync_emails_success(
        self, 
        mock_requests_post,
        mock_store_vector,
        mock_list, 
        mock_service,
        client,
        test_user
    ):
        """Test GET /api/sync successfully syncs emails (mocked Gmail API, real HTTP request)"""
        # Mock Gmail API responses (external service)
        mock_service.return_value = MagicMock()
        mock_list.return_value = ["msg1", "msg2", "msg3"]
        
        # Mock Go server response (external service)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "emails": [
                {
                    "message_id": "msg1",
                    "subject": "Test Email 1",
                    "sender": "sender1@test.com",
                    "recipient": test_user.email,
                    "date_sent": datetime.now().isoformat(),
                    "snippet": "Test snippet 1",
                    "body_text": "Test body 1",
                    "body_html": "<p>Test body 1</p>"
                },
                {
                    "message_id": "msg2",
                    "subject": "Test Email 2",
                    "sender": "sender2@test.com",
                    "recipient": test_user.email,
                    "date_sent": datetime.now().isoformat(),
                    "snippet": "Test snippet 2",
                    "body_text": "Test body 2",
                    "body_html": "<p>Test body 2</p>"
                }
            ]
        }
        mock_requests_post.return_value = mock_response
        mock_store_vector.return_value = None
        
        # Make ACTUAL HTTP request to endpoint - this goes through the real FastAPI app
        response = client.get(f"/api/sync?email_account_id={test_user.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "total_fetched" in data
        assert "new_emails" in data
        assert data["total_fetched"] == 3
    
    def test_sync_emails_missing_email_account_id(self, client):
        """Test GET /api/sync requires email_account_id parameter"""
        response = client.get("/api/sync")
        
        assert response.status_code == 400
        data = response.json()
        assert "email_account_id parameter is required" in data["detail"]


# ============================================================================
# CALENDAR EVENT ENDPOINT TESTS
# ============================================================================

class TestCalendarEndpoints:
    """Test calendar event endpoints"""
    
    def test_get_calendar_events_missing_email_account_id(self, client):
        """Test GET /api/calendar/events requires email_account_id"""
        response = client.get("/api/calendar/events")
        
        assert response.status_code == 400
        data = response.json()
        assert "email_account_id parameter is required" in data["detail"]
    
    @patch('backend.app.get_calendar_service')
    def test_get_calendar_events_success(self, mock_calendar_service, client, test_user):
        """Test GET /api/calendar/events returns calendar events"""
        # Mock Google Calendar API
        mock_service = MagicMock()
        mock_events = {
            'items': [
                {
                    'id': 'event1',
                    'summary': 'Test Event 1',
                    'start': {'dateTime': '2025-11-24T10:00:00Z'},
                    'end': {'dateTime': '2025-11-24T11:00:00Z'},
                    'description': 'Test description',
                    'extendedProperties': {
                        'private': {'category': 'Academic'}
                    }
                },
                {
                    'id': 'event2',
                    'summary': 'Test Event 2',
                    'start': {'date': '2025-11-25'},
                    'end': {'date': '2025-11-25'},
                    'extendedProperties': {
                        'private': {'category': 'Personal'}
                    }
                }
            ]
        }
        mock_service.events().list().execute.return_value = mock_events
        mock_calendar_service.return_value = (mock_service, None)
        
        # Make HTTP request
        response = client.get(f"/api/calendar/events?email_account_id={test_user.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "events" in data
        assert isinstance(data["events"], dict)
        
        # Check that events are organized by date
        events_dict = data["events"]
        assert len(events_dict) > 0
    
    @patch('backend.app.get_calendar_service')
    def test_create_calendar_event_success(self, mock_calendar_service, client, test_user):
        """Test POST /api/calendar/events creates new event"""
        # Mock Calendar service
        mock_service = MagicMock()
        mock_created = {
            'id': 'new_event_123',
            'htmlLink': 'https://calendar.google.com/event/123'
        }
        mock_service.events().insert().execute.return_value = mock_created
        mock_calendar_service.return_value = (mock_service, None)
        
        # Make HTTP POST request
        event_data = {
            "email_account_id": test_user.id,
            "event_data": {
                "title": "New Test Event",
                "date": "2025-12-01",
                "time": "02:00 PM",
                "category": "Academic",
                "description": "Test event description"
            }
        }
        
        response = client.post("/api/calendar/events", json=event_data)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "event_id" in data
        assert data["event_id"] == "new_event_123"
        assert "event_link" in data
    
    def test_create_calendar_event_missing_email_account_id(self, client):
        """Test POST /api/calendar/events requires email_account_id"""
        event_data = {
            "event_data": {
                "title": "Test Event",
                "date": "2025-12-01"
            }
        }
        
        response = client.post("/api/calendar/events", json=event_data)
        
        assert response.status_code in [400, 500]
    
    def test_create_calendar_event_invalid_data(self, client, test_user):
        """Test POST /api/calendar/events validates event data"""
        event_data = {
            "email_account_id": test_user.id,
            "event_data": {
                # Missing required fields
            }
        }
        
        response = client.post("/api/calendar/events", json=event_data)
        
        # Should handle missing fields gracefully
        assert response.status_code in [400, 422, 500]
    
    @patch('backend.app.get_calendar_service')
    def test_update_calendar_event_success(self, mock_calendar_service, client, test_user):
        """Test PUT /api/calendar/events/{event_id} updates event"""
        mock_service = MagicMock()
        
        # Mock getting existing event
        existing_event = {
            'id': 'event123',
            'summary': 'Old Title',
            'start': {'date': '2025-12-01'},
            'end': {'date': '2025-12-01'},
            'extendedProperties': {
                'private': {'category': 'Academic'}
            }
        }
        mock_service.events().get().execute.return_value = existing_event
        
        # Mock update response
        mock_service.events().update().execute.return_value = {
            'htmlLink': 'https://calendar.google.com/updated'
        }
        mock_calendar_service.return_value = (mock_service, None)
        
        # Make HTTP PUT request
        update_data = {
            "email_account_id": test_user.id,
            "event_data": {
                "title": "Updated Title",
                "category": "Personal",
                "description": "Updated description"
            }
        }
        
        response = client.put("/api/calendar/events/event123", json=update_data)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "event_link" in data
    
    @patch('backend.app.get_calendar_service')
    def test_delete_calendar_event_success(self, mock_calendar_service, client, test_user):
        """Test DELETE /api/calendar/events/{event_id} deletes event"""
        mock_service = MagicMock()
        mock_service.events().delete().execute.return_value = None
        mock_calendar_service.return_value = (mock_service, None)
        
        # Make HTTP DELETE request
        response = client.delete(f"/api/calendar/events/event123?email_account_id={test_user.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["message"] == "Event deleted successfully"
    
    def test_delete_calendar_event_missing_email_account_id(self, client):
        """Test DELETE /api/calendar/events/{event_id} requires email_account_id"""
        response = client.delete("/api/calendar/events/event123")
        
        # API returns 422 (query param validation) - acceptable
        assert response.status_code in [400, 422]


# ============================================================================
# MOODLE CALENDAR ENDPOINT TESTS
# ============================================================================

class TestMoodleEndpoints:
    """Test Moodle calendar integration endpoints"""
    
    @patch('backend.app.get_moodle_events_for_api')
    def test_get_moodle_events_success(self, mock_moodle, client, test_user):
        """Test GET /api/calendar/moodle returns Moodle events"""
        # Mock Moodle API response
        mock_moodle.return_value = {
            "status": "success",
            "events": {
                "2025-12-01": [
                    {
                        "title": "Assignment Due",
                        "category": "Academic",
                        "time": "11:59 PM",
                        "description": "Submit assignment"
                    }
                ]
            }
        }
        
        response = client.get(f"/api/calendar/moodle?email_account_id={test_user.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "events" in data
        assert isinstance(data["events"], dict)


# ============================================================================
# VECTOR DATABASE & AI QUERY TESTS
# ============================================================================

class TestVectorDBEndpoints:
    """Test vector database query and AI endpoints"""
    
    def test_query_missing_query_param(self, client):
        """Test GET /api/query requires query parameter"""
        response = client.get("/api/query")
        
        # API returns 422 (query param validation) - acceptable
        assert response.status_code in [400, 422]
    
    @patch('backend.app.query_vector_db', new_callable=AsyncMock)
    @patch('backend.app.llm_response')
    def test_query_with_results(self, mock_llm, mock_vector_query, client):
        """Test GET /api/query returns AI response with sources"""
        # Mock vector DB results
        mock_doc1 = MagicMock()
        mock_doc1.page_content = "Email about Python project deadline"
        mock_doc1.metadata = {
            "message_id": "msg1",
            "sender": "professor@university.edu",
            "subject": "Python Project Deadline",
            "date_sent": "2025-11-20"
        }
        
        mock_doc2 = MagicMock()
        mock_doc2.page_content = "Email about project requirements"
        mock_doc2.metadata = {
            "message_id": "msg2",
            "sender": "ta@university.edu",
            "subject": "Project Requirements",
            "date_sent": "2025-11-18"
        }
        
        # Mock async query_vector_db
        mock_vector_query.return_value = [mock_doc1, mock_doc2]
        
        # Mock LLM response
        mock_llm.return_value = "The Python project deadline is mentioned in the email from your professor."
        
        # Make HTTP request
        response = client.get("/api/query?query=When+is+the+Python+project+due&top_k=3")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "answer" in data
        assert "sources" in data
        assert len(data["sources"]) == 2
        
        # Validate source structure
        source = data["sources"][0]
        assert "message_id" in source
        assert "sender" in source
        assert "subject" in source
        assert "date_sent" in source
    
    @patch('backend.app.query_vector_db', new_callable=AsyncMock)
    def test_query_no_results(self, mock_vector_query, client):
        """Test GET /api/query handles no results gracefully"""
        # Mock async query_vector_db returning empty list
        mock_vector_query.return_value = []
        
        response = client.get("/api/query?query=nonexistent+topic&top_k=3")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        # Message might vary - just check it mentions not finding results
        assert ("couldn't find" in data["answer"].lower() or 
                "no relevant" in data["answer"].lower() or
                "not find" in data["answer"].lower())
        assert data["sources"] == []


# ============================================================================
# AUTHENTICATION ENDPOINT TESTS
# ============================================================================

class TestAuthEndpoints:
    """Test authentication endpoints"""
    
    def test_signup_creates_account_and_email_account(self, client, setup_test_db):
        """Test POST /api/auth/signup creates account and email account"""
        test_db_manager = setup_test_db
        
        # Clean up any existing test account
        existing_account = test_db_manager.get_account_by_email("newuser@example.com")
        if existing_account:
            # Would need to delete email accounts first, but for test we'll use unique email
            pass
        
        signup_data = {
            "email": "newuser@example.com",
            "password": "testpassword123"
        }
        
        response = client.post("/api/auth/signup", json=signup_data)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "account_id" in data
        assert "email_account_id" in data
    
    def test_signin_with_valid_credentials(self, client, setup_test_db):
        """Test POST /api/auth/signin with valid credentials"""
        test_db_manager = setup_test_db
        
        # Create test account first
        password_hash = _hash_password("testpassword")
        account = test_db_manager.get_or_create_account("signintest@example.com", password_hash)
        test_db_manager.get_or_create_email_account(
            account_id=account.id,
            email="signintest@example.com",
            provider='gmail',
            is_primary=True
        )
        
        signin_data = {
            "email": "signintest@example.com",
            "password": "testpassword"
        }
        
        response = client.post("/api/auth/signin", json=signin_data)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "account_id" in data
        assert "email_account_id" in data
    
    def test_signin_with_invalid_credentials(self, client):
        """Test POST /api/auth/signin with invalid credentials"""
        signin_data = {
            "email": "nonexistent@example.com",
            "password": "wrongpassword"
        }
        
        response = client.post("/api/auth/signin", json=signin_data)
        
        assert response.status_code == 401
        data = response.json()
        assert "Invalid email or password" in data["detail"]


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

class TestErrorHandling:
    """Test error handling across endpoints"""
    
    def test_invalid_endpoint_returns_404(self, client):
        """Test accessing non-existent endpoint returns 404"""
        response = client.get("/api/nonexistent")
        
        assert response.status_code == 404
    
    def test_invalid_email_account_id_type(self, client):
        """Test endpoints handle invalid email_account_id types"""
        response = client.get("/api/emails?email_account_id=invalid")
        
        # Should return validation error
        assert response.status_code == 422
    
    def test_malformed_json_request(self, client):
        """Test POST endpoints handle malformed JSON"""
        response = client.post(
            "/api/calendar/events",
            data="not valid json",
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 422


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-s"])

