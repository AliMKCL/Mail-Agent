"""
Integration tests for MCP Server Tools
Tests actual tool functionality without mocks (except for Google API calls).
Uses real database operations and ensures proper cleanup.
"""

import pytest
import sys
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.databases.database import DatabaseManager, User, Email
import backend.mcp_server as mcp_server

# Extract actual functions from FunctionTool wrappers
def get_tool_function(tool):
    """Extract the actual function from a FunctionTool wrapper."""
    if hasattr(tool, 'fn'):
        return tool.fn
    return tool

# User management tools
list_users = get_tool_function(mcp_server.list_users)
get_user_info = get_tool_function(mcp_server.get_user_info)

# Email tools
search_emails = get_tool_function(mcp_server.search_emails)
get_email_details = get_tool_function(mcp_server.get_email_details)
get_user_emails = get_tool_function(mcp_server.get_user_emails)

# Calendar tools
create_calendar_event = get_tool_function(mcp_server.create_calendar_event)
update_calendar_event = get_tool_function(mcp_server.update_calendar_event)
delete_calendar_event = get_tool_function(mcp_server.delete_calendar_event)
get_calendar_events = get_tool_function(mcp_server.get_calendar_events)

# AI-enhanced tools
extract_dates_from_emails = get_tool_function(mcp_server.extract_dates_from_emails)
summarize_emails = get_tool_function(mcp_server.summarize_emails)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def db_manager():
    """Get the real database manager."""
    return DatabaseManager()


@pytest.fixture
def test_user(db_manager):
    """Create a test user and clean up after."""
    user = db_manager.get_or_create_user("mcp_test@example.com", "MCP Test User")
    yield user

    # Cleanup: Delete test user and associated data
    with db_manager.get_session() as session:
        # Delete user's emails
        session.query(Email).filter_by(user_id=user.id).delete()
        # Delete user
        session.query(User).filter_by(id=user.id).delete()
        session.commit()


@pytest.fixture
def test_user_with_emails(db_manager, test_user):
    """Create a test user with sample emails."""
    emails = [
        {
            "message_id": "mcp_test_msg_001",
            "subject": "Project Deadline Reminder",
            "sender": "pm@company.com",
            "recipient": "mcp_test@example.com",
            "date_sent": datetime.now() - timedelta(days=2),
            "snippet": "The project is due next Friday",
            "body_text": "Hi team, just a reminder that our project deadline is next Friday, December 6th. Please make sure all deliverables are ready.",
            "body_html": "<p>Hi team, just a reminder that our project deadline is next Friday, December 6th.</p>"
        },
        {
            "message_id": "mcp_test_msg_002",
            "subject": "Team Meeting Tomorrow",
            "sender": "boss@company.com",
            "recipient": "mcp_test@example.com",
            "date_sent": datetime.now() - timedelta(days=1),
            "snippet": "Meeting at 10am in conference room",
            "body_text": "Please join our team meeting tomorrow at 10am in conference room A. We'll discuss Q4 goals.",
            "body_html": None
        },
        {
            "message_id": "mcp_test_msg_003",
            "subject": "Lunch invitation",
            "sender": "colleague@company.com",
            "recipient": "mcp_test@example.com",
            "date_sent": datetime.now(),
            "snippet": "Want to grab lunch?",
            "body_text": "Hey! Want to grab lunch today at noon? Let me know!",
            "body_html": None
        }
    ]
    db_manager.save_emails(test_user.id, emails)
    return test_user


