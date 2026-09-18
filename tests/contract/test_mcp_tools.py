"""
Contract tests for the MCP service's 14 tools and 9 resources (agent M1, Spec 6.7).

**Why this file exists — hazard B4.**

The suite it replaces (``tests/test_mcp_tools.py``) built its stack like this::

    DatabaseManager()          # no url -> settings.DATABASE_URL -> sqlite:///gmail_agent.db

That is the **real 4.5 MB production database**. The old suite therefore created
accounts, email accounts and emails in real user data on every run and relied on
a hand-written teardown block to delete them again. This file builds the stack
like this instead::

    DatabaseManager(f"sqlite:///{tmp_path / 'test_gmail_agent.db'}")

Same constructor, opposite blast radius. ``test_the_tool_stack_never_touches_the_real_database``
md5s the real file before and after a sync that inserts rows, so the difference
is asserted rather than hoped for. **Nobody may "tidy" the ``manager`` fixture
back into a bare ``DatabaseManager()``** — that reintroduces the hazard this
file was written to retire.

How the stack is wired, and why:

* The MCP tool and resource bodies are plain ``async def``s called in-process
  (Spec 3.6), so there is no app to hang ``dependency_overrides`` on. They
  resolve their downstreams through ``backend.services.mcp.clients`` at call
  time, so the two provider functions there are monkeypatched to return
  ``AsyncServiceClient``s whose transports wrap the **real** Accounts and
  User_data ASGI apps.
* Those two apps in turn get ``AsyncServiceClient``s wrapping the **real**
  Database app (bound to a ``tmp_path`` SQLite file through
  ``database_app.dependency_overrides[get_db_manager]`` — the sanctioned seam,
  R1) and the **real** Vector DB app.
* ``store.collection`` and ``store.embeddings`` are monkeypatched, so nothing
  reaches Ollama and no handle is opened on the real Chroma directory.
* ``gmail.get_service`` and ``google_calendar.get_calendar_service`` are patched
  by name — the single binding each has, which is also the binding ``moodle.py``
  resolves through.
* ``llm_response`` / ``slm_response`` are patched where they are resolved
  (``tools.ai`` for the former, ``ask_ollama`` for both) with functions that
  raise. ``.env`` carries a real ``OPENAI_API_KEY`` that ``ask_ollama``'s
  module-level ``load_dotenv()`` loads into ``os.environ``, so an unmocked path
  into ``llm_response`` would be a live billed OpenAI call. ``requests.post`` is
  blocked for the same reason.

No test here contacts the network: no Google API, no Ollama, no OpenAI, no Go
server on :8001, no rate limiter on :8002.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import MagicMock

import httpx
import pytest
import requests

# Layer 1: no real Chroma client and no real embedding model is ever constructed.
# ``store.py`` builds both at import time, so the import itself must be guarded.
# These are ``mock.patch`` target strings, not imports: this module imports
# neither package.
with mock.patch("langchain_chroma.Chroma"), mock.patch(
    "langchain_ollama.OllamaEmbeddings"
):
    from backend.services.vector_db import store
    from backend.services.vector_db.app import app as vector_db_app

from backend.libs.common.http import AsyncServiceClient
from backend.services.accounts.app import app as accounts_app
from backend.services.accounts.clients import (
    get_database_client as accounts_database_client,
)
from backend.services.database.app import app as database_app
from backend.services.database.manager import DatabaseManager, get_db_manager
from backend.services.mcp import clients as mcp_clients
from backend.services.mcp import mcp_server
from backend.services.mcp import resources as mcp_resources
from backend.services.mcp import ask_ollama
from backend.services.mcp.tools import ai as ai_tools
from backend.services.mcp.tools import emails as emails_tools
from backend.services.user_data import gmail, google_calendar, moodle
from backend.services.user_data.app import app as user_data_app
from backend.services.user_data.clients import (
    get_accounts_client as user_data_accounts_client,
    get_database_client as user_data_database_client,
    get_vector_db_client as user_data_vector_db_client,
)
from backend.services.user_data.config import CALENDAR_EMAIL_ACCOUNT_ID

# The real production database. Read-only here, and only to prove it is never
# written to.
REAL_DATABASE = Path(__file__).resolve().parents[2] / "gmail_agent.db"

ACCOUNT_EMAIL = "mcp_test@example.com"
EMAIL_ACCOUNT_EMAIL = "mcp_test_gmail@example.com"


# ---------------------------------------------------------------------------
# Tool resolution — harvested from the old suite verbatim
# ---------------------------------------------------------------------------


def get_tool_function(tool):
    """Extract the actual function from a FunctionTool wrapper.

    Carried over from the old suite unchanged. ``mcp_server.py`` registers with
    ``mcp.tool()(fn)`` and keeps the plain function bound to the module name, so
    the ``hasattr(tool_obj, 'fn')`` guard Spec 6.5 requires stays exercised
    either way.
    """
    if hasattr(tool, 'fn'):
        return tool.fn
    return tool


# Account management tools
list_accounts = get_tool_function(mcp_server.list_accounts)
list_email_accounts = get_tool_function(mcp_server.list_email_accounts)
get_account_info = get_tool_function(mcp_server.get_account_info)
get_email_account_info = get_tool_function(mcp_server.get_email_account_info)

# Email tools
search_emails = get_tool_function(mcp_server.search_emails)
sync_emails = get_tool_function(mcp_server.sync_emails)
get_email_details = get_tool_function(mcp_server.get_email_details)
get_email_account_emails = get_tool_function(mcp_server.get_email_account_emails)

# Calendar tools
create_calendar_event = get_tool_function(mcp_server.create_calendar_event)
update_calendar_event = get_tool_function(mcp_server.update_calendar_event)
delete_calendar_event = get_tool_function(mcp_server.delete_calendar_event)
get_calendar_events = get_tool_function(mcp_server.get_calendar_events)

# AI-enhanced tools
extract_dates_from_emails = get_tool_function(mcp_server.extract_dates_from_emails)
summarize_emails = get_tool_function(mcp_server.summarize_emails)

CALENDAR_TOOLS = (
    create_calendar_event,
    update_calendar_event,
    delete_calendar_event,
    get_calendar_events,
)


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    digest.update(path.read_bytes())
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDocument:
    """Duck-typed stand-in for a LangChain ``Document``.

    ``store.query_vector_db`` returns whatever the collection hands back, and
    only ``page_content`` and ``metadata`` are ever read downstream.
    """

    def __init__(self, page_content: str, metadata: dict[str, Any]) -> None:
        self.page_content = page_content
        self.metadata = metadata


class FakeCollection:
    """Stands in for the module-scope Chroma collection in ``store``."""

    def __init__(self) -> None:
        self.add_documents_calls: list[tuple[list[Any], list[str]]] = []
        self.searches: list[tuple[list[float], int]] = []
        self.hits: list[FakeDocument] = []
        self._collection = MagicMock()

    def add_documents(self, documents, ids=None):
        self.add_documents_calls.append((documents, ids))

    def similarity_search_by_vector(self, embedding, k=None):
        self.searches.append((embedding, k))
        return list(self.hits)


class FakeEmbeddings:
    """Stands in for ``OllamaEmbeddings``. Never hits the network."""

    def embed_query(self, text):
        return [0.1, 0.2, 0.3]


class _Executable:
    def __init__(self, result: Any) -> None:
        self.result = result

    def execute(self):
        return self.result


class FakeGmailService:
    """A googleapiclient-shaped fake for the handful of calls we make.

    ``list_message_ids``, ``get_message_metadata`` and ``get_message_body`` run
    for real against it, so ``prepare_email_data`` is exercised end to end.
    """

    def __init__(self, mailbox: dict[str, dict[str, Any]]) -> None:
        self.mailbox = mailbox
        self.list_queries: list[dict[str, Any]] = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId=None, q=None, labelIds=None, maxResults=None, pageToken=None):
        self.list_queries.append({"q": q, "maxResults": maxResults})
        return _Executable({"messages": [{"id": msg_id} for msg_id in self.mailbox]})

    def get(self, userId=None, id=None, format=None, metadataHeaders=None):
        message = self.mailbox[id]
        if format == "metadata":
            return _Executable(
                {
                    "payload": {
                        "headers": [
                            {"name": name, "value": value}
                            for name, value in message["headers"].items()
                        ]
                    },
                    "snippet": message.get("snippet", ""),
                }
            )
        return _Executable({"payload": message["payload"]})


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _gmail_message(subject: str, sender: str, body_text: str) -> dict[str, Any]:
    return {
        "headers": {
            "From": sender,
            "To": EMAIL_ACCOUNT_EMAIL,
            "Subject": subject,
            "Date": "Mon, 15 Sep 2026 10:30:00 +0000",
        },
        "snippet": "snip",
        "payload": {
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(body_text)}},
                {"mimeType": "text/html", "body": {"data": _b64(f"<p>{body_text}</p>")}},
            ]
        },
    }


class ExplodingClient:
    """A downstream client that always fails, for the error-envelope tests."""

    def __init__(self, message: str) -> None:
        self.message = message

    async def _boom(self, *args, **kwargs):
        raise RuntimeError(self.message)

    get = post = put = patch = delete = _boom


class Stack:
    """The wired-up in-process service stack handed to every test."""

    def __init__(
        self,
        manager: DatabaseManager,
        database: AsyncServiceClient,
        accounts: AsyncServiceClient,
        user_data: AsyncServiceClient,
        collection: FakeCollection,
    ) -> None:
        self.manager = manager
        self.database = database
        self.accounts = accounts
        self.user_data = user_data
        self.collection = collection


class Calendar:
    """Everything a test needs about the faked Google Calendar."""

    def __init__(self, service, events_store: dict, requested_ids: list[int]) -> None:
        self.service = service
        self.events_store = events_store
        self.requested_ids = requested_ids


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path) -> DatabaseManager:
    """A ``DatabaseManager`` on a throwaway database, never the real one.

    This one line is the whole point of the file (hazard B4). The old suite's
    ``DatabaseManager()`` resolved to ``sqlite:///gmail_agent.db``, i.e. real
    user data. Do not remove the url.
    """
    return DatabaseManager(f"sqlite:///{tmp_path / 'test_gmail_agent.db'}")


@pytest.fixture
def collection(monkeypatch) -> FakeCollection:
    fake = FakeCollection()
    monkeypatch.setattr(store, "collection", fake)
    monkeypatch.setattr(store, "embeddings", FakeEmbeddings())
    return fake


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in this file may reach Ollama, OpenAI or the Go server."""

    def boom(*args, **kwargs):  # pragma: no cover - reaching it is the failure
        raise AssertionError("no test in this file may make a network call")

    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(ask_ollama, "slm_response", boom)
    monkeypatch.setattr(ask_ollama, "llm_response", boom)
    monkeypatch.setattr(ai_tools, "llm_response", boom)


