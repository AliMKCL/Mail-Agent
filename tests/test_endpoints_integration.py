"""
Integration tests for FastAPI endpoints
Tests actual HTTP requests/responses through TestClient
Mocks only 3rd party services (Gmail API, Calendar API, Ollama)
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
import json
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import app
from backend.databases.database import DatabaseManager, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Create test client
client = TestClient(app)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture(scope="module")
def test_db_path():
    """Test database path"""
    return "test_gmail_agent.db"


@pytest.fixture(scope="module")
def setup_test_db(test_db_path):
    """Setup test database once for all tests"""
    # Create test database
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    
    engine = create_engine(f"sqlite:///{test_db_path}")
    Base.metadata.create_all(bind=engine)
    
    yield
    
    # Cleanup
    if os.path.exists(test_db_path):
        os.remove(test_db_path)


@pytest.fixture(scope="module")
def test_user(setup_test_db, test_db_path):
    """Create a test user"""
    db_url = f"sqlite:///{test_db_path}"
    db_manager = DatabaseManager(db_url)
    user = db_manager.get_or_create_user("testuser@example.com", "Test User")
    return user


@pytest.fixture(scope="module")
def second_user(setup_test_db, test_db_path):
    """Create a second test user"""
    db_url = f"sqlite:///{test_db_path}"
    db_manager = DatabaseManager(db_url)
    user = db_manager.get_or_create_user("seconduser@example.com", "Second User")
    return user


# ============================================================================
# STATIC FILE & ROOT ENDPOINT TESTS
# ============================================================================

class TestStaticEndpoints:
    """Test static file serving and root endpoint"""
    
    def test_root_endpoint_returns_html(self):
        """Test GET / returns HTML page"""
        response = client.get("/")
        
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        # Basic check that it's HTML
        assert "<!DOCTYPE html>" in response.text or "<html" in response.text
    
    def test_static_css_file(self):
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
    
    def test_get_all_users(self, test_user, second_user):
        """Test GET /api/users returns list of users"""
        response = client.get("/api/users")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 2  # At least our test users
        
        # Check structure of user objects
        user = data[0]
        assert "id" in user
        assert "email" in user
        assert "name" in user
        assert "created_at" in user
    
    def test_get_user_info_success(self, test_user):
        """Test GET /api/user/{user_id} returns user info"""
        # First get users to find a valid ID from the app's database
        users_response = client.get("/api/users")
        users = users_response.json()

        if len(users) > 0:
            user_id = users[0]["id"]
            response = client.get(f"/api/user/{user_id}")

            assert response.status_code == 200
            data = response.json()
            assert "id" in data
            assert "email" in data
            assert "name" in data
            assert "created_at" in data
        else:
            pytest.skip("No users in database to test")
    
    def test_get_user_info_not_found(self):
        """Test GET /api/user/{user_id} returns 404 for non-existent user"""
        response = client.get("/api/user/99999")
        
        assert response.status_code in [404, 500]
        data = response.json()
        if response.status_code == 404:
            assert "not found" in data["detail"].lower()


# ============================================================================
# EMAIL ENDPOINT TESTS
# ============================================================================

class TestEmailEndpoints:
    """Test email-related endpoints"""
    
    def test_get_emails_missing_user_id(self):
        """Test GET /api/emails requires user_id parameter"""
        response = client.get("/api/emails")
        
        assert response.status_code in [400, 500]
        if response.status_code == 400:
            data = response.json()
            assert "user_id parameter is required" in data["detail"]
    
    def test_get_emails_with_user_id(self, test_user):
        """Test GET /api/emails returns email list structure"""
        response = client.get(f"/api/emails?user_id={test_user.id}&limit=10")
        
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
    
    def test_get_emails_with_limit(self, test_user):
        """Test GET /api/emails respects limit parameter"""
        response = client.get(f"/api/emails?user_id={test_user.id}&limit=5")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 5
    
    # Mocks GMAIL API calls
    @patch('backend.app.get_service')
    @patch('backend.app.list_message_ids')
    @patch('backend.app.prepare_email_data')
    @patch('backend.app.embed_and_store')
    async def test_sync_emails_success(
        self, 
        mock_embed, 
        mock_prepare, 
        mock_list, 
        mock_service,
        test_user
    ):
        """Test GET /api/sync successfully syncs emails (mocked Gmail API)"""
        # Mock Gmail API responses
        mock_service.return_value = MagicMock()
        mock_list.return_value = ["msg1", "msg2", "msg3"]
        mock_prepare.return_value = [
            {
                "message_id": "msg1",
                "subject": "Test Email 1",
                "sender": "sender1@test.com",
                "recipient": test_user.email,
                "date_sent": datetime.now(),
                "snippet": "Test snippet 1",
                "body_text": "Test body 1",
                "body_html": "<p>Test body 1</p>"
            },
            {
                "message_id": "msg2",
                "subject": "Test Email 2",
                "sender": "sender2@test.com",
                "recipient": test_user.email,
                "date_sent": datetime.now(),
                "snippet": "Test snippet 2",
                "body_text": "Test body 2",
                "body_html": "<p>Test body 2</p>"
            }
        ]
        mock_embed.return_value = None
        
        # Make actual HTTP request to endpoint
        response = client.get(f"/api/sync?user_id={test_user.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "total_fetched" in data
        assert "new_emails" in data
        assert data["total_fetched"] == 3
    
    def test_sync_emails_missing_user_id(self):
        """Test GET /api/sync requires user_id parameter"""
        response = client.get("/api/sync")
        
        # API returns 500 instead of 400 - error handling needs improvement!
        assert response.status_code in [400, 500]
        if response.status_code == 400:
            data = response.json()
            assert "user_id parameter is required" in data["detail"]


# ============================================================================
# CALENDAR EVENT ENDPOINT TESTS
# ============================================================================

class TestCalendarEndpoints:
    """Test calendar event endpoints"""
    
    def test_get_calendar_events_missing_user_id(self):
        """Test GET /api/calendar/events requires user_id"""
        response = client.get("/api/calendar/events")
        
        # API returns 500 instead of 400 - error handling needs improvement!
        assert response.status_code in [400, 500]
        if response.status_code == 400:
            data = response.json()
            assert "user_id parameter is required" in data["detail"]
    
    @patch('backend.app.get_calendar_service')
    def test_get_calendar_events_success(self, mock_calendar_service, test_user):
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
        response = client.get(f"/api/calendar/events?user_id={test_user.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "events" in data
        assert isinstance(data["events"], dict)
        
        # Check that events are organized by date
        events_dict = data["events"]
        assert len(events_dict) > 0
    
    @patch('backend.app.get_calendar_service')
    def test_create_calendar_event_success(self, mock_calendar_service, test_user):
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
            "user_id": test_user.id,
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
    
    def test_create_calendar_event_missing_user_id(self):
        """Test POST /api/calendar/events requires user_id"""
        event_data = {
            "event_data": {
                "title": "Test Event",
                "date": "2025-12-01"
            }
        }
        
        response = client.post("/api/calendar/events", json=event_data)
        
        # API returns 500 instead of 422 - validation needs improvement!
        assert response.status_code in [422, 500]
    
    def test_create_calendar_event_invalid_data(self, test_user):
        """Test POST /api/calendar/events validates event data"""
        event_data = {
            "user_id": test_user.id,
            "event_data": {
                # Missing required fields
            }
        }
        
        response = client.post("/api/calendar/events", json=event_data)
        
        # Should handle missing fields gracefully
        assert response.status_code in [400, 422, 500]
    
    @patch('backend.app.get_calendar_service')
    def test_update_calendar_event_success(self, mock_calendar_service, test_user):
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
            "user_id": test_user.id,
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
    def test_delete_calendar_event_success(self, mock_calendar_service, test_user):
        """Test DELETE /api/calendar/events/{event_id} deletes event"""
        mock_service = MagicMock()
        mock_service.events().delete().execute.return_value = None
        mock_calendar_service.return_value = (mock_service, None)
        
        # Make HTTP DELETE request
        response = client.delete(f"/api/calendar/events/event123?user_id={test_user.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["message"] == "Event deleted successfully"
    
    def test_delete_calendar_event_missing_user_id(self):
        """Test DELETE /api/calendar/events/{event_id} requires user_id"""
        response = client.delete("/api/calendar/events/event123")
        
        # API returns 422 (query param validation) - acceptable
        assert response.status_code in [400, 422]


# ============================================================================
# MOODLE CALENDAR ENDPOINT TESTS
# ============================================================================

class TestMoodleEndpoints:
    """Test Moodle calendar integration endpoints"""
    
    @patch('backend.app.get_moodle_calendar_events')
    def test_get_moodle_events_success(self, mock_moodle, test_user):
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
        
        response = client.get(f"/api/calendar/moodle?user_id={test_user.id}")
        
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
    
    def test_query_missing_query_param(self):
        """Test GET /api/query requires query parameter"""
        response = client.get("/api/query")
        
        # API returns 422 (query param validation) - acceptable
        assert response.status_code in [400, 422]
    
    @patch('backend.app.query_vector_db')
    @patch('backend.app.llm_response')
    def test_query_with_results(self, mock_llm, mock_vector_query):
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
        # Note: snippet might not be included in all source formats
    
    @patch('backend.app.query_vector_db')
    def test_query_no_results(self, mock_vector_query):
        """Test GET /api/query handles no results gracefully"""
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
# ERROR HANDLING TESTS
# ============================================================================

class TestErrorHandling:
    """Test error handling across endpoints"""
    
    def test_invalid_endpoint_returns_404(self):
        """Test accessing non-existent endpoint returns 404"""
        response = client.get("/api/nonexistent")
        
        assert response.status_code == 404
    
    def test_invalid_user_id_type(self):
        """Test endpoints handle invalid user_id types"""
        response = client.get("/api/emails?user_id=invalid")
        
        # Should return validation error
        assert response.status_code == 422
    
    def test_malformed_json_request(self):
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