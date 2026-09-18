"""
Pytest configuration and shared fixtures for the Mail Agent test suite.

**The test-wiring convention, post-split.** The monolith (``backend/app.py``,
``backend/dependencies.py``, ``backend/mcp_server.py``, ``backend/controllers/``,
``backend/databases/``, ``backend/utilities/``) is gone. The old pattern this
file used — reaching into ``backend.dependencies.db_manager`` and reassigning its
``engine`` / ``SessionLocal`` in place, which ``PROJECT_MEMORY.md`` used to mark
as load-bearing — **no longer exists and must never come back**: it was a
module-attribute mutation that could not survive process separation (issue B3).

What replaces it:

* Every downstream dependency is a **provider function** (``get_db_manager``,
  ``get_database_client``, ``get_accounts_client``, ``get_vector_db_client``,
  ``get_user_data_client``, ``get_limiter``) resolved at call time, so tests swap
  it through ``app.dependency_overrides`` and nothing else.
* The six services run **in one process with no sockets**: every hop is an
  ``httpx.ASGITransport``. The Gateway's upstream clients are installed through
  its contractual seam, ``backend.gateway.proxy.set_client_factory``.
* The Database service is bound to a ``tmp_path`` SQLite file via a
  ``DatabaseManager`` built here. This fixture module is the *only* sanctioned
  place outside ``backend/services/database/`` that may construct one (R1);
  production code may not. The real ``gmail_agent.db`` is never opened.
* The Vector DB service's ``store.collection`` and ``store.embeddings`` are
  replaced wholesale, so the real ``vector_database/`` directory is never opened
  (addendum C2) and Ollama is never contacted.
* ``client`` is a ``TestClient`` over the **Gateway** (:8000's app), not over a
  monolith. Requests therefore take the real proxy path.

Nothing here reaches the network: no Google API, no OpenAI, no Ollama, no Go sync
server on :8001, no rate limiter on :8002, and no listening port among
8000/8010/8020/8030/8040/8050.

Tests may re-patch any of these targets per-test; a ``@patch`` decorator is
applied after fixture setup, so a per-test override always wins over the default
installed here.

``get_accounts_sync_client`` is deliberately *not* overridden: its only two
callers are ``gmail.get_service`` and ``google_calendar.get_calendar_service``,
both of which are patched wholesale below, and ``httpx.ASGITransport`` cannot
back a synchronous client anyway.

This module contains fixtures only. Tests live in ``tests/unit``,
``tests/contract``, ``tests/e2e`` and ``tests/golden``.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
import requests
from fastapi.testclient import TestClient

# The Vector DB service builds its Chroma client and its Ollama embedding model
# at module import time against the REAL ``vector_database/`` directory, so both
# constructors are patched for the duration of the import (addendum C2). The
# module-scope objects are then replaced wholesale in ``client``.
with patch("langchain_chroma.Chroma"), patch("langchain_ollama.OllamaEmbeddings"):
    from backend.services.vector_db import store
    from backend.services.vector_db.app import app as vector_db_app

from backend.gateway import proxy
from backend.gateway.app import app as gateway_app
from backend.libs.common.http import AsyncServiceClient
from backend.services.accounts import clients as accounts_clients
from backend.services.accounts import google_oauth as accounts_google_oauth
from backend.services.accounts.app import app as accounts_app
from backend.services.accounts.routers import oauth as accounts_oauth
from backend.services.database.app import app as database_app
from backend.services.database.manager import DatabaseManager, get_db_manager
from backend.services.mcp import ask_ollama as mcp_ask_ollama
from backend.services.mcp import clients as mcp_clients
from backend.services.mcp import http_app as mcp_http_app
from backend.services.mcp.http_app import app as mcp_app
from backend.services.user_data import clients as user_data_clients
from backend.services.user_data import gmail, google_calendar
from backend.services.user_data.app import app as user_data_app

# In-process base URLs. The path component must stay empty: the Gateway proxy
# forwards ``request.url.path`` as a RELATIVE url and ``httpx`` merges it onto
# the client's base_url.
ACCOUNTS_BASE_URL = "http://accounts.test"
USER_DATA_BASE_URL = "http://user-data.test"
MCP_BASE_URL = "http://mcp.test"
DATABASE_BASE_URL = "http://database.test"
VECTOR_DB_BASE_URL = "http://vector-db.test"

#: What ``RateLimiterClient.check`` really returns when :8002 is unreachable —
#: it fails open. Tests have always observed exactly this dict, because the rate
#: limiter has never been running during a ``pytest`` run, so reproducing it is
#: what keeps behaviour unchanged (R8).
LIMITER_FAIL_OPEN = {
    "allowed": True,
    "remaining": -1,
    "limit": -1,
    "reset_after_seconds": 0,
    "retry_after_seconds": 0,
}

#: The answer the fake OpenAI client gives ``process_with_openai``. Same value as
#: ``tests/golden/capture.py`` and ``tests/contract/test_mcp_http.py``, so all
#: three suites agree.
OPENAI_ANSWER = "You have no deadlines in the next week."


# ============================================================================
# DATABASE FIXTURES
# ============================================================================

@pytest.fixture(scope="function")
def test_db(tmp_path):
    """The Database service's ``DatabaseManager``, on a fresh SQLite file.

    A temp *file* rather than in-memory SQLite because the manager hands out
    sessions from several connections.

    The sanctioned R1 seam: a fixture MAY construct a ``DatabaseManager`` solely
    to bind the Database app to a throwaway database. Returning the manager
    itself keeps every seeding helper below (and every downstream test) writing
    through the same object it always did.
    """
    manager = DatabaseManager(f"sqlite:///{tmp_path / 'test.db'}")
    database_app.dependency_overrides[get_db_manager] = lambda: manager
    try:
        yield manager
    finally:
        database_app.dependency_overrides.pop(get_db_manager, None)


# ============================================================================
# THE SIX-SERVICE STACK
# ============================================================================

def _asgi_client(base_url: str, target) -> AsyncServiceClient:
    return AsyncServiceClient(base_url, transport=httpx.ASGITransport(app=target))


@contextmanager
def _overridden(app, overrides: dict):
    """Install ``overrides`` into ``app.dependency_overrides`` and take exactly
    those entries out again, leaving any pre-existing ones alone."""
    app.dependency_overrides.update(overrides)
    try:
        yield
    finally:
        for provider in overrides:
            app.dependency_overrides.pop(provider, None)


@contextmanager
def _gateway_clients(clients: dict[str, httpx.AsyncClient]):
    """Point the Gateway's three upstreams at in-process apps.

    ``set_client_factory`` returns the previous factory, which is restored on the
    way out. The proxy never closes what the factory hands it and an
    ``ASGITransport`` owns no socket, so the clients just go out of scope.
    """
    previous = proxy.set_client_factory(clients.__getitem__)
    try:
        yield
    finally:
        proxy.set_client_factory(previous)


class _FailOpenLimiter:
    """``RateLimiterClient`` stand-in returning the fail-open dict verbatim, so
    the ``result["allowed"]`` / ``result["limit"]`` / ``result["remaining"]`` /
    ``result["retry_after_seconds"]`` reads in every ``limiter.check`` block keep
    working without a rate limiter on :8002."""

    def check(self, **kwargs) -> dict:
        return dict(LIMITER_FAIL_OPEN)


def _empty_inbox_gmail_service() -> MagicMock:
    """A Gmail service whose ``messages().list()`` yields no message ids.

    Patching ``gmail.get_service`` is not optional: unpatched it asks Accounts
    for credentials with ``allow_interactive=true``, and with no token row that
    reaches ``InstalledAppFlow.run_local_server`` — a browser on the developer's
    machine (B7).
    """
    service = MagicMock()
    messages = service.users.return_value.messages.return_value
    messages.list.return_value.execute.return_value = {}
    messages.get.return_value.execute.return_value = {}
    return service


def _no_go_server(*args, **kwargs):
    """``requests.post`` to the Go sync server on :8001. Raising
    ``RequestException`` is exactly what a connection refusal does today, so
    ``sync_emails`` takes its Python fallback branch (addendum A1)."""
    raise requests.exceptions.RequestException(
        "the Go sync server on :8001 is not running in tests"
    )


class _OfflineFlow:
    """``google_auth_oauthlib.flow.Flow`` stand-in for the OAuth callback.

    ``from_client_secrets_file`` succeeds (it only reads a local file today) and
    ``fetch_token`` raises, which is precisely what a live token exchange with a
    test authorization code does. The callback's ``except`` branch is therefore
    the one that runs, unchanged.
    """

    redirect_uri: str | None = None

    @classmethod
    def from_client_secrets_file(cls, *args, **kwargs) -> "_OfflineFlow":
        return cls()

    def fetch_token(self, *args, **kwargs):
        raise RuntimeError("OAuth token exchange is disabled in tests")


class _OfflineInstalledAppFlow:
    """``InstalledAppFlow`` stand-in. Interactive re-auth opens a browser and
    blocks (B7), so it is made to fail; ``reauthenticate_user_token_failure``
    swallows the exception and returns ``None``, its documented failure path."""

    @classmethod
    def from_client_secrets_file(cls, *args, **kwargs):
        raise RuntimeError("interactive OAuth is disabled in tests")


class _FakeAsyncOpenAI:
    """``openai.AsyncOpenAI`` stand-in. Never opens a socket.

    ``.env`` carries a real ``OPENAI_API_KEY`` that ``load_dotenv()`` puts into
    ``os.environ``, so an unmocked path here would bill money. Every
    ``chat.completions.create`` answers with a message that requests no tools,
    which is ``process_with_openai``'s "LLM has a final answer" branch.
    ``process_with_openai`` imports ``AsyncOpenAI`` at call time, so the module
    attribute on ``openai`` is the binding it resolves.
    """

    def __init__(self, api_key=None) -> None:
        self.api_key = api_key
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    async def _create(self, **kwargs):
        message = SimpleNamespace(content=OPENAI_ANSWER, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeEmbeddings:
    """``store.embeddings`` stand-in. Records the query text so the fake
    collection can branch on it the way ``mock_vector_db`` does — ``store``
    embeds first and searches by vector, so the text is otherwise lost."""

    def __init__(self) -> None:
        self.last_query = ""

    def embed_query(self, text: str) -> list[float]:
        self.last_query = text
        return [0.1, 0.2, 0.3]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeRawCollection:
    """``collection._collection`` — the private Chroma handle
    ``store_in_vector_db`` reaches through."""

    def __init__(self) -> None:
        self.add_calls: list[dict] = []

    def add(self, **kwargs) -> None:
        self.add_calls.append(kwargs)


class _FakeCollection:
    """``store.collection`` stand-in: an in-memory LangChain ``Chroma``.

    ``similarity_search_by_vector`` ignores ``k``, exactly as
    ``mock_vector_db`` ignores ``top_k``.
    """

    def __init__(self, embeddings: _FakeEmbeddings) -> None:
        self._embeddings = embeddings
        self._collection = _FakeRawCollection()
        self.added: list[tuple] = []

    def add_documents(self, documents, ids=None) -> None:
        self.added.append((documents, ids))

    def similarity_search_by_vector(self, embedding, k=2):
        return _vector_hits(self._embeddings.last_query)


@pytest.fixture
def client(test_db, mock_calendar_service, mock_vector_db, mock_llm):
    """A ``TestClient`` over the Gateway, in front of the whole six-service stack.

    ``test_db`` has already bound the Database service to a temp file, so seeding
    fixtures and HTTP requests see the same database.
    """
    calendar_service, _events_store = mock_calendar_service

    def fake_get_calendar_service(email_account_id=None):
        # Callers unpack a (service, error) tuple.
        return calendar_service, None

    embeddings = _FakeEmbeddings()
    collection = _FakeCollection(embeddings)
    limiter = _FailOpenLimiter()

    database_client = _asgi_client(DATABASE_BASE_URL, database_app)
    vector_db_client = _asgi_client(VECTOR_DB_BASE_URL, vector_db_app)
    accounts_client = _asgi_client(ACCOUNTS_BASE_URL, accounts_app)
    user_data_client = _asgi_client(USER_DATA_BASE_URL, user_data_app)

    with ExitStack() as stack:
        # ---------------- service-to-service wiring ----------------
        stack.enter_context(
            _overridden(
                accounts_app,
                {accounts_clients.get_database_client: lambda: database_client},
            )
        )
        stack.enter_context(
            _overridden(
                user_data_app,
                {
                    user_data_clients.get_database_client: lambda: database_client,
                    user_data_clients.get_vector_db_client: lambda: vector_db_client,
                    user_data_clients.get_accounts_client: lambda: accounts_client,
                    user_data_clients.get_limiter: lambda: limiter,
                },
            )
        )
        stack.enter_context(
            _overridden(
                mcp_app,
                {
                    mcp_clients.get_user_data_client: lambda: user_data_client,
                    mcp_clients.get_accounts_client: lambda: accounts_client,
                    mcp_clients.get_limiter: lambda: limiter,
                },
            )
        )
        stack.enter_context(
            _gateway_clients(
                {
                    proxy.ACCOUNTS: httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=accounts_app),
                        base_url=ACCOUNTS_BASE_URL,
                    ),
                    proxy.USER_DATA: httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=user_data_app),
                        base_url=USER_DATA_BASE_URL,
                    ),
                    proxy.MCP: httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=mcp_app),
                        base_url=MCP_BASE_URL,
                    ),
                }
            )
        )

        # ---------------- externals ----------------
        # Gmail. ``sync`` and the internal email routers both reach it as
        # ``gmail.get_service``, so this one binding covers them.
        stack.enter_context(
            patch.object(
                gmail,
                "get_service",
                MagicMock(return_value=_empty_inbox_gmail_service()),
            )
        )
        # The Go sync server on :8001.
        stack.enter_context(
            patch("backend.services.user_data.sync.requests.post", _no_go_server)
        )
        # Google Calendar. ONE binding, unlike the monolith: the public calendar
        # router, ``moodle.py`` and the CLI all resolve
        # ``google_calendar.get_calendar_service`` as a module attribute.
        stack.enter_context(
            patch.object(
                google_calendar, "get_calendar_service", fake_get_calendar_service
            )
        )
        # Google OAuth: the callback's token exchange and the interactive
        # re-auth flow, the only two paths that would leave the machine.
        stack.enter_context(patch.object(accounts_oauth, "Flow", _OfflineFlow))
        stack.enter_context(
            patch.object(
                accounts_google_oauth, "InstalledAppFlow", _OfflineInstalledAppFlow
            )
        )
        # Chroma + Ollama embeddings, replaced wholesale.
        stack.enter_context(patch.object(store, "collection", collection))
        stack.enter_context(patch.object(store, "embeddings", embeddings))
        # OpenAI, plus belt and braces: even an unpatched client cannot
        # authenticate with this key.
        stack.enter_context(patch("openai.AsyncOpenAI", _FakeAsyncOpenAI))
        stack.enter_context(
            patch.dict(os.environ, {"OPENAI_API_KEY": "tests-not-a-real-key"})
        )
        # ``/api/query``'s answer generator (itself an OpenAI call) and the
        # Ollama branch of ``process_llm_query``, which imports ``slm_response``
        # at call time.
        stack.enter_context(patch.object(mcp_http_app, "llm_response", mock_llm))
        stack.enter_context(patch.object(mcp_ask_ollama, "slm_response", mock_llm))

        with TestClient(gateway_app) as c:
            yield c


# ============================================================================
# SEED DATA
# ============================================================================

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

def _vector_documents() -> tuple[MagicMock, MagicMock]:
    """The two documents ``mock_vector_db`` has always returned.

    Shared with the fake Chroma collection ``client`` installs, so the corpus a
    semantic query retrieves is defined exactly once.
    """
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

    return mock_doc1, mock_doc2


def _vector_hits(query: str) -> list[MagicMock]:
    """``mock_vector_db``'s keyword branching, as a plain function."""
    mock_doc1, mock_doc2 = _vector_documents()

    # Return relevant docs based on query keywords
    if "meeting" in query.lower():
        return [mock_doc1]
    elif "deadline" in query.lower():
        return [mock_doc2]
    return [mock_doc1, mock_doc2]


@pytest.fixture
def mock_vector_db():
    """Mock vector database query function."""
    async def mock_query(query, top_k=3):
        return _vector_hits(query)

    return mock_query


@pytest.fixture
def mock_llm():
    """Mock LLM response function."""
    def mock_response(query):
        return "Based on your emails, you have a meeting tomorrow at 10am."

    return mock_response