@pytest.fixture(autouse=True)
def reset_last_sync_time(monkeypatch):
    """``context`` is module-level process state (X9); isolate it per test."""
    monkeypatch.setitem(emails_tools.context, "last_sync_time", None)


@pytest.fixture
def stack(manager, collection, monkeypatch):
    """MCP's tool layer wired to the real Accounts, User_data, Database and
    Vector DB apps, all in-process over ``httpx.ASGITransport``."""
    database_app.dependency_overrides[get_db_manager] = lambda: manager

    database = AsyncServiceClient(
        "http://database.test", transport=httpx.ASGITransport(app=database_app)
    )
    vector_db = AsyncServiceClient(
        "http://vector-db.test", transport=httpx.ASGITransport(app=vector_db_app)
    )

    accounts_app.dependency_overrides[accounts_database_client] = lambda: database
    accounts = AsyncServiceClient(
        "http://accounts.test", transport=httpx.ASGITransport(app=accounts_app)
    )

    user_data_app.dependency_overrides[user_data_database_client] = lambda: database
    user_data_app.dependency_overrides[user_data_vector_db_client] = lambda: vector_db
    user_data_app.dependency_overrides[user_data_accounts_client] = lambda: accounts
    user_data = AsyncServiceClient(
        "http://user-data.test", transport=httpx.ASGITransport(app=user_data_app)
    )

    # The tool layer has no app, so the provider functions themselves are the seam.
    monkeypatch.setattr(mcp_clients, "get_accounts_client", lambda: accounts)
    monkeypatch.setattr(mcp_clients, "get_user_data_client", lambda: user_data)

    try:
        yield Stack(manager, database, accounts, user_data, collection)
    finally:
        user_data_app.dependency_overrides.clear()
        accounts_app.dependency_overrides.clear()
        database_app.dependency_overrides.clear()


@pytest.fixture
async def test_account(stack) -> dict:
    """Create a test account.

    Harvested from the old suite. Its teardown block — which deleted rows out of
    the real database — is gone on purpose: the database is a ``tmp_path`` file
    that pytest discards. That is the fix, not an omission.
    """
    response = await stack.database.post(
        "/accounts/get-or-create",
        json={"primary_email": ACCOUNT_EMAIL, "password_hash": "test_password_hash"},
    )
    return response.json()


@pytest.fixture
async def test_email_account(stack, test_account) -> dict:
    """Create a test email account linked to the test account."""
    response = await stack.database.post(
        "/email-accounts/get-or-create",
        json={
            "account_id": test_account["id"],
            "email": EMAIL_ACCOUNT_EMAIL,
            "provider": "gmail",
            "is_primary": True,
        },
    )
    return response.json()