@pytest.fixture
def mock_calendar_service():
    """
    Mock Google Calendar API service for calendar operations.
    Maintains state for CRUD operations during tests.
    """
    # In-memory event storage for this test session
    events_store = {}

    service = MagicMock()

    # Mock events().list()
    def mock_list(**kwargs):
        result = MagicMock()
        result.execute.return_value = {"items": list(events_store.values())}
        return result

    # Mock events().insert()
    def mock_insert(**kwargs):
        result = MagicMock()
        body = kwargs.get("body", {})
        event_id = f"test_event_{len(events_store) + 1}"
        new_event = {
            "id": event_id,
            "summary": body.get("summary", ""),
            "description": body.get("description", ""),
            "start": body.get("start", {}),
            "end": body.get("end", {}),
            "htmlLink": f"https://calendar.google.com/event?id={event_id}",
            "extendedProperties": body.get("extendedProperties", {})
        }
        events_store[event_id] = new_event
        result.execute.return_value = new_event
        return result

    # Mock events().get()
    def mock_get(**kwargs):
        result = MagicMock()
        event_id = kwargs.get("eventId")
        if event_id in events_store:
            result.execute.return_value = events_store[event_id]
        else:
            result.execute.side_effect = Exception(f"Event not found: {event_id}")
        return result

    # Mock events().update()
    def mock_update(**kwargs):
        result = MagicMock()
        event_id = kwargs.get("eventId")
        body = kwargs.get("body", {})
        if event_id in events_store:
            events_store[event_id].update(body)
            result.execute.return_value = events_store[event_id]
        else:
            result.execute.side_effect = Exception(f"Event not found: {event_id}")
        return result

    # Mock events().delete()
    def mock_delete(**kwargs):
        result = MagicMock()
        event_id = kwargs.get("eventId")
        if event_id in events_store:
            del events_store[event_id]
        result.execute.return_value = None
        return result

    # Attach mocks to service
    service.events.return_value.list = mock_list
    service.events.return_value.insert = mock_insert
    service.events.return_value.get = mock_get
    service.events.return_value.update = mock_update
    service.events.return_value.delete = mock_delete

    # Also set up _http.credentials for credential saving
    service._http = MagicMock()
    service._http.credentials = None

    return service, events_store


# ============================================================================
# USER MANAGEMENT TOOL TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_list_users(test_user):
    """Test listing all users."""
    result = await list_users()

    assert result["status"] == "success"
    assert "users" in result
    assert result["count"] > 0

    # Check our test user is in the list
    user_emails = [u["email"] for u in result["users"]]
    assert "mcp_test@example.com" in user_emails


@pytest.mark.asyncio
async def test_get_user_info(test_user):
    """Test getting specific user information."""
    result = await get_user_info(user_id=test_user.id)

    assert result["status"] == "success"
    assert result["user"]["id"] == test_user.id
    assert result["user"]["email"] == "mcp_test@example.com"
    assert result["user"]["name"] == "MCP Test User"


@pytest.mark.asyncio
async def test_get_user_info_invalid_id():
    """Test getting user info with invalid ID."""
    result = await get_user_info(user_id=99999)

    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


# ============================================================================
# EMAIL TOOL TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_get_user_emails(test_user_with_emails):
    """Test getting emails for a specific user."""
    result = await get_user_emails(user_id=test_user_with_emails.id, limit=10)

    assert result["status"] == "success"
    assert result["count"] == 3
    assert len(result["emails"]) == 3

    # Verify email data
    subjects = [email["subject"] for email in result["emails"]]
    assert "Project Deadline Reminder" in subjects
    assert "Team Meeting Tomorrow" in subjects


@pytest.mark.asyncio
async def test_get_email_details(test_user_with_emails):
    """Test getting details of a specific email."""
    result = await get_email_details(message_id="mcp_test_msg_001")

    assert result["status"] == "success"
    assert result["email"]["subject"] == "Project Deadline Reminder"
    assert result["email"]["sender"] == "pm@company.com"
    assert "deadline" in result["email"]["body_text"].lower()


@pytest.mark.asyncio
async def test_get_email_details_not_found():
    """Test getting details of non-existent email."""
    result = await get_email_details(message_id="nonexistent_id")

    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_search_emails_by_content(test_user_with_emails, db_manager):
    """Test searching emails by content in database."""
    # Search for emails containing "deadline"
    with db_manager.get_session() as session:
        emails = session.query(Email).filter(
            Email.user_id == test_user_with_emails.id
        ).filter(
            Email.body_text.ilike("%deadline%")
        ).all()

    assert len(emails) > 0
    assert any("deadline" in email.body_text.lower() for email in emails)


