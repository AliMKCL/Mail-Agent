"""
Tests for calendar CRUD operations.

Tests cover:
- Create calendar events
- Read/list calendar events
- Update calendar events
- Delete calendar events
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import app


class TestCalendarRead:
    """Tests for reading calendar events."""

    def test_get_calendar_events(self, client, test_user, mock_calendar_service):
        """Test retrieving calendar events."""
        service, _ = mock_calendar_service

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            response = client.get(f"/api/calendar/events?user_id={test_user.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "events" in data

    def test_calendar_events_grouped_by_date(self, client, test_user, mock_calendar_service):
        """Test that events are grouped by date."""
        service, _ = mock_calendar_service

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            response = client.get(f"/api/calendar/events?user_id={test_user.id}")

        data = response.json()
        events = data["events"]

        assert isinstance(events, dict)
        for date_key, event_list in events.items():
            assert len(date_key) == 10
            assert date_key[4] == "-" and date_key[7] == "-"
            assert isinstance(event_list, list)

    def test_event_has_required_fields(self, client, test_user, mock_calendar_service):
        """Test that each event has required fields."""
        service, _ = mock_calendar_service

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            response = client.get(f"/api/calendar/events?user_id={test_user.id}")

        data = response.json()
        events = data["events"]
        required_fields = ["id", "title", "time", "start", "end"]

        for date_key, event_list in events.items():
            for event in event_list:
                for field in required_fields:
                    assert field in event, f"Missing field: {field}"


class TestCalendarCreate:
    """Tests for creating calendar events."""

    def test_create_event(self, client, test_user, mock_calendar_service):
        """Test creating a new calendar event."""
        service, events_store = mock_calendar_service

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            event_data = {
                "user_id": test_user.id,
                "event_data": {
                    "title": "New Test Event",
                    "description": "Test description",
                    "date": "2025-11-25",
                    "time": "02:00 PM",
                    "category": "Career"
                }
            }
            response = client.post("/api/calendar/events", json=event_data)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "event_id" in data

    def test_create_event_returns_link(self, client, test_user, mock_calendar_service):
        """Test that creating an event returns a calendar link."""
        service, _ = mock_calendar_service

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            event_data = {
                "user_id": test_user.id,
                "event_data": {
                    "title": "Link Test Event",
                    "date": "2025-11-26",
                    "time": "03:00 PM",
                    "category": "Academic"
                }
            }
            response = client.post("/api/calendar/events", json=event_data)

        data = response.json()
        assert "event_link" in data
        assert "calendar.google.com" in data["event_link"]

    def test_create_all_day_event(self, client, test_user, mock_calendar_service):
        """Test creating an all-day event."""
        service, _ = mock_calendar_service

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            event_data = {
                "user_id": test_user.id,
                "event_data": {
                    "title": "All Day Event",
                    "date": "2025-11-27",
                    "time": "All Day",
                    "category": "Social"
                }
            }
            response = client.post("/api/calendar/events", json=event_data)

        assert response.status_code == 200
        assert response.json()["status"] == "success"


class TestCalendarUpdate:
    """Tests for updating calendar events."""

    def test_update_event_title(self, client, test_user, mock_calendar_service):
        """Test updating an event's title."""
        service, events_store = mock_calendar_service

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            update_data = {
                "user_id": test_user.id,
                "event_data": {
                    "title": "Updated Event Title",
                    "date": "2025-11-23",
                    "time": "10:00 AM"
                }
            }
            response = client.put("/api/calendar/events/event_001", json=update_data)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_update_event_time(self, client, test_user, mock_calendar_service):
        """Test updating an event's time."""
        service, _ = mock_calendar_service

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            update_data = {
                "user_id": test_user.id,
                "event_data": {
                    "title": "Existing Event",
                    "date": "2025-11-23",
                    "time": "04:00 PM"
                }
            }
            response = client.put("/api/calendar/events/event_001", json=update_data)

        assert response.status_code == 200
        assert response.json()["status"] == "success"


class TestCalendarDelete:
    """Tests for deleting calendar events."""

    def test_delete_event(self, client, test_user, mock_calendar_service):
        """Test deleting a calendar event."""
        service, events_store = mock_calendar_service

        assert "event_001" in events_store

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            response = client.delete(f"/api/calendar/events/event_001?user_id={test_user.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "event_001" not in events_store

    def test_delete_returns_success_message(self, client, test_user, mock_calendar_service):
        """Test that delete returns a success message."""
        service, _ = mock_calendar_service

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            response = client.delete(f"/api/calendar/events/event_001?user_id={test_user.id}")

        data = response.json()
        assert "message" in data


class TestCalendarStatus:
    """Tests for calendar status endpoint."""

    def test_calendar_status_success(self, client, mock_calendar_service):
        """Test calendar status when service is available."""
        service, _ = mock_calendar_service

        with patch('backend.app.get_calendar_service', return_value=(service, None)):
            response = client.get("/api/calendar/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_calendar_status_error(self, client):
        """Test calendar status when service is unavailable."""
        with patch('backend.app.get_calendar_service', return_value=(None, "Auth required")):
            response = client.get("/api/calendar/status")

        data = response.json()
        assert data["status"] == "error"