@pytest.fixture
async def test_email_account_with_emails(stack, test_email_account) -> dict:
    """Create a test email account with sample emails.

    The three mails and every field are the old suite's, with ``date_sent``
    serialized to ISO strings because the Database service takes a bare JSON
    array of ``EmailInputDTO`` (addendum A1) rather than ORM kwargs.
    """
    emails = [
        {
            "message_id": "mcp_test_msg_001",
            "subject": "Project Deadline Reminder",
            "sender": "pm@company.com",
            "recipient": EMAIL_ACCOUNT_EMAIL,
            "date_sent": (datetime.now() - timedelta(days=2)).isoformat(),
            "snippet": "The project is due next Friday",
            "body_text": "Hi team, just a reminder that our project deadline is next Friday, December 6th. Please make sure all deliverables are ready.",
            "body_html": "<p>Hi team, just a reminder that our project deadline is next Friday, December 6th.</p>",
        },
        {
            "message_id": "mcp_test_msg_002",
            "subject": "Team Meeting Tomorrow",
            "sender": "boss@company.com",
            "recipient": EMAIL_ACCOUNT_EMAIL,
            "date_sent": (datetime.now() - timedelta(days=1)).isoformat(),
            "snippet": "Meeting at 10am in conference room",
            "body_text": "Please join our team meeting tomorrow at 10am in conference room A. We'll discuss Q4 goals.",
            "body_html": None,
        },
        {
            "message_id": "mcp_test_msg_003",
            "subject": "Lunch invitation",
            "sender": "colleague@company.com",
            "recipient": EMAIL_ACCOUNT_EMAIL,
            "date_sent": datetime.now().isoformat(),
            "snippet": "Want to grab lunch?",
            "body_text": "Hey! Want to grab lunch today at noon? Let me know!",
            "body_html": None,
        },
    ]
    saved = await stack.database.post(
        "/emails", params={"email_account_id": test_email_account["id"]}, json=emails
    )
    assert saved.json()["count"] == 3
    return test_email_account


@pytest.fixture
def mock_calendar_service():
    """
    Mock Google Calendar API service for calendar operations.
    Maintains state for CRUD operations during tests.

    Harvested from the old suite. Two additions, both required by the new
    Moodle-merge test and both inert for every primary-calendar test:
    ``events().list`` now honours ``calendarId`` instead of returning the
    primary store for every calendar, and ``calendarList().list`` is wired to a
    (by default empty) calendar list.
    """
    # In-memory event storage for this test session
    events_store = {}
    # Moodle side: {calendar_id: [event, ...]}, plus the calendarList payload.
    moodle_store: dict[str, list[dict]] = {}
    calendars: list[dict] = []

    service = MagicMock()

    # Mock events().list()
    def mock_list(**kwargs):
        result = MagicMock()
        calendar_id = kwargs.get("calendarId")
        if calendar_id == "primary":
            items = list(events_store.values())
        else:
            items = list(moodle_store.get(calendar_id, []))
        result.execute.return_value = {"items": items}
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

    # Mock calendarList().list() — how moodle.py finds the Moodle calendar.
    def mock_calendar_list():
        result = MagicMock()
        result.execute.return_value = {"items": calendars}
        return result

    # Attach mocks to service
    service.events.return_value.list = mock_list
    service.events.return_value.insert = mock_insert
    service.events.return_value.get = mock_get
    service.events.return_value.update = mock_update
    service.events.return_value.delete = mock_delete
    service.calendarList.return_value.list = mock_calendar_list

    # Also set up _http.credentials for credential saving
    service._http = MagicMock()
    service._http.credentials = None

    # Handles for the Moodle test.
    service.test_calendars = calendars
    service.test_moodle_store = moodle_store

    return service, events_store


@pytest.fixture
def calendar(monkeypatch, mock_calendar_service) -> Calendar:
    """Patch the single ``get_calendar_service`` binding, recording which email
    account id each caller asked for (X4).

    The old suite wrapped every calendar test in
    ``patch('backend.mcp_server.get_calendar_service', ...)``. The function now
    lives in User_data, behind the ``/internal/calendar/*`` routes, and this is
    the one binding both those routes and ``moodle.py`` resolve through.
    """
    service, events_store = mock_calendar_service
    requested_ids: list[int] = []

    def get_calendar_service(email_account_id=None):
        requested_ids.append(email_account_id)
        return service, None

    monkeypatch.setattr(google_calendar, "get_calendar_service", get_calendar_service)
    return Calendar(service, events_store, requested_ids)


# ============================================================================
# THE REGISTERED SURFACE
# ============================================================================


async def test_stdio_server_registers_14_tools_and_9_resources():
    """Spec 6.4 acceptance: the stdio entrypoint exposes 14 tools + 9 resources.

    Counts come from the ``FastMCP`` instance, never from a hand-written list,
    so renaming a tool does not silently weaken this test. Nine resources means
    four static URIs plus five URI templates — FastMCP splits them by whether
    the URI has parameters.
    """
    tools = await mcp_server.mcp.list_tools()
    resources = await mcp_server.mcp.list_resources()
    templates = await mcp_server.mcp.list_resource_templates()

    assert len(tools) == 14
    assert len(resources) + len(templates) == 9
    # Every registration resolves to a real callable through the same
    # ``hasattr(tool_obj, 'fn')`` guard llm_integration.py relies on.
    assert all(callable(get_tool_function(tool)) for tool in tools)