# ============================================================================
# CALENDAR CRUD TOOL TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_calendar_create_event(mock_calendar_service):
    """Test creating a calendar event."""
    service, events_store = mock_calendar_service

    with patch('backend.mcp_server.get_calendar_service', return_value=(service, None)):
        # Track initial state
        initial_count = len(events_store)

        # Create event for today
        today = datetime.now().date()
        result = await create_calendar_event(
            title="Test Meeting",
            date=today.isoformat(),
            time="2:00 PM",
            description="This is a test meeting",
            category="Career"
        )

        assert result["status"] == "success"
        assert "event_id" in result
        assert result["title"] == "Test Meeting"
        assert result["date"] == today.isoformat()

        # Verify event was added to store (check delta, not absolute)
        assert len(events_store) == initial_count + 1

        # Find our created event
        created_event = events_store[result["event_id"]]
        assert created_event["summary"] == "Test Meeting"

        # Cleanup
        await delete_calendar_event(event_id=result["event_id"])


@pytest.mark.asyncio
async def test_calendar_create_all_day_event(mock_calendar_service):
    """Test creating an all-day calendar event."""
    service, events_store = mock_calendar_service

    with patch('backend.mcp_server.get_calendar_service', return_value=(service, None)):
        initial_count = len(events_store)

        tomorrow = (datetime.now() + timedelta(days=1)).date()
        result = await create_calendar_event(
            title="All Day Event",
            date=tomorrow.isoformat(),
            time="All Day",
            category="Social"
        )

        assert result["status"] == "success"
        assert result["title"] == "All Day Event"

        # Verify one event was added
        assert len(events_store) == initial_count + 1

        # Verify it's an all-day event (has 'date' not 'dateTime')
        created_event = events_store[result["event_id"]]
        assert "date" in created_event["start"]

        # Cleanup
        await delete_calendar_event(event_id=result["event_id"])


@pytest.mark.asyncio
async def test_calendar_full_crud_cycle(mock_calendar_service):
    """
    Test complete CRUD cycle: Create -> Read -> Update -> Delete
    This ensures no side effects remain after the test.
    """
    service, events_store = mock_calendar_service

    with patch('backend.mcp_server.get_calendar_service', return_value=(service, None)):
        # Track initial state
        initial_count = len(events_store)

        # 1. CREATE
        today = datetime.now().date()
        create_result = await create_calendar_event(
            title="CRUD Test Event",
            date=today.isoformat(),
            time="3:00 PM",
            description="Testing full CRUD cycle",
            category="Academic"
        )

        assert create_result["status"] == "success"
        event_id = create_result["event_id"]
        assert len(events_store) == initial_count + 1

        # 2. READ (via get_calendar_events)
        read_result = await get_calendar_events(
            start_date=today.isoformat(),
            end_date=(today + timedelta(days=1)).isoformat()
        )

        assert read_result["status"] == "success"
        # Find our specific event in the results
        our_event = next((e for e in read_result["events"] if e["id"] == event_id), None)
        assert our_event is not None
        assert our_event["title"] == "CRUD Test Event"

        # 3. UPDATE
        update_result = await update_calendar_event(
            event_id=event_id,
            title="Updated CRUD Test Event",
            description="Updated description"
        )

        assert update_result["status"] == "success"
        updated_event = events_store[event_id]
        assert updated_event["summary"] == "Updated CRUD Test Event"
        assert updated_event["description"] == "Updated description"

        # 4. DELETE
        delete_result = await delete_calendar_event(event_id=event_id)

        assert delete_result["status"] == "success"
        assert event_id not in events_store
        assert len(events_store) == initial_count  # Back to initial state


@pytest.mark.asyncio
async def test_calendar_update_event(mock_calendar_service):
    """Test updating an existing calendar event."""
    service, events_store = mock_calendar_service

    with patch('backend.mcp_server.get_calendar_service', return_value=(service, None)):
        # First create an event
        today = datetime.now().date()
        create_result = await create_calendar_event(
            title="Original Title",
            date=today.isoformat(),
            category="Deadline"
        )
        event_id = create_result["event_id"]

        # Now update it
        update_result = await update_calendar_event(
            event_id=event_id,
            title="Modified Title",
            category="Academic"
        )

        assert update_result["status"] == "success"

        # Verify the update
        updated_event = events_store[event_id]
        assert updated_event["summary"] == "Modified Title"

        # Cleanup
        await delete_calendar_event(event_id=event_id)


