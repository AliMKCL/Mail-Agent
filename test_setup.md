# Test Setup for Mail Agent

## Overview

The test suite uses **pytest** with mocked services to test the application without requiring real Google API credentials or external services.

## Running Tests

```bash
# Activate virtual environment
source .mail_venv/bin/activate

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_emails.py -v

# Run specific test class
pytest tests/test_calendar.py::TestCalendarCreate -v

# Run single test
pytest tests/test_emails.py::TestEmailIsolation::test_user_sees_only_own_emails -v
```

## Test Structure

```
tests/
├── conftest.py      # Shared fixtures (database, mocks, test users)
├── test_emails.py   # Email retrieval and user isolation tests
├── test_calendar.py # Calendar CRUD operation tests
└── test_search.py   # Vector DB and keyword search tests
```

## Configuration

`pytest.ini` defines test discovery settings:
- Test files: `test_*.py`
- Test classes: `Test*`
- Test functions: `test_*`
- Async mode: auto (for async endpoint testing)

## Key Fixtures (conftest.py)

### Database Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `test_db` | function | Fresh SQLite database for each test |
| `client` | function | FastAPI TestClient with test database |
| `test_user` | function | Creates a test user |
| `second_user` | function | Creates a second test user |
| `user_with_emails` | function | Test user with 2 sample emails |
| `second_user_with_emails` | function | Second user with 1 sample email |

### Mock Fixtures

| Fixture | Description |
|---------|-------------|
| `mock_calendar_service` | Mocked Google Calendar API with in-memory event storage |
| `mock_vector_db` | Mocked vector database query function |
| `mock_llm` | Mocked LLM response function |

## How Database Testing Works

### The Problem with In-Memory SQLite

SQLite in-memory databases (`:memory:`) don't share across connections. Each connection gets an isolated database, which breaks tests when the test setup and the application use different connections.

### The Solution

Tests use **temporary file-based SQLite** databases:

```python
@pytest.fixture(scope="function")
def test_db(tmp_path):
    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    # ... setup code
```

This ensures:
1. All connections within a test share the same database
2. Each test gets a fresh database (function scope)
3. Cleanup is automatic (pytest's `tmp_path` handles deletion)

### Database Patching

The fixture patches `app_module.db_manager` to use the test database:

```python
app_module.db_manager.engine = test_engine
app_module.db_manager.SessionLocal = TestSessionLocal
```

## How Calendar Testing Works

Calendar tests mock the Google Calendar API service:

```python
@pytest.fixture
def mock_calendar_service():
    service = MagicMock()
    events_store = {...}  # In-memory event storage

    # Mock CRUD operations
    service.events().list = list_events
    service.events().insert = insert_event
    service.events().get = get_event
    service.events().update = update_event
    service.events().delete = delete_event

    return service, events_store
```

Tests patch the service at the endpoint level:

```python
with patch('backend.app.get_calendar_service', return_value=(service, None)):
    response = client.get("/api/calendar/events?user_id=1")
```

## Test Coverage

### Email Tests (test_emails.py)

| Test Class | Coverage |
|------------|----------|
| `TestEmailRetrieval` | Get emails, full body, HTML body, required fields |
| `TestEmailIsolation` | User sees only own emails, no cross-user access |
| `TestEmailLimits` | Pagination/limit parameter |

### Calendar Tests (test_calendar.py)

| Test Class | Coverage |
|------------|----------|
| `TestCalendarRead` | Get events, grouped by date, required fields |
| `TestCalendarCreate` | Create event, returns link, all-day events |
| `TestCalendarUpdate` | Update title, update time |
| `TestCalendarDelete` | Delete event, success message |
| `TestCalendarStatus` | Service available/unavailable states |

### Search Tests (test_search.py)

| Test Class | Coverage |
|------------|----------|
| `TestVectorDatabaseSearch` | Query response, sources, metadata, top_k, empty query, no results |
| `TestKeywordSearch` | Subject filtering, case insensitivity, partial match, sorting |

## Adding New Tests

### Adding a New Test File

1. Create `tests/test_<feature>.py`
2. Import required fixtures from conftest.py
3. Use `client` fixture for API testing

```python
class TestNewFeature:
    def test_something(self, client, test_user):
        response = client.get(f"/api/endpoint?user_id={test_user.id}")
        assert response.status_code == 200
```

### Adding a New Fixture

Add to `conftest.py`:

```python
@pytest.fixture
def my_fixture(test_db):
    # Setup
    yield something
    # Teardown (optional)
```

### Mocking External Services

Use `unittest.mock.patch` to mock external dependencies:

```python
from unittest.mock import patch

def test_with_mock(self, client):
    with patch('backend.app.external_service', return_value=mock_data):
        response = client.get("/api/endpoint")
```

## Dependencies

Required packages (in requirements.txt):
- `pytest>=7.4.0`
- `pytest-asyncio>=0.21.0`
- `httpx>=0.24.0` (required by FastAPI TestClient)