async def test_the_tool_stack_never_touches_the_real_database(stack, monkeypatch):
    """Hazard B4, asserted rather than assumed.

    A sync writes two rows and an embedding through the whole stack; the real
    ``gmail_agent.db`` must be byte-identical afterwards.
    """
    if not REAL_DATABASE.exists():  # pragma: no cover - it does exist in this repo
        pytest.skip("the real gmail_agent.db is not present")

    before = _md5(REAL_DATABASE)

    service = FakeGmailService(
        {
            "b4-1": _gmail_message("One", "a@example.com", "first body"),
            "b4-2": _gmail_message("Two", "b@example.com", "second body"),
        }
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    result = await sync_emails(email_account_id=42, max_results=5)

    assert result["status"] == "success"
    assert result["new_emails"] == 2
    assert str(stack.manager.engine.url).endswith("test_gmail_agent.db")
    assert _md5(REAL_DATABASE) == before


# ============================================================================
# ACCOUNT MANAGEMENT TOOL TESTS
# ============================================================================


async def test_list_accounts(test_account):
    """Test listing all accounts."""
    result = await list_accounts()

    assert result["status"] == "success"
    assert "accounts" in result
    assert result["count"] > 0

    # Check our test account is in the list
    account_emails = [a["primary_email"] for a in result["accounts"]]
    assert ACCOUNT_EMAIL in account_emails


async def test_list_accounts_is_key_for_key(test_account):
    """The envelope and the per-account keys are exactly today's."""
    result = await list_accounts()

    assert set(result) == {"status", "accounts", "count"}
    assert set(result["accounts"][0]) == {"id", "primary_email", "created_at"}
    assert result["count"] == len(result["accounts"]) == 1


async def test_list_accounts_error_envelope(stack, monkeypatch):
    """The catch shape is ``{"status": "error", "error": str(e)}`` — two keys."""
    monkeypatch.setattr(
        mcp_clients, "get_accounts_client", lambda: ExplodingClient("accounts down")
    )

    result = await list_accounts()

    assert result == {"status": "error", "error": "accounts down"}


async def test_list_email_accounts(test_email_account):
    """Test listing all email accounts."""
    result = await list_email_accounts()

    assert result["status"] == "success"
    assert "email_accounts" in result
    assert result["count"] > 0

    # Check our test email account is in the list
    email_account_emails = [ea["email"] for ea in result["email_accounts"]]
    assert EMAIL_ACCOUNT_EMAIL in email_account_emails


async def test_list_email_accounts_filtered(test_account, test_email_account):
    """Test listing email accounts filtered by account_id."""
    result = await list_email_accounts(account_id=test_account["id"])

    assert result["status"] == "success"
    assert "email_accounts" in result

    # All returned email accounts should belong to test_account
    for ea in result["email_accounts"]:
        assert ea["account_id"] == test_account["id"]


async def test_list_email_accounts_is_key_for_key(test_email_account):
    """``is_primary`` is coerced with ``bool(...)`` even though it is stored 0/1."""
    result = await list_email_accounts()

    assert set(result) == {"status", "email_accounts", "count"}
    entry = result["email_accounts"][0]
    assert set(entry) == {"id", "email", "provider", "account_id", "is_primary"}
    assert entry["is_primary"] is True


async def test_get_account_info(test_account, test_email_account):
    """Test getting specific account information."""
    result = await get_account_info(account_id=test_account["id"])

    assert result["status"] == "success"
    assert result["account"]["id"] == test_account["id"]
    assert result["account"]["primary_email"] == ACCOUNT_EMAIL
    assert result["account"]["email_accounts_count"] > 0


async def test_get_account_info_is_key_for_key(test_account, test_email_account):
    result = await get_account_info(account_id=test_account["id"])

    assert set(result) == {"status", "account"}
    assert set(result["account"]) == {
        "id",
        "primary_email",
        "created_at",
        "email_accounts_count",
        "email_accounts",
    }
    assert set(result["account"]["email_accounts"][0]) == {
        "id",
        "email",
        "provider",
        "is_primary",
    }


async def test_get_account_info_invalid_id(stack):
    """Test getting account info with invalid ID."""
    result = await get_account_info(account_id=99999)

    assert result["status"] == "error"
    assert "not found" in result["error"].lower()
    # The exact sentence the LLM sees.
    assert result == {"status": "error", "error": "Account with ID 99999 not found"}


async def test_get_email_account_info(test_email_account):
    """Test getting specific email account information."""
    result = await get_email_account_info(email_account_id=test_email_account["id"])

    assert result["status"] == "success"
    assert result["email_account"]["id"] == test_email_account["id"]
    assert result["email_account"]["email"] == EMAIL_ACCOUNT_EMAIL
    assert result["email_account"]["provider"] == "gmail"


async def test_get_email_account_info_joins_two_services(
    test_email_account_with_emails,
):
    """Identity comes from Accounts, ``email_count`` from User_data."""
    result = await get_email_account_info(
        email_account_id=test_email_account_with_emails["id"]
    )

    assert set(result) == {"status", "email_account"}
    assert set(result["email_account"]) == {
        "id",
        "email",
        "provider",
        "account_id",
        "is_primary",
        "account_primary_email",
        "email_count",
        "created_at",
    }
    assert result["email_account"]["account_primary_email"] == ACCOUNT_EMAIL
    assert result["email_account"]["email_count"] == 3


async def test_get_email_account_info_invalid_id(stack):
    """Test getting email account info with invalid ID."""
    result = await get_email_account_info(email_account_id=99999)

    assert result["status"] == "error"
    assert "not found" in result["error"].lower()
    assert result == {
        "status": "error",
        "error": "Email account with ID 99999 not found",
    }


# ============================================================================
# EMAIL TOOL TESTS
# ============================================================================


async def test_get_email_account_emails(test_email_account_with_emails):
    """Test getting emails for a specific email account."""
    result = await get_email_account_emails(
        email_account_id=test_email_account_with_emails["id"], limit=10
    )

    assert result["status"] == "success"
    assert result["count"] == 3
    assert len(result["emails"]) == 3

    # Verify email data
    subjects = [email["subject"] for email in result["emails"]]
    assert "Project Deadline Reminder" in subjects
    assert "Team Meeting Tomorrow" in subjects


async def test_get_email_account_emails_is_key_for_key(
    test_email_account_with_emails,
):
    """The per-email projection drops ``body_text`` / ``body_html`` on purpose."""
    result = await get_email_account_emails(
        email_account_id=test_email_account_with_emails["id"], limit=10
    )

    assert set(result) == {"status", "emails", "count", "email_account_id"}
    assert result["email_account_id"] == test_email_account_with_emails["id"]
    assert set(result["emails"][0]) == {
        "message_id",
        "subject",
        "sender",
        "recipient",
        "date_sent",
        "snippet",
    }


async def test_get_email_details(test_email_account_with_emails):
    """Test getting details of a specific email."""
    result = await get_email_details(message_id="mcp_test_msg_001")

    assert result["status"] == "success"
    assert result["email"]["subject"] == "Project Deadline Reminder"
    assert result["email"]["sender"] == "pm@company.com"
    assert "deadline" in result["email"]["body_text"].lower()
    assert result["email"]["email_account_id"] == test_email_account_with_emails["id"]


async def test_get_email_details_is_key_for_key(test_email_account_with_emails):
    """``thread_id`` is always ``None`` (X5) and is still returned."""
    result = await get_email_details(message_id="mcp_test_msg_001")

    assert set(result) == {"status", "email"}
    assert set(result["email"]) == {
        "message_id",
        "thread_id",
        "subject",
        "sender",
        "recipient",
        "date_sent",
        "snippet",
        "body_text",
        "body_html",
        "email_account_id",
    }
    assert result["email"]["thread_id"] is None


async def test_get_email_details_not_found(stack):
    """Test getting details of non-existent email."""
    result = await get_email_details(message_id="nonexistent_id")

    assert result["status"] == "error"
    assert "not found" in result["error"].lower()
    assert result == {
        "status": "error",
        "error": "Email with message_id nonexistent_id not found",
    }


async def test_search_emails_by_content(test_email_account_with_emails):
    """Test searching emails by content in database.

    Adapted: the old test reached past the tools into SQLAlchemy
    (``session.query(Email).filter(Email.body_text.ilike('%deadline%'))``) on
    the real database. There is no ``ilike`` route in the split — that query was
    the test's own, not production code — so the same claim (the seeded corpus
    is content-searchable and the bodies survive the round trip) is asserted
    through the owning service instead.
    """
    result = await get_email_account_emails(
        email_account_id=test_email_account_with_emails["id"], limit=10
    )
    assert result["status"] == "success"

    matches = []
    for email in result["emails"]:
        details = await get_email_details(message_id=email["message_id"])
        if "deadline" in details["email"]["body_text"].lower():
            matches.append(details["email"])

    assert len(matches) > 0
    assert any("deadline" in email["body_text"].lower() for email in matches)


async def test_search_emails_gmail_requires_email_account_id(stack):
    """The error sentence the LLM branches on, verbatim."""
    result = await search_emails(query="from:pm@company.com")

    assert result == {
        "status": "error",
        "error": "email_account_id required for Gmail API search",
    }


async def test_search_emails_gmail_branch(
    test_email_account_with_emails, monkeypatch
):
    """Gmail answers *which* ids match; the rows come from the cache."""
    service = FakeGmailService({"mcp_test_msg_001": {}, "mcp_test_msg_002": {}})
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    result = await search_emails(
        query="deadline",
        email_account_id=test_email_account_with_emails["id"],
        limit=5,
    )

    assert result["status"] == "success"
    assert result["search_type"] == "gmail_api"
    assert result["count"] == 2
    assert set(result) == {"status", "results", "count", "search_type"}
    assert set(result["results"][0]) == {
        "message_id",
        "sender",
        "subject",
        "date",
        "snippet",
    }
    assert {row["message_id"] for row in result["results"]} == {
        "mcp_test_msg_001",
        "mcp_test_msg_002",
    }
    assert service.list_queries[0]["q"] == "deadline"


async def test_search_emails_semantic_branch(
    stack, test_email_account_with_emails, collection
):
    """The semantic branch drops the ``documents`` key the route carries.

    ``/internal/emails/search/semantic`` returns ``documents`` too, because
    ``/api/query`` needs the raw hits for its retrieval context. The *tool* dict
    never had that key and still must not.
    """
    collection.hits = [
        FakeDocument(
            "the project deadline is next Friday",
            {
                "message_id": "mcp_test_msg_001",
                "sender": "pm@company.com",
                "subject": "Project Deadline Reminder",
                "date_sent": "2026-09-15 10:30:00",
            },
        )
    ]

    result = await search_emails(
        query="deadline",
        email_account_id=test_email_account_with_emails["id"],
        use_semantic=True,
        limit=4,
    )

    assert set(result) == {"status", "results", "count", "search_type"}
    assert "documents" not in result
    assert result["search_type"] == "semantic"
    assert result["count"] == 1
    assert result["results"][0] == {
        "message_id": "mcp_test_msg_001",
        "sender": "pm@company.com",
        "subject": "Project Deadline Reminder",
        "date": "2026-09-15 10:30:00",
        "snippet": "the project deadline is next Friday",
    }
    # ``limit`` reached the vector store as Chroma's ``k``.
    assert collection.searches[0][1] == 4


async def test_search_emails_error_envelope(stack, monkeypatch):
    monkeypatch.setattr(
        mcp_clients, "get_user_data_client", lambda: ExplodingClient("user_data down")
    )

    result = await search_emails(query="anything", email_account_id=1)

    assert result == {"status": "error", "error": "user_data down"}


async def test_sync_emails_sets_last_sync_time(stack, collection, monkeypatch):
    """``sync_emails`` is the only writer of ``context["last_sync_time"]``."""
    assert emails_tools.context["last_sync_time"] is None

    service = FakeGmailService(
        {"sync-1": _gmail_message("Synced", "s@example.com", "a body")}
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    result = await sync_emails(email_account_id=7, max_results=3)

    assert set(result) == {"status", "total_fetched", "new_emails", "last_sync"}
    assert result["status"] == "success"
    assert result["total_fetched"] == 1
    assert result["new_emails"] == 1
    assert result["last_sync"] == emails_tools.context["last_sync_time"]
    datetime.fromisoformat(result["last_sync"])

    # max_results reached Gmail and the mail was embedded once.
    assert service.list_queries[0]["maxResults"] == 3
    assert len(collection.add_documents_calls) == 1


async def test_sync_emails_no_new_emails_leaves_last_sync_time_alone(
    stack, monkeypatch
):
    """The "No new emails found" branch returns the route's dict untouched."""
    monkeypatch.setattr(gmail, "get_service", lambda _id: FakeGmailService({}))

    result = await sync_emails(email_account_id=7)

    assert result == {
        "status": "success",
        "message": "No new emails found",
        "total_fetched": 0,
        "new_emails": 0,
    }
    assert emails_tools.context["last_sync_time"] is None


async def test_sync_emails_error_envelope(stack, monkeypatch):
    monkeypatch.setattr(
        mcp_clients, "get_user_data_client", lambda: ExplodingClient("sync exploded")
    )

    result = await sync_emails(email_account_id=7)

    assert result == {"status": "error", "error": "sync exploded"}
    assert emails_tools.context["last_sync_time"] is None


async def test_last_sync_time_is_mcp_process_local(stack, monkeypatch):
    """X9, preserved.

    ``context`` is one dict shared by the tool, the stdio entrypoint and the
    ``system://status`` resource inside this process; it is never sent to or
    stored by another service, and it starts out ``None`` on every restart.
    """
    assert emails_tools.context is mcp_server.context
    assert emails_tools.context is mcp_resources.context

    service = FakeGmailService(
        {"x9-1": _gmail_message("Synced", "s@example.com", "a body")}
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)
    await sync_emails(email_account_id=7)
    assert emails_tools.context["last_sync_time"] is not None

    # Not shared with any other service: neither the User_data sync route nor
    # the email stats it owns carries a sync timestamp.
    route_payload = (
        await stack.user_data.post(
            "/internal/emails/sync", params={"email_account_id": 7}
        )
    ).json()
    assert "last_sync" not in route_payload
    assert "last_sync_time" not in route_payload
    stats = (await stack.user_data.get("/internal/emails/stats")).json()
    assert set(stats) == {"total_emails", "latest_email_date"}

    # Reset on restart, proved the only honest way: a *fresh interpreter* is a
    # restart. This process has just set the timestamp; a new one must still see
    # None, because nothing persisted it anywhere.
    fresh = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json;"
            "from backend.services.mcp.tools.emails import context;"
            "print(json.dumps(context))",
        ],
        cwd=REAL_DATABASE.parent,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(fresh.stdout.strip().splitlines()[-1]) == {
        "last_sync_time": None
    }


# ============================================================================
# CALENDAR CRUD TOOL TESTS
# ============================================================================


async def test_calendar_create_event(stack, calendar):
    """Test creating a calendar event."""
    events_store = calendar.events_store

    # Track initial state
    initial_count = len(events_store)

    # Create event for today
    today = datetime.now().date()
    result = await create_calendar_event(
        title="Test Meeting",
        date=today.isoformat(),
        time="2:00 PM",
        description="This is a test meeting",
        category="Career",
    )

    assert result["status"] == "success"
    assert "event_id" in result
    assert result["title"] == "Test Meeting"
    assert result["date"] == today.isoformat()
    assert set(result) == {"status", "event_id", "event_link", "title", "date"}

    # Verify event was added to store (check delta, not absolute)
    assert len(events_store) == initial_count + 1

    # Find our created event
    created_event = events_store[result["event_id"]]
    assert created_event["summary"] == "Test Meeting"
    # A timed event, so dateTime rather than date.
    assert "dateTime" in created_event["start"]

    # Cleanup
    await delete_calendar_event(event_id=result["event_id"])


async def test_calendar_create_all_day_event(stack, calendar):
    """Test creating an all-day calendar event."""
    events_store = calendar.events_store

    initial_count = len(events_store)

    tomorrow = (datetime.now() + timedelta(days=1)).date()
    result = await create_calendar_event(
        title="All Day Event",
        date=tomorrow.isoformat(),
        time="All Day",
        category="Social",
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


async def test_calendar_full_crud_cycle(stack, calendar):
    """
    Test complete CRUD cycle: Create -> Read -> Update -> Delete
    This ensures no side effects remain after the test.
    """
    events_store = calendar.events_store

    # Track initial state
    initial_count = len(events_store)

    # 1. CREATE
    today = datetime.now().date()
    create_result = await create_calendar_event(
        title="CRUD Test Event",
        date=today.isoformat(),
        time="3:00 PM",
        description="Testing full CRUD cycle",
        category="Academic",
    )

    assert create_result["status"] == "success"
    event_id = create_result["event_id"]
    assert len(events_store) == initial_count + 1

    # 2. READ (via get_calendar_events)
    read_result = await get_calendar_events(
        start_date=today.isoformat(),
        end_date=(today + timedelta(days=1)).isoformat(),
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
        description="Updated description",
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


async def test_calendar_update_event(stack, calendar):
    """Test updating an existing calendar event."""
    events_store = calendar.events_store

    # First create an event
    today = datetime.now().date()
    create_result = await create_calendar_event(
        title="Original Title", date=today.isoformat(), category="Deadline"
    )
    event_id = create_result["event_id"]

    # Now update it
    update_result = await update_calendar_event(
        event_id=event_id, title="Modified Title", category="Academic"
    )

    assert update_result["status"] == "success"
    assert set(update_result) == {"status", "event_id", "event_link"}

    # Verify the update
    updated_event = events_store[event_id]
    assert updated_event["summary"] == "Modified Title"
    assert updated_event["extendedProperties"]["private"]["category"] == "Academic"

    # Cleanup
    await delete_calendar_event(event_id=event_id)


async def test_calendar_delete_event(stack, calendar):
    """Test deleting a calendar event."""
    events_store = calendar.events_store

    initial_count = len(events_store)

    # Create an event
    today = datetime.now().date()
    create_result = await create_calendar_event(
        title="Event to Delete", date=today.isoformat()
    )
    event_id = create_result["event_id"]
    assert len(events_store) == initial_count + 1

    # Delete it
    delete_result = await delete_calendar_event(event_id=event_id)

    assert delete_result["status"] == "success"
    assert delete_result == {
        "status": "success",
        "message": f"Event {event_id} deleted successfully",
    }
    assert event_id not in events_store
    assert len(events_store) == initial_count  # Back to initial


async def test_calendar_get_events(stack, calendar):
    """Test getting calendar events for a date range."""
    events_store = calendar.events_store

    initial_count = len(events_store)

    # Create multiple events
    today = datetime.now().date()
    event_ids = []

    for i in range(3):
        result = await create_calendar_event(
            title=f"Test Event {i+1}",
            date=(today + timedelta(days=i)).isoformat(),
            category="Social",
        )
        event_ids.append(result["event_id"])

    # Get events for date range
    get_result = await get_calendar_events(
        start_date=today.isoformat(),
        end_date=(today + timedelta(days=3)).isoformat(),
    )

    assert get_result["status"] == "success"
    assert set(get_result) == {
        "status",
        "events",
        "count",
        "primary_count",
        "sources",
    }
    assert get_result["sources"] == ["primary", "moodle"]

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


async def test_calendar_error_handling(stack, calendar):
    """Test calendar error handling for invalid operations."""
    # Try to update non-existent event
    update_result = await update_calendar_event(
        event_id="nonexistent_event_id", title="Should Fail"
    )

    assert update_result["status"] == "error"
    assert set(update_result) == {"status", "error"}

    # Try to delete non-existent event
    delete_result = await delete_calendar_event(event_id="nonexistent_event_id")

    # Delete might succeed (Google API returns success even if event doesn't exist)
    # So we just verify it doesn't crash
    assert "status" in delete_result


async def test_calendar_error_envelope(stack, monkeypatch):
    """All four calendar tools share the same two-key catch shape."""
    monkeypatch.setattr(
        mcp_clients, "get_user_data_client", lambda: ExplodingClient("calendar down")
    )

    assert await create_calendar_event(title="t", date="2026-01-01") == {
        "status": "error",
        "error": "calendar down",
    }
    assert await update_calendar_event(event_id="e") == {
        "status": "error",
        "error": "calendar down",
    }
    assert await delete_calendar_event(event_id="e") == {
        "status": "error",
        "error": "calendar down",
    }
    assert await get_calendar_events() == {
        "status": "error",
        "error": "calendar down",
    }


async def test_calendar_tools_always_use_calendar_email_account_1(stack, calendar):
    """X4, preserved: calendar work is hardwired to email account 1.

    None of the four tools takes an account argument, so no caller — LLM or
    HTTP — can steer them elsewhere, and every credential lookup asks for
    ``CALENDAR_EMAIL_ACCOUNT_ID``.
    """
    assert CALENDAR_EMAIL_ACCOUNT_ID == 1

    for tool in CALENDAR_TOOLS:
        parameters = set(inspect.signature(tool).parameters)
        assert not parameters & {"email_account_id", "account_id", "user_id"}

    created = await create_calendar_event(title="X4", date="2026-01-01")
    await update_calendar_event(event_id=created["event_id"], title="X4 again")
    await get_calendar_events(start_date="2026-01-01", end_date="2026-01-02")
    await delete_calendar_event(event_id=created["event_id"])

    assert calendar.requested_ids
    assert set(calendar.requested_ids) == {CALENDAR_EMAIL_ACCOUNT_ID}


async def test_get_calendar_events_includes_moodle_events(stack, calendar):
    """P2, fixed: MCP finally returns Moodle events.

    The monolith called ``get_moodle_events_for_api(email_account_id=...)``
    while the parameter was named ``user_id``, so every call raised
    ``TypeError`` straight into the surrounding ``except``, which logged a
    warning and returned primary-only events. MCP therefore never once returned
    a Moodle event. Wave 4 renamed the parameter; this proves the merge end to
    end, through the tool.
    """
    # The keyword the monolith used must now be the real parameter name.
    assert "email_account_id" in inspect.signature(
        moodle.get_moodle_events_for_api
    ).parameters

    calendar.service.test_calendars.append(
        {"id": "moodle-cal", "summary": "Moodle", "accessRole": "reader"}
    )
    calendar.service.test_moodle_store["moodle-cal"] = [
        {
            "id": "moodle-evt-1",
            "summary": "CS101 Assignment 3",
            "description": "Submit on Moodle",
            "start": {"dateTime": "2026-03-02T23:59:00+00:00"},
            "end": {"dateTime": "2026-03-03T00:59:00+00:00"},
        }
    ]

    primary = await create_calendar_event(
        title="Primary Standup", date="2026-03-01", time="10:00 AM"
    )

    result = await get_calendar_events(start_date="2026-03-01", end_date="2026-03-31")

    assert result["status"] == "success"
    assert result["primary_count"] == 1
    assert result["count"] == 2

    by_source = {event["source"]: event for event in result["events"]}
    assert set(by_source) == {"primary", "moodle"}
    assert by_source["primary"]["id"] == primary["event_id"]

    moodle_event = by_source["moodle"]
    assert moodle_event["id"] == "moodle-evt-1"
    assert moodle_event["title"] == "CS101 Assignment 3"
    assert moodle_event["category"] == "Moodle"
    assert moodle_event["start"] == "2026-03-02T23:59:00+00:00"
    assert moodle_event["description"] == "Submit on Moodle"

    # Moodle is resolved against the same hardwired email account (X4).
    assert set(calendar.requested_ids) == {CALENDAR_EMAIL_ACCOUNT_ID}


# ============================================================================
# AI-ENHANCED TOOL TESTS
# ============================================================================


async def test_extract_dates_basic(test_email_account_with_emails, monkeypatch):
    """Test basic date extraction from emails (without LLM call)."""
    monkeypatch.setattr(ai_tools, "llm_response", lambda prompt: "[]")

    result = await extract_dates_from_emails(
        email_account_id=test_email_account_with_emails["id"],
        limit=3,
        auto_create_events=False,
    )

    assert result["status"] == "success"
    assert "extracted_dates" in result
    assert result["count"] == 0  # Empty because LLM returned []
    assert set(result) == {"status", "extracted_dates", "count", "created_events"}
    # ``created_events`` is None, not [], when auto_create_events is False.
    assert result["created_events"] is None


async def test_extract_dates_with_mock_llm(
    test_email_account_with_emails, calendar, monkeypatch
):
    """Test date extraction with mocked LLM response.

    Revived from the old suite, where it sat inside a triple-quoted string and
    therefore never ran. It needed a live calendar; the faked one makes it real.
    """
    mock_llm_response = '''[
        {
            "date": "2025-12-06",
            "description": "Project deadline",
            "email_subject": "Project Deadline Reminder"
        }
    ]'''
    monkeypatch.setattr(ai_tools, "llm_response", lambda prompt: mock_llm_response)

    result = await extract_dates_from_emails(
        email_account_id=test_email_account_with_emails["id"],
        limit=3,
        auto_create_events=True,
    )

    assert result["status"] == "success"
    assert result["count"] == 1
    assert len(result["extracted_dates"]) == 1
    assert result["extracted_dates"][0]["date"] == "2025-12-06"

    # Should have created a calendar event
    assert len(result["created_events"]) == 1

    created = calendar.events_store[result["created_events"][0]]
    assert created["summary"] == "Project deadline"
    assert created["description"] == "From email: Project Deadline Reminder"
    assert created["extendedProperties"]["private"]["category"] == "Deadline"

    # Cleanup
    for event_id in result["created_events"]:
        await delete_calendar_event(event_id=event_id)


async def test_extract_dates_strips_markdown_fences(
    test_email_account_with_emails, monkeypatch
):
    """The LLM's ```json fences are peeled before ``json.loads``."""
    monkeypatch.setattr(
        ai_tools,
        "llm_response",
        lambda prompt: '```json\n[{"date": "2026-01-02", "description": "d"}]\n```',
    )

    result = await extract_dates_from_emails(
        email_account_id=test_email_account_with_emails["id"], limit=3
    )

    assert result["status"] == "success"
    assert result["extracted_dates"] == [{"date": "2026-01-02", "description": "d"}]


async def test_extract_dates_unparseable_llm_response(
    test_email_account_with_emails, monkeypatch
):
    """A non-JSON answer keeps its own three-key error envelope."""
    monkeypatch.setattr(ai_tools, "llm_response", lambda prompt: "I cannot help")

    result = await extract_dates_from_emails(
        email_account_id=test_email_account_with_emails["id"], limit=3
    )

    assert result == {
        "status": "error",
        "error": "Failed to parse dates from LLM response",
        "raw_response": "I cannot help",
    }


async def test_extract_dates_no_emails(stack):
    """No mails means no LLM call at all — the exploding default proves it."""
    result = await extract_dates_from_emails(email_account_id=4242, limit=3)

    assert result == {
        "status": "success",
        "extracted_dates": [],
        "message": "No emails found",
    }


async def test_summarize_emails_basic(
    stack, test_email_account_with_emails, collection, monkeypatch
):
    """Test basic email summarization (without actual LLM call).

    Revived from the old suite's commented-out block. Adapted in one way: it
    patched ``slm_response``, but ``summarize_emails`` has always called
    ``llm_response`` (X8 is the same choice made in ``/api/query``), so patching
    ``slm_response`` would have left a live OpenAI call in the test.
    """
    collection.hits = [
        FakeDocument(
            "project deadline next Friday",
            {
                "message_id": "mcp_test_msg_001",
                "sender": "pm@company.com",
                "subject": "Project Deadline Reminder",
                "date_sent": "2026-09-15 10:30:00",
            },
        ),
        FakeDocument(
            "team meeting tomorrow at 10am",
            {
                "message_id": "mcp_test_msg_002",
                "sender": "boss@company.com",
                "subject": "Team Meeting Tomorrow",
                "date_sent": "2026-09-16 10:30:00",
            },
        ),
        FakeDocument(
            "lunch today at noon",
            {
                "message_id": "mcp_test_msg_003",
                "sender": "colleague@company.com",
                "subject": "Lunch invitation",
                "date_sent": "2026-09-17 10:30:00",
            },
        ),
    ]
    prompts: list[str] = []

    def fake_llm(prompt):
        prompts.append(prompt)
        return (
            "Summary of emails: You have 3 emails including project deadline "
            "and meeting."
        )

    monkeypatch.setattr(ai_tools, "llm_response", fake_llm)

    result = await summarize_emails(
        query="all",
        email_account_id=test_email_account_with_emails["id"],
        summary_type="brief",
    )

    assert result["status"] == "success"
    assert "summary" in result
    assert result["email_count"] == 3
    assert (
        "deadline" in result["summary"].lower()
        or "meeting" in result["summary"].lower()
    )
    assert set(result) == {"status", "summary", "email_count", "summary_type"}
    assert result["summary_type"] == "brief"
    # ``brief`` is the fall-through branch of the prompt builder.
    assert prompts[0].startswith("TODAY'S DATE: ")
    assert prompts[0].endswith("Brief summary:\n")


@pytest.mark.parametrize(
    "summary_type, tail",
    [
        ("bullet_points", "Provide a concise bullet-point summary:\n"),
        ("detailed", "Detailed summary:\n"),
        ("nonsense", "Brief summary:\n"),
    ],
)
async def test_summarize_emails_prompt_branches(
    stack, test_email_account_with_emails, collection, monkeypatch, summary_type, tail
):
    """Three prompt shapes; anything unknown falls through to ``brief``."""
    collection.hits = [
        FakeDocument(
            "project deadline next Friday",
            {
                "message_id": "mcp_test_msg_001",
                "sender": "pm@company.com",
                "subject": "Project Deadline Reminder",
                "date_sent": "2026-09-15 10:30:00",
            },
        )
    ]
    prompts: list[str] = []

    def record(prompt):
        prompts.append(prompt)
        return "ok"

    monkeypatch.setattr(ai_tools, "llm_response", record)

    result = await summarize_emails(
        query="all",
        email_account_id=test_email_account_with_emails["id"],
        summary_type=summary_type,
    )

    assert result["summary_type"] == summary_type
    assert prompts[0].endswith(tail)


async def test_summarize_emails_no_results(stack, collection):
    """No hits short-circuits before the model, with a two-key dict."""
    collection.hits = []

    result = await summarize_emails(query="nothing", email_account_id=1)

    assert result == {
        "status": "success",
        "summary": "No emails found matching the criteria.",
    }


# ============================================================================
# RESOURCE TESTS — 9 resources, json.dumps(..., indent=2)
# ============================================================================


async def test_inbox_resource(test_email_account_with_emails):
    """mail://inbox/{email_account_id}"""
    raw = await mcp_resources.get_inbox_resource(
        test_email_account_with_emails["id"]
    )
    payload = json.loads(raw)

    assert raw.startswith("{\n  ")  # indent=2 on the success path
    assert set(payload) == {"email_account_id", "emails", "count"}
    assert payload["email_account_id"] == test_email_account_with_emails["id"]
    assert payload["count"] == 3
    assert set(payload["emails"][0]) == {
        "message_id",
        "subject",
        "sender",
        "date",
        "snippet",
    }


async def test_email_resource(test_email_account_with_emails):
    """mail://email/{message_id}"""
    payload = json.loads(
        await mcp_resources.get_email_resource("mcp_test_msg_001")
    )

    assert set(payload) == {
        "message_id",
        "thread_id",
        "subject",
        "sender",
        "recipient",
        "date_sent",
        "snippet",
        "body_text",
        "body_html",
        "email_account_id",
    }
    assert payload["subject"] == "Project Deadline Reminder"


async def test_email_resource_not_found(stack):
    """Resources answer with a bare, *unindented* ``{"error": ...}`` and no
    ``status`` key — deliberately unlike the tools."""
    raw = await mcp_resources.get_email_resource("nope")

    assert raw == '{"error": "Email with message_id nope not found"}'


async def test_calendar_events_resource(stack, calendar):
    """calendar://events"""
    await create_calendar_event(title="Resource Event", date="2026-03-01")

    payload = json.loads(await mcp_resources.get_calendar_events_resource())

    assert set(payload) == {"events", "count", "primary_count", "sources", "month"}
    assert payload["count"] == 1
    assert payload["primary_count"] == 1
    assert payload["sources"] == ["primary", "moodle"]
    assert payload["month"] == datetime.now().strftime("%B %Y")
    assert payload["events"][0]["title"] == "Resource Event"


async def test_calendar_events_resource_error(stack, monkeypatch):
    monkeypatch.setattr(
        mcp_clients, "get_user_data_client", lambda: ExplodingClient("no calendar")
    )

    raw = await mcp_resources.get_calendar_events_resource()

    assert raw == '{"error": "no calendar"}'


async def test_calendar_event_resource(stack, calendar):
    """calendar://event/{event_id}"""
    created = await create_calendar_event(
        title="One Event", date="2026-03-04", time="9:00 AM", category="Academic"
    )

    payload = json.loads(
        await mcp_resources.get_calendar_event_resource(created["event_id"])
    )

    assert set(payload) == {
        "id",
        "title",
        "start",
        "end",
        "description",
        "link",
        "created",
        "updated",
        "category",
    }
    assert payload["id"] == created["event_id"]
    assert payload["title"] == "One Event"
    assert payload["category"] == "Academic"


async def test_accounts_resource(test_account):
    """account://list"""
    payload = json.loads(await mcp_resources.get_accounts_resource())

    assert set(payload) == {"accounts", "count"}
    assert payload["count"] == 1
    assert set(payload["accounts"][0]) == {"id", "primary_email", "created_at"}
    assert payload["accounts"][0]["primary_email"] == ACCOUNT_EMAIL


async def test_email_accounts_resource(test_email_account):
    """mailbox://list — carries ``created_at``, which the tool's dict omits."""
    payload = json.loads(await mcp_resources.get_email_accounts_resource())

    assert set(payload) == {"email_accounts", "count"}
    assert payload["count"] == 1
    assert set(payload["email_accounts"][0]) == {
        "id",
        "email",
        "provider",
        "account_id",
        "is_primary",
        "created_at",
    }


async def test_account_resource(test_email_account_with_emails, test_account):
    """account://info/{account_id} — totals emails across every mailbox."""
    payload = json.loads(
        await mcp_resources.get_account_resource(test_account["id"])
    )

    assert set(payload) == {
        "id",
        "primary_email",
        "created_at",
        "updated_at",
        "email_accounts_count",
        "total_emails",
        "email_accounts",
    }
    assert payload["email_accounts_count"] == 1
    assert payload["total_emails"] == 3


async def test_account_resource_not_found(stack):
    raw = await mcp_resources.get_account_resource(99999)

    assert raw == '{"error": "Account with ID 99999 not found"}'


async def test_email_account_resource(test_email_account_with_emails):
    """mailbox://info/{email_account_id}"""
    payload = json.loads(
        await mcp_resources.get_email_account_resource(
            test_email_account_with_emails["id"]
        )
    )

    assert set(payload) == {
        "id",
        "email",
        "provider",
        "account_id",
        "is_primary",
        "account_primary_email",
        "email_count",
        "created_at",
        "updated_at",
    }
    assert payload["email_count"] == 3
    assert payload["account_primary_email"] == ACCOUNT_EMAIL


async def test_email_account_resource_not_found(stack):
    raw = await mcp_resources.get_email_account_resource(99999)

    assert raw == '{"error": "Email account with ID 99999 not found"}'


async def test_system_status_resource_aggregates_two_services(
    test_email_account_with_emails, monkeypatch
):
    """system://status

    ``total_accounts`` / ``total_email_accounts`` come from Accounts
    ``/internal/stats``; ``total_emails`` / ``latest_email_date`` come from
    User_data ``/internal/emails/stats``; ``last_sync_time`` comes from MCP's own
    process-local ``context`` (X9).
    """
    payload = json.loads(await mcp_resources.get_system_status_resource())

    assert set(payload) == {
        "total_accounts",
        "total_email_accounts",
        "total_emails",
        "latest_email_date",
        "last_sync_time",
        "calendar_email_account_id",
        "timestamp",
    }
    assert payload["total_accounts"] == 1
    assert payload["total_email_accounts"] == 1
    assert payload["total_emails"] == 3
    assert payload["latest_email_date"] is not None
    assert payload["last_sync_time"] is None
    assert payload["calendar_email_account_id"] == CALENDAR_EMAIL_ACCOUNT_ID
    datetime.fromisoformat(payload["timestamp"])

    # After a sync the same resource reports the process-local timestamp.
    service = FakeGmailService(
        {"status-1": _gmail_message("Synced", "s@example.com", "body")}
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)
    await sync_emails(email_account_id=test_email_account_with_emails["id"])

    after = json.loads(await mcp_resources.get_system_status_resource())
    assert after["last_sync_time"] == emails_tools.context["last_sync_time"]
    assert after["total_emails"] == 4


async def test_system_status_resource_error(stack, monkeypatch):
    monkeypatch.setattr(
        mcp_clients, "get_accounts_client", lambda: ExplodingClient("stats down")
    )

    raw = await mcp_resources.get_system_status_resource()

    assert raw == '{"error": "stats down"}'


# ============================================================================
# CLEANUP VERIFICATION
# ============================================================================


async def test_no_side_effects_after_tests(stack):
    """
    Verify that test account and email account cleanup worked properly.

    Adapted: the old version hand-deleted every ``mcp_test%`` row out of the
    real database with raw ORM queries and then asserted the deletes worked.
    There is nothing to delete now — each test gets a fresh ``tmp_path``
    database, so this asserts the stronger property instead: the stack this
    test sees is empty even though every preceding test seeded rows, and it is
    bound to a throwaway file rather than the production url.
    """
    accounts = await list_accounts()
    email_accounts = await list_email_accounts()

    assert accounts == {"status": "success", "accounts": [], "count": 0}
    assert email_accounts == {
        "status": "success",
        "email_accounts": [],
        "count": 0,
    }

    assert str(stack.manager.engine.url) != "sqlite:///gmail_agent.db"
    assert str(stack.manager.engine.url).endswith("test_gmail_agent.db")