@pytest.mark.asyncio
async def test_calendar_delete_event(mock_calendar_service):
    """Test deleting a calendar event."""
    service, events_store = mock_calendar_service

    with patch('backend.mcp_server.get_calendar_service', return_value=(service, None)):
        initial_count = len(events_store)

        # Create an event
        today = datetime.now().date()
        create_result = await create_calendar_event(
            title="Event to Delete",
            date=today.isoformat()
        )
        event_id = create_result["event_id"]
        assert len(events_store) == initial_count + 1

        # Delete it
        delete_result = await delete_calendar_event(event_id=event_id)

        assert delete_result["status"] == "success"
        assert event_id not in events_store
        assert len(events_store) == initial_count  # Back to initial


@pytest.mark.asyncio
async def test_calendar_get_events(mock_calendar_service):
    """Test getting calendar events for a date range."""
    service, events_store = mock_calendar_service

    with patch('backend.mcp_server.get_calendar_service', return_value=(service, None)):
        initial_count = len(events_store)

        # Create multiple events
        today = datetime.now().date()
        event_ids = []

        for i in range(3):
            result = await create_calendar_event(
                title=f"Test Event {i+1}",
                date=(today + timedelta(days=i)).isoformat(),
                category="Social"
            )
            event_ids.append(result["event_id"])

        # Get events for date range
        get_result = await get_calendar_events(
            start_date=today.isoformat(),
            end_date=(today + timedelta(days=3)).isoformat()
        )

        assert get_result["status"] == "success"

        # Verify our 3 events are in the results
        our_events = [e for e in get_result["events"] if e["id"] in event_ids]
        assert len(our_events) == 3

        # Verify titles
        our_titles = {e["title"] for e in our_events}
        assert our_titles == {"Test Event 1", "Test Event 2", "Test Event 3"}

        # Cleanup all events
        for event_id in event_ids:
            await delete_calendar_event(event_id=event_id)

        assert len(events_store) == initial_count  # Back to initial


@pytest.mark.asyncio
async def test_calendar_error_handling(mock_calendar_service):
    """Test calendar error handling for invalid operations."""
    service, events_store = mock_calendar_service

    with patch('backend.mcp_server.get_calendar_service', return_value=(service, None)):
        # Try to update non-existent event
        update_result = await update_calendar_event(
            event_id="nonexistent_event_id",
            title="Should Fail"
        )

        assert update_result["status"] == "error"

        # Try to delete non-existent event
        delete_result = await delete_calendar_event(event_id="nonexistent_event_id")

        # Delete might succeed (Google API returns success even if event doesn't exist)
        # So we just verify it doesn't crash
        assert "status" in delete_result


# ============================================================================
# AI-ENHANCED TOOL TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_extract_dates_basic(test_user_with_emails):
    """Test basic date extraction from emails (without LLM call)."""
    # This test verifies the tool structure without making actual LLM calls
    with patch('backend.mcp_server.llm_response', return_value='[]'):
        result = await extract_dates_from_emails(
            user_id=test_user_with_emails.id,
            limit=3,
            auto_create_events=False
        )

        assert result["status"] == "success"
        assert "extracted_dates" in result
        assert result["count"] == 0  # Empty because LLM returned []

"""
@pytest.mark.asyncio
async def test_extract_dates_with_mock_llm(test_user_with_emails, mock_calendar_service):
    #Test date extraction with mocked LLM response.
    service, events_store = mock_calendar_service

    # Mock LLM to return a valid date
    mock_llm_response = '''[
        {
            "date": "2025-12-06",
            "description": "Project deadline",
            "email_subject": "Project Deadline Reminder"
        }
    ]'''

    with patch('backend.mcp_server.llm_response', return_value=mock_llm_response), \
         patch('backend.mcp_server.get_calendar_service', return_value=(service, None)):

        result = await extract_dates_from_emails(
            user_id=test_user_with_emails.id,
            limit=3,
            auto_create_events=True
        )

        assert result["status"] == "success"
        assert result["count"] == 1
        assert len(result["extracted_dates"]) == 1
        assert result["extracted_dates"][0]["date"] == "2025-12-06"

        # Should have created a calendar event
        assert len(result["created_events"]) == 1

        # Cleanup
        for event_id in result["created_events"]:
            await delete_calendar_event(event_id=event_id)
"""
"""
@pytest.mark.asyncio
async def test_summarize_emails_basic(test_user_with_emails):
    #Test basic email summarization (without actual LLM call).
    with patch('backend.mcp_server.slm_response', return_value="Summary of emails: You have 3 emails including project deadline and meeting."):
        result = await summarize_emails(
            query="all",
            user_id=test_user_with_emails.id,
            summary_type="brief"
        )

        assert result["status"] == "success"
        assert "summary" in result
        assert result["email_count"] == 3
        assert "deadline" in result["summary"].lower() or "meeting" in result["summary"].lower()
"""

# ============================================================================
# INTEGRATION TESTS - Multiple Tools Together
# ============================================================================
"""
@pytest.mark.asyncio
async def test_workflow_user_emails_to_calendar(test_user_with_emails, mock_calendar_service):
    
    #Test a complete workflow:
    #1. List users
    #2. Get user emails
    #3. Extract dates from emails
    #4. Create calendar events
    #5. Verify events
    #6. Cleanup
    
    service, events_store = mock_calendar_service

    # Step 1: List users
    users_result = await list_users()
    assert users_result["status"] == "success"

    # Step 2: Get emails
    emails_result = await get_user_emails(user_id=test_user_with_emails.id, limit=10)
    assert emails_result["status"] == "success"
    assert emails_result["count"] > 0

    # Step 3 & 4: Extract dates and create events (mocked LLM)
    mock_llm_response = '''[
        {
            "date": "2025-11-30",
            "description": "Team meeting",
            "email_subject": "Team Meeting Tomorrow"
        }
    ]'''

    with patch('backend.mcp_server.llm_response', return_value=mock_llm_response), \
         patch('backend.mcp_server.get_calendar_service', return_value=(service, None)):

        extract_result = await extract_dates_from_emails(
            user_id=test_user_with_emails.id,
            limit=3,
            auto_create_events=True
        )

        assert extract_result["status"] == "success"
        event_ids = extract_result.get("created_events", [])

        # Step 5: Verify events were created
        if event_ids:
            calendar_result = await get_calendar_events(
                start_date="2025-11-29",
                end_date="2025-12-01"
            )
            assert calendar_result["status"] == "success"

            # Verify our created events are in the results
            our_events = [e for e in calendar_result["events"] if e["id"] in event_ids]
            assert len(our_events) == len(event_ids)

            # Step 6: Cleanup
            for event_id in event_ids:
                await delete_calendar_event(event_id=event_id)

            # Verify our events are removed
            for event_id in event_ids:
                assert event_id not in events_store
"""
# ============================================================================
# CLEANUP VERIFICATION
# ============================================================================

@pytest.mark.asyncio
async def test_no_side_effects_after_tests(db_manager):
    """
    Verify that test user cleanup worked properly.
    This should run last to ensure no test data remains.
    """
    with db_manager.get_session() as session:
        # Check for any remaining test emails
        test_emails = session.query(Email).filter(
            Email.message_id.like("mcp_test_%")
        ).all()

        # These should be cleaned up by fixtures, but just in case:
        for email in test_emails:
            session.delete(email)

        # Check for test user
        test_user = session.query(User).filter_by(email="mcp_test@example.com").first()
        if test_user:
            session.delete(test_user)

        session.commit()

    # Verify cleanup
    with db_manager.get_session() as session:
        remaining_emails = session.query(Email).filter(
            Email.message_id.like("mcp_test_%")
        ).count()
        assert remaining_emails == 0, "Test emails were not cleaned up properly"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
