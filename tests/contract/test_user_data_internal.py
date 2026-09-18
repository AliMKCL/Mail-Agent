"""
Contract tests for the User_data service's ``/internal/*`` surface (agent U2).

Spec 3.4's internal table, Phase 5.8. The public ``/api/*`` half lives in
``tests/contract/test_user_data_service.py`` (agent U1); this file never
duplicates it, but it does call one public route on purpose — see
``test_internal_and_public_calendar_shapes_differ``.

How the stack is wired, and why:

* User_data runs in-process over ``httpx.ASGITransport``.
* ``get_database_client()`` and ``get_vector_db_client()`` are overridden with
  ``AsyncServiceClient``s whose transports wrap the **real** Database and Vector
  DB apps, so the wire shapes addendum A1 and A2 pin are exercised rather than
  imagined. The Database service runs against a ``tmp_path`` SQLite file; the
  real ``gmail_agent.db`` is never opened.
* ``store.collection`` and ``store.embeddings`` are monkeypatched, so no request
  reaches Ollama and no handle is opened on the real Chroma directory.
* The Accounts client is a stub. ``gmail.get_service`` and
  ``google_calendar.get_calendar_service`` are patched by name — the single
  binding each now has (Wave 0 hazard 1 is retired), which is also the binding
  ``moodle.py`` resolves through.

No test here contacts the network: no Google API, no Ollama, no Go server on
:8001, no rate limiter on :8002.
"""

from __future__ import annotations

import base64
import calendar
from datetime import datetime
from typing import Any
from unittest import mock

import httpx
import pytest
import requests
from google.oauth2.credentials import Credentials

# Layer 1: no real Chroma client / embedding model is ever constructed.
with mock.patch("langchain_chroma.Chroma"), mock.patch(
    "langchain_ollama.OllamaEmbeddings"
):
    from backend.services.vector_db import store
    from backend.services.vector_db.app import app as vector_db_app

from backend.libs.common.http import AsyncServiceClient
from backend.services.database.app import app as database_app
from backend.services.database.manager import DatabaseManager, get_db_manager
from backend.services.user_data import gmail, google_calendar
from backend.services.user_data.app import app as user_data_app
from backend.services.user_data.clients import (
    get_accounts_client,
    get_database_client,
    get_vector_db_client,
)
from backend.services.user_data.config import CALENDAR_EMAIL_ACCOUNT_ID

BASE_URL = "http://user-data.test"
EMAIL_ACCOUNT_ID = 7
OTHER_EMAIL_ACCOUNT_ID = 8


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDocument:
    """Duck-typed stand-in for a LangChain ``Document``.

    ``store.query_vector_db`` returns whatever the collection hands back, and
    both the Vector DB router and this service only ever read ``page_content``
    and ``metadata``. Using a local class keeps ``langchain_*`` out of the
    import graph.
    """

    def __init__(self, page_content: str, metadata: dict[str, Any]) -> None:
        self.page_content = page_content
        self.metadata = metadata


class FakeRawCollection:
    """Stands in for ``collection._collection`` (the private Chroma handle)."""

    def __init__(self) -> None:
        self.add_calls: list[dict[str, Any]] = []

    def add(self, ids=None, embeddings=None, documents=None, metadatas=None):
        self.add_calls.append(
            {
                "ids": ids,
                "embeddings": embeddings,
                "documents": documents,
                "metadatas": metadatas,
            }
        )


class FakeCollection:
    """Stands in for the module-scope LangChain ``Chroma`` collection."""

    def __init__(self) -> None:
        self.add_documents_calls: list[tuple[list[Any], list[str]]] = []
        self.searches: list[tuple[list[float], int]] = []
        self.hits: list[FakeDocument] = []
        self._collection = FakeRawCollection()

    def add_documents(self, documents, ids=None):
        self.add_documents_calls.append((documents, ids))

    def similarity_search_by_vector(self, embedding, k=None):
        self.searches.append((embedding, k))
        return list(self.hits)


class FakeEmbeddings:
    """Stands in for ``OllamaEmbeddings``. Never hits the network."""

    def embed_query(self, text):
        return [0.1, 0.2, 0.3]


class StubAccounts:
    """Stub for the Accounts service client.

    ``/internal/*`` only ever writes to Accounts, via the credential ``PUT``
    that replaces ``save_calendar_credentials_after_use``. ``get`` exists for
    the one public route this file touches.
    """

    def __init__(self) -> None:
        self.puts: list[tuple[str, Any]] = []
        self.put_fails = False

    async def get(self, path, *, params=None, timeout=None):
        if path.endswith("/primary"):
            return httpx.Response(200, json={"email_account_id": EMAIL_ACCOUNT_ID})
        raise AssertionError(f"unexpected Accounts call: {path}")

    async def put(self, path, *, params=None, json=None, timeout=None):
        self.puts.append((path, json))
        if self.put_fails:
            raise RuntimeError("accounts unavailable")
        return httpx.Response(200, json={"status": "success"})


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


def _gmail_message(
    subject: str, sender: str, body_text: str, body_html: str, snippet: str = "snip"
) -> dict[str, Any]:
    return {
        "headers": {
            "From": sender,
            "To": "me@example.com",
            "Subject": subject,
            "Date": "Mon, 15 Sep 2026 10:30:00 +0000",
        },
        "snippet": snippet,
        "payload": {
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(body_text)}},
                {"mimeType": "text/html", "body": {"data": _b64(body_html)}},
            ]
        },
    }


class _FakeHttp:
    """``service._http`` — the handle ``save_calendar_credentials_after_use``
    reaches through to find possibly-auto-refreshed credentials."""

    def __init__(self) -> None:
        self.credentials = Credentials(
            token="access-token",
            refresh_token="refresh-token",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="client-id",
            client_secret="client-secret",
            scopes=["https://www.googleapis.com/auth/calendar"],
        )


class _CalendarListResource:
    def __init__(self, calendars: list[dict[str, Any]]) -> None:
        self.calendars = calendars

    def list(self):
        return _Executable({"items": self.calendars})


class _EventsResource:
    def __init__(self, owner: FakeCalendarService) -> None:
        self.owner = owner

    def list(self, **kwargs):
        self.owner.listed.append(kwargs)
        if kwargs.get("calendarId") == "primary":
            return _Executable({"items": self.owner.primary_items})
        return _Executable({"items": self.owner.moodle_items})

    def insert(self, calendarId=None, body=None):
        self.owner.inserted.append(body)
        return _Executable({"id": "created-1", "htmlLink": "https://cal/created-1"})

    def get(self, calendarId=None, eventId=None):
        self.owner.fetched.append(eventId)
        return _Executable(dict(self.owner.existing_event))

    def update(self, calendarId=None, eventId=None, body=None):
        self.owner.updated.append((eventId, body))
        return _Executable({"id": eventId, "htmlLink": "https://cal/updated"})

    def delete(self, calendarId=None, eventId=None):
        self.owner.deleted.append(eventId)
        return _Executable({})


class FakeCalendarService:
    """Google Calendar fake covering both the ``events`` and ``calendarList``
    surfaces, so ``moodle.py`` can find its calendar."""

    def __init__(
        self,
        primary_items: list[dict[str, Any]] | None = None,
        moodle_items: list[dict[str, Any]] | None = None,
        calendars: list[dict[str, Any]] | None = None,
    ) -> None:
        self.primary_items = primary_items or []
        self.moodle_items = moodle_items or []
        self.calendars = calendars if calendars is not None else []
        self.listed: list[dict[str, Any]] = []
        self.inserted: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []
        self.fetched: list[str] = []
        self.existing_event: dict[str, Any] = {}
        self._http = _FakeHttp()

    def events(self):
        return _EventsResource(self)

    def calendarList(self):
        return _CalendarListResource(self.calendars)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path) -> DatabaseManager:
    """A ``DatabaseManager`` on a throwaway database, never the real one."""
    return DatabaseManager(f"sqlite:///{tmp_path / 'test_gmail_agent.db'}")


@pytest.fixture
def collection(monkeypatch) -> FakeCollection:
    fake = FakeCollection()
    monkeypatch.setattr(store, "collection", fake)
    monkeypatch.setattr(store, "embeddings", FakeEmbeddings())
    return fake


@pytest.fixture
def accounts() -> StubAccounts:
    return StubAccounts()


@pytest.fixture
def no_go_server(monkeypatch):
    """The MCP sync flavour must never reach the Go server."""
    def boom(*args, **kwargs):  # pragma: no cover - reaching it is the failure
        raise AssertionError("the internal sync route must not call the Go server")

    monkeypatch.setattr(requests, "post", boom)


@pytest.fixture
async def client(manager, collection, accounts):
    """User_data driven in-process, wired to the real Database and Vector DB apps."""
    database_app.dependency_overrides[get_db_manager] = lambda: manager

    database_client = AsyncServiceClient(
        "http://database.test", transport=httpx.ASGITransport(app=database_app)
    )
    vector_client = AsyncServiceClient(
        "http://vector-db.test", transport=httpx.ASGITransport(app=vector_db_app)
    )

    user_data_app.dependency_overrides[get_database_client] = lambda: database_client
    user_data_app.dependency_overrides[get_vector_db_client] = lambda: vector_client
    user_data_app.dependency_overrides[get_accounts_client] = lambda: accounts

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=user_data_app), base_url=BASE_URL
        ) as async_client:
            yield async_client
    finally:
        user_data_app.dependency_overrides.clear()
        database_app.dependency_overrides.clear()


@pytest.fixture
async def database(manager) -> AsyncServiceClient:
    """Direct access to the Database service, for seeding and for assertions."""
    database_app.dependency_overrides[get_db_manager] = lambda: manager
    return AsyncServiceClient(
        "http://database.test", transport=httpx.ASGITransport(app=database_app)
    )


async def _seed_email(
    database: AsyncServiceClient,
    email_account_id: int = EMAIL_ACCOUNT_ID,
    **overrides,
) -> dict:
    payload = {
        "message_id": "msg-1",
        "subject": "Seeded subject",
        "sender": "sender@example.com",
        "recipient": "me@example.com",
        "date_sent": "2026-09-15T10:30:00",
        "snippet": "a snippet",
        "body_text": "plain body",
        "body_html": "<p>html body</p>",
    }
    payload.update(overrides)
    response = await database.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[payload],
    )
    return response.json()


@pytest.fixture
def calendar_service(monkeypatch) -> FakeCalendarService:
    """Patch the single ``get_calendar_service`` binding, which is also the one
    ``moodle.py`` resolves through."""
    service = FakeCalendarService()
    monkeypatch.setattr(
        google_calendar, "get_calendar_service", lambda _id=None: (service, None)
    )
    return service


# ---------------------------------------------------------------------------
# POST /internal/emails/sync  —  mcp_server.sync_emails (:311-372)
# ---------------------------------------------------------------------------


async def test_internal_sync_no_message_ids(client, monkeypatch, no_go_server):
    monkeypatch.setattr(gmail, "get_service", lambda _id: FakeGmailService({}))

    response = await client.post(
        "/internal/emails/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "No new emails found",
        "total_fetched": 0,
        "new_emails": 0,
    }


async def test_internal_sync_saves_and_embeds_cleaned_bodies(
    client, database, collection, monkeypatch, no_go_server
):
    """The MCP sync flavour, end to end.

    It differs from the public ``/api/sync`` in four observable ways, all
    asserted here: ``max_results`` reaches Gmail, the query is bare
    ``in:inbox category:primary`` with **no** ``newer_than:`` window, the Go
    server is never called, and every body is ``clean_email``-ed before it is
    embedded.
    """
    html_body = (
        "<html><body><div>Assignment deadline is 12-Oct-2025.</div>"
        "<div>Submit before the closing date.</div></body></html>"
    )
    raw_body_text = (
        "Assignment deadline is 12-Oct-2025.\n\n\n\nSubmit before the closing date."
    )
    service = FakeGmailService(
        {
            "mcp-1": _gmail_message(
                "Deadline notice", "registry@example.com", raw_body_text, html_body
            )
        }
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    response = await client.post(
        "/internal/emails/sync",
        params={"email_account_id": EMAIL_ACCOUNT_ID, "max_results": 5},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "total_fetched": 1,
        "new_emails": 1,
    }
    # X9: last_sync_time is MCP-process-local and must not appear here.
    assert "last_sync" not in response.json()

    # No newer_than window, and max_results is honoured.
    assert service.list_queries[0]["q"] == "in:inbox category:primary"
    assert service.list_queries[0]["maxResults"] == 5

    # The rows landed in the Database service.
    stored = (
        await database.get("/emails", params={"email_account_id": EMAIL_ACCOUNT_ID})
    ).json()
    assert [row["message_id"] for row in stored] == ["mcp-1"]
    assert stored[0]["subject"] == "Deadline notice"

    # ...and the embedding payload was built from the prepared dicts (C1),
    # with cleaned bodies.
    assert len(collection.add_documents_calls) == 1
    documents, ids = collection.add_documents_calls[0]
    assert ids == ["mcp-1"]
    cleaned = documents[0].page_content
    assert "Assignment deadline is 12-Oct-2025." in cleaned
    assert "Submit before the closing date." in cleaned
    assert "<html>" not in cleaned and "<div>" not in cleaned
    assert cleaned != raw_body_text
    assert documents[0].metadata == {
        "message_id": "mcp-1",
        "sender": "registry@example.com",
        "subject": "Deadline notice",
        "date_sent": "2026-09-15 10:30:00+00:00",
    }
    # /store (the precomputed, Go-only route) is not part of this path.
    assert collection._collection.add_calls == []


async def test_internal_sync_new_emails_is_the_newly_inserted_count(
    client, database, collection, monkeypatch, no_go_server
):
    """``new_emails`` is ``len(save_emails(...))``, i.e. the Database service's
    ``count`` (addendum A1) — so a re-sync of the same mail reports 0.

    This is the one place that wiring is correct. The public ``/api/sync``
    reports mails *fetched* instead; that divergence is deliberate.
    """
    service = FakeGmailService(
        {"dupe-1": _gmail_message("Once", "s@example.com", "body", "<p>body</p>")}
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    first = await client.post(
        "/internal/emails/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )
    second = await client.post(
        "/internal/emails/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert first.json()["new_emails"] == 1
    assert second.json()["total_fetched"] == 1
    assert second.json()["new_emails"] == 0


async def test_internal_sync_returns_the_tool_error_envelope(client, monkeypatch):
    """Failures keep the MCP tool's ``{"status": "error", ...}`` dict at HTTP 200."""
    def explode(_id):
        raise RuntimeError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(gmail, "get_service", explode)

    response = await client.post(
        "/internal/emails/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "error": "invalid_grant: Token has been expired or revoked.",
    }


# ---------------------------------------------------------------------------
# GET /internal/emails  —  get_email_account_emails + mail://inbox/{id}
# ---------------------------------------------------------------------------


async def test_internal_list_emails_passes_through(client, database):
    await _seed_email(database, message_id="msg-1")
    await _seed_email(database, message_id="msg-2", date_sent="2026-09-16T08:00:00")

    response = await client.get(
        "/internal/emails", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    rows = response.json()
    # Newest date_sent first, exactly as the Database service ordered them.
    assert [row["message_id"] for row in rows] == ["msg-2", "msg-1"]
    assert rows[0]["email_account_id"] == EMAIL_ACCOUNT_ID
    assert rows[0]["date_sent"] == "2026-09-16T08:00:00"


async def test_internal_list_emails_honours_limit(client, database):
    await _seed_email(database, message_id="msg-1")
    await _seed_email(database, message_id="msg-2", date_sent="2026-09-16T08:00:00")

    response = await client.get(
        "/internal/emails",
        params={"email_account_id": EMAIL_ACCOUNT_ID, "limit": 1},
    )

    assert [row["message_id"] for row in response.json()] == ["msg-2"]


async def test_internal_list_emails_requires_email_account_id(client):
    response = await client.get("/internal/emails")

    assert response.status_code == 422  # FastAPI: required query parameter


# ---------------------------------------------------------------------------
# GET /internal/emails/by-message-id/{message_id}  —  X3
# ---------------------------------------------------------------------------


async def test_internal_email_by_message_id(client, database):
    await _seed_email(database, message_id="msg-1")

    response = await client.get("/internal/emails/by-message-id/msg-1")

    assert response.status_code == 200
    body = response.json()
    assert body["message_id"] == "msg-1"
    assert body["subject"] == "Seeded subject"
    assert body["email_account_id"] == EMAIL_ACCOUNT_ID


async def test_internal_email_by_message_id_has_no_account_filter(client, database):
    """**X3, preserved.** ``emails.message_id`` has no unique constraint, so a
    message cached by two mailboxes resolves to an arbitrary one of them. The
    route deliberately takes no ``email_account_id`` to disambiguate with.
    """
    await _seed_email(database, email_account_id=EMAIL_ACCOUNT_ID, message_id="dup-1")
    await _seed_email(
        database, email_account_id=OTHER_EMAIL_ACCOUNT_ID, message_id="dup-1"
    )

    response = await client.get("/internal/emails/by-message-id/dup-1")

    assert response.status_code == 200
    body = response.json()
    assert body["message_id"] == "dup-1"
    assert body["email_account_id"] in {EMAIL_ACCOUNT_ID, OTHER_EMAIL_ACCOUNT_ID}


async def test_internal_email_by_message_id_propagates_404(client):
    """The Database service's status and ``detail`` cross the boundary verbatim."""
    response = await client.get("/internal/emails/by-message-id/nope")

    assert response.status_code == 404
    assert response.json()["detail"] == "Email not found"


# ---------------------------------------------------------------------------
# GET /internal/emails/count  and  /internal/emails/stats
# ---------------------------------------------------------------------------


async def test_internal_email_count(client, database):
    await _seed_email(database, message_id="msg-1")
    await _seed_email(database, message_id="msg-2")
    await _seed_email(
        database, email_account_id=OTHER_EMAIL_ACCOUNT_ID, message_id="msg-3"
    )

    response = await client.get(
        "/internal/emails/count", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    assert response.json() == {"count": 2}


async def test_internal_email_count_empty_mailbox(client):
    response = await client.get(
        "/internal/emails/count", params={"email_account_id": 999}
    )

    assert response.json() == {"count": 0}


async def test_internal_email_stats(client, database):
    await _seed_email(database, message_id="msg-1")
    await _seed_email(
        database,
        email_account_id=OTHER_EMAIL_ACCOUNT_ID,
        message_id="msg-2",
        date_sent="2026-09-20T12:00:00",
    )

    response = await client.get("/internal/emails/stats")

    assert response.status_code == 200
    # Counted across every mailbox, not just one — this feeds system://status.
    assert response.json() == {
        "total_emails": 2,
        "latest_email_date": "2026-09-20T12:00:00",
    }


async def test_internal_email_stats_on_empty_database(client):
    response = await client.get("/internal/emails/stats")

    assert response.json() == {"total_emails": 0, "latest_email_date": None}


# ---------------------------------------------------------------------------
# GET /internal/emails/search/gmail  —  search_emails non-semantic branch
# ---------------------------------------------------------------------------


async def test_internal_gmail_search_returns_cached_rows_for_gmail_ids(
    client, database, monkeypatch
):
    await _seed_email(database, message_id="hit-1", subject="Matched")
    await _seed_email(database, message_id="miss-1", subject="Not matched")

    service = FakeGmailService(
        {"hit-1": _gmail_message("Matched", "s@example.com", "b", "<p>b</p>")}
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    response = await client.get(
        "/internal/emails/search/gmail",
        params={"email_account_id": EMAIL_ACCOUNT_ID, "query": "from:s@example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "results": [
            {
                "message_id": "hit-1",
                "sender": "sender@example.com",
                "subject": "Matched",
                "date": "2026-09-15T10:30:00",
                "snippet": "a snippet",
            }
        ],
        "count": 1,
        "search_type": "gmail_api",
    }
    # The query went to Gmail verbatim; the limit became max_results.
    assert service.list_queries[0]["q"] == "from:s@example.com"
    assert service.list_queries[0]["maxResults"] == 10


async def test_internal_gmail_search_limit_caps_both_sides(
    client, database, monkeypatch
):
    for index in range(3):
        await _seed_email(database, message_id=f"m-{index}")

    service = FakeGmailService(
        {
            f"m-{index}": _gmail_message("s", "s@example.com", "b", "<p>b</p>")
            for index in range(3)
        }
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    response = await client.get(
        "/internal/emails/search/gmail",
        params={
            "email_account_id": EMAIL_ACCOUNT_ID,
            "query": "anything",
            "limit": 2,
        },
    )

    body = response.json()
    assert body["count"] == 2
    assert len(body["results"]) == 2
    assert service.list_queries[0]["maxResults"] == 2


async def test_internal_gmail_search_ignores_uncached_ids(client, monkeypatch):
    """Gmail knows the message but we never synced it, so no row comes back."""
    service = FakeGmailService(
        {"never-synced": _gmail_message("s", "s@example.com", "b", "<p>b</p>")}
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    response = await client.get(
        "/internal/emails/search/gmail",
        params={"email_account_id": EMAIL_ACCOUNT_ID, "query": "anything"},
    )

    assert response.json() == {
        "status": "success",
        "results": [],
        "count": 0,
        "search_type": "gmail_api",
    }


async def test_internal_gmail_search_requires_email_account_id(client):
    """The error string is the tool's, verbatim, at HTTP 200."""
    response = await client.get(
        "/internal/emails/search/gmail", params={"query": "anything"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "error": "email_account_id required for Gmail API search",
    }


async def test_internal_gmail_search_error_envelope(client, monkeypatch):
    def explode(_id):
        raise RuntimeError("Authentication required")

    monkeypatch.setattr(gmail, "get_service", explode)

    response = await client.get(
        "/internal/emails/search/gmail",
        params={"email_account_id": EMAIL_ACCOUNT_ID, "query": "anything"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "error", "error": "Authentication required"}


# ---------------------------------------------------------------------------
# GET /internal/emails/search/semantic
# ---------------------------------------------------------------------------


def _hit(message_id: str, body: str = "a body") -> FakeDocument:
    return FakeDocument(
        page_content=body,
        metadata={
            "message_id": message_id,
            "sender": "sender@example.com",
            "subject": f"Subject {message_id}",
            "date_sent": "2026-09-15 10:30:00+00:00",
        },
    )


async def test_internal_semantic_search_unfiltered(client, collection):
    """With no ``email_account_id`` the hits pass through untouched — this is
    the form MCP's ``/api/query`` consumes (R4: MCP never calls Vector DB)."""
    collection.hits = [_hit("sem-1", "deadline is friday"), _hit("sem-2")]

    response = await client.get(
        "/internal/emails/search/semantic",
        params={"query": "when is the deadline", "top_k": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["search_type"] == "semantic"
    assert body["count"] == 2
    assert [row["message_id"] for row in body["results"]] == ["sem-1", "sem-2"]
    assert body["results"][0] == {
        "message_id": "sem-1",
        "sender": "sender@example.com",
        "subject": "Subject sem-1",
        "date": "2026-09-15 10:30:00+00:00",
        "snippet": "deadline is friday",
    }
    # The VectorDocDTO form /api/query retrieves: full page_content, untouched
    # metadata, in hit order.
    assert body["documents"] == [
        {
            "page_content": "deadline is friday",
            "metadata": {
                "message_id": "sem-1",
                "sender": "sender@example.com",
                "subject": "Subject sem-1",
                "date_sent": "2026-09-15 10:30:00+00:00",
            },
        },
        {
            "page_content": "a body",
            "metadata": {
                "message_id": "sem-2",
                "sender": "sender@example.com",
                "subject": "Subject sem-2",
                "date_sent": "2026-09-15 10:30:00+00:00",
            },
        },
    ]

    # top_k reached Chroma as k.
    assert collection.searches[0][1] == 3


async def test_internal_semantic_search_truncates_long_snippets(client, collection):
    long_body = "x" * 250
    collection.hits = [_hit("sem-long", long_body)]

    response = await client.get(
        "/internal/emails/search/semantic", params={"query": "anything"}
    )

    snippet = response.json()["results"][0]["snippet"]
    assert len(snippet) == 200
    # ...while the document keeps the full text for /api/query's context.
    assert response.json()["documents"][0]["page_content"] == long_body


async def test_internal_semantic_search_filters_by_email_account(
    client, database, collection
):
    """The account filter uses the Database service's batch ``by-message-ids``
    route in place of the original's per-result loop. Addendum A1 documents it
    as running the very same per-id ``.first()`` queries, so the substitution
    is behavior-neutral.
    """
    await _seed_email(database, email_account_id=EMAIL_ACCOUNT_ID, message_id="mine-1")
    await _seed_email(
        database, email_account_id=OTHER_EMAIL_ACCOUNT_ID, message_id="theirs-1"
    )
    collection.hits = [_hit("mine-1"), _hit("theirs-1"), _hit("uncached-1")]

    response = await client.get(
        "/internal/emails/search/semantic",
        params={"query": "anything", "email_account_id": EMAIL_ACCOUNT_ID},
    )

    body = response.json()
    assert [row["message_id"] for row in body["results"]] == ["mine-1"]
    assert body["count"] == 1
    assert [doc["metadata"]["message_id"] for doc in body["documents"]] == ["mine-1"]


async def test_internal_semantic_search_filter_drops_everything_uncached(
    client, collection
):
    collection.hits = [_hit("ghost-1"), _hit("ghost-2")]

    response = await client.get(
        "/internal/emails/search/semantic",
        params={"query": "anything", "email_account_id": EMAIL_ACCOUNT_ID},
    )

    assert response.json() == {
        "status": "success",
        "results": [],
        "count": 0,
        "search_type": "semantic",
        "documents": [],
    }


async def test_internal_semantic_search_no_hits(client, collection):
    collection.hits = []

    response = await client.get(
        "/internal/emails/search/semantic", params={"query": "anything"}
    )

    assert response.json() == {
        "status": "success",
        "results": [],
        "count": 0,
        "search_type": "semantic",
        "documents": [],
    }


# ---------------------------------------------------------------------------
# POST /internal/calendar/events  —  create_calendar_event (:457-538)
# ---------------------------------------------------------------------------


async def test_internal_create_event_timed(client, calendar_service, accounts):
    response = await client.post(
        "/internal/calendar/events",
        json={
            "title": "Team Meeting",
            "date": "2026-03-15",
            "time": "10:00 AM",
            "description": "Discuss project updates",
            "category": "Academic",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "event_id": "created-1",
        "event_link": "https://cal/created-1",
        "title": "Team Meeting",
        "date": "2026-03-15",
    }
    assert calendar_service.inserted == [
        {
            "summary": "Team Meeting",
            "description": "Discuss project updates",
            "start": {"dateTime": "2026-03-15T10:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-15T11:00:00", "timeZone": "UTC"},
            "extendedProperties": {"private": {"category": "Academic"}},
        }
    ]
    # save_calendar_credentials_after_use routed through Accounts (R2).
    assert accounts.puts
    path, payload = accounts.puts[0]
    assert path == (
        f"/internal/email-accounts/{CALENDAR_EMAIL_ACCOUNT_ID}/credentials"
    )
    assert payload["refresh_token"] == "refresh-token"


async def test_internal_create_event_all_day(client, calendar_service):
    response = await client.post(
        "/internal/calendar/events",
        json={"title": "Assignment Due", "date": "2026-03-20"},
    )

    assert response.json()["status"] == "success"
    assert calendar_service.inserted == [
        {
            "summary": "Assignment Due",
            "description": "",
            "start": {"date": "2026-03-20"},
            "end": {"date": "2026-03-20"},
        }
    ]


async def test_internal_create_event_unparseable_time_falls_back_to_all_day(
    client, calendar_service
):
    response = await client.post(
        "/internal/calendar/events",
        json={"title": "Fuzzy", "date": "2026-03-20", "time": "sometime"},
    )

    assert response.json()["status"] == "success"
    assert calendar_service.inserted[0]["start"] == {"date": "2026-03-20"}
    assert calendar_service.inserted[0]["end"] == {"date": "2026-03-20"}


async def test_internal_create_event_uses_account_one_regardless_of_caller(
    client, monkeypatch, accounts
):
    """**X4, preserved.** Every ``/internal/calendar/*`` route is hardwired to
    ``settings.CALENDAR_EMAIL_ACCOUNT_ID``; the caller cannot choose."""
    assert CALENDAR_EMAIL_ACCOUNT_ID == 1

    seen: list[int] = []
    service = FakeCalendarService()

    def record(email_account_id=None):
        seen.append(email_account_id)
        return service, None

    monkeypatch.setattr(google_calendar, "get_calendar_service", record)

    await client.post(
        "/internal/calendar/events",
        json={"title": "Anything", "date": "2026-03-20"},
    )

    assert seen == [CALENDAR_EMAIL_ACCOUNT_ID]


async def test_internal_create_event_service_failure(client, monkeypatch):
    monkeypatch.setattr(
        google_calendar,
        "get_calendar_service",
        lambda _id=None: (None, "Authentication required - Calendar scope missing"),
    )

    response = await client.post(
        "/internal/calendar/events",
        json={"title": "Anything", "date": "2026-03-20"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "error": "Authentication required - Calendar scope missing",
    }


async def test_internal_credential_save_failure_never_fails_the_operation(
    client, calendar_service, accounts
):
    """The helper's swallow-everything ``except`` is load-bearing."""
    accounts.put_fails = True

    response = await client.post(
        "/internal/calendar/events",
        json={"title": "Anything", "date": "2026-03-20"},
    )

    assert response.json()["status"] == "success"
    assert accounts.puts  # it was attempted


# ---------------------------------------------------------------------------
# PATCH /internal/calendar/events/{event_id}  —  update_calendar_event
# ---------------------------------------------------------------------------


async def test_internal_update_event(client, calendar_service, accounts):
    calendar_service.existing_event = {
        "id": "evt-9",
        "summary": "Old title",
        "description": "Old description",
        "start": {"date": "2026-09-19"},
        "end": {"date": "2026-09-19"},
    }

    response = await client.patch(
        "/internal/calendar/events/evt-9",
        json={
            "title": "New title",
            "date": "2026-09-20",
            "time": "09:00 AM",
            "description": "New description",
            "category": "Deadline",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "event_id": "evt-9",
        "event_link": "https://cal/updated",
    }
    event_id, body = calendar_service.updated[0]
    assert event_id == "evt-9"
    assert body["summary"] == "New title"
    assert body["description"] == "New description"
    assert body["start"] == {"dateTime": "2026-09-20T09:00:00", "timeZone": "UTC"}
    assert body["end"] == {"dateTime": "2026-09-20T10:00:00", "timeZone": "UTC"}
    assert body["extendedProperties"] == {"private": {"category": "Deadline"}}
    assert accounts.puts  # credentials saved after the call


async def test_internal_update_event_empty_description_is_applied(
    client, calendar_service
):
    """``description is not None`` rather than truthiness: ``""`` clears it."""
    calendar_service.existing_event = {
        "id": "evt-9",
        "summary": "Title",
        "description": "Old description",
        "start": {"date": "2026-09-19"},
        "end": {"date": "2026-09-19"},
    }

    await client.patch("/internal/calendar/events/evt-9", json={"description": ""})

    _, body = calendar_service.updated[0]
    assert body["description"] == ""


async def test_internal_update_event_date_only_becomes_all_day(
    client, calendar_service
):
    calendar_service.existing_event = {
        "id": "evt-9",
        "summary": "Title",
        "start": {"dateTime": "2026-09-19T08:00:00Z"},
        "end": {"dateTime": "2026-09-19T09:00:00Z"},
    }

    await client.patch("/internal/calendar/events/evt-9", json={"date": "2026-09-21"})

    _, body = calendar_service.updated[0]
    assert body["start"] == {"date": "2026-09-21"}
    assert body["end"] == {"date": "2026-09-21"}


async def test_internal_update_event_time_only_reuses_the_existing_date(
    client, calendar_service
):
    """With no ``date`` the existing event's date is sliced out of whichever of
    ``date`` / ``dateTime`` it carries — ``event['start'].get('date', ...[:10])``."""
    calendar_service.existing_event = {
        "id": "evt-9",
        "summary": "Title",
        "start": {"dateTime": "2026-09-19T08:00:00Z"},
        "end": {"dateTime": "2026-09-19T09:00:00Z"},
    }

    await client.patch("/internal/calendar/events/evt-9", json={"time": "02:30 PM"})

    _, body = calendar_service.updated[0]
    assert body["start"] == {"dateTime": "2026-09-19T14:30:00", "timeZone": "UTC"}


async def test_internal_update_event_service_failure(client, monkeypatch):
    monkeypatch.setattr(
        google_calendar,
        "get_calendar_service",
        lambda _id=None: (None, "Authentication required"),
    )

    response = await client.patch(
        "/internal/calendar/events/evt-9", json={"title": "New"}
    )

    assert response.json() == {"status": "error", "error": "Authentication required"}


# ---------------------------------------------------------------------------
# DELETE /internal/calendar/events/{event_id}
# ---------------------------------------------------------------------------


async def test_internal_delete_event(client, calendar_service, accounts):
    response = await client.delete("/internal/calendar/events/evt-9")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Event evt-9 deleted successfully",
    }
    assert calendar_service.deleted == ["evt-9"]
    assert accounts.puts  # credentials saved after the call


async def test_internal_delete_event_service_failure(client, monkeypatch):
    monkeypatch.setattr(
        google_calendar,
        "get_calendar_service",
        lambda _id=None: (None, "Authentication required"),
    )

    response = await client.delete("/internal/calendar/events/evt-9")

    assert response.json() == {"status": "error", "error": "Authentication required"}
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /internal/calendar/events  —  the Moodle merge (P2) and the flat shape
# ---------------------------------------------------------------------------


PRIMARY_ITEMS = [
    {
        "id": "p-late",
        "summary": "Late primary event",
        "start": {"dateTime": "2026-09-25T09:00:00Z"},
        "end": {"dateTime": "2026-09-25T10:00:00Z"},
        "description": "later",
        "htmlLink": "https://cal/p-late",
        "extendedProperties": {"private": {"category": "Career"}},
    },
    {
        "id": "p-early",
        "summary": "Early primary event",
        "start": {"dateTime": "2026-09-20T09:00:00Z"},
        "end": {"dateTime": "2026-09-20T10:00:00Z"},
        "htmlLink": "https://cal/p-early",
    },
]

MOODLE_ITEMS = [
    {
        "id": "m-1",
        "summary": "Coursework deadline",
        "start": {"dateTime": "2026-09-22T12:00:00Z"},
        "end": {"dateTime": "2026-09-22T13:00:00Z"},
        "description": "submit on Moodle",
    }
]

MOODLE_CALENDARS = [
    {"id": "personal@example.com", "summary": "Personal"},
    {"id": "moodle@example.com", "summary": "Moodle Calendar"},
]


@pytest.fixture
def merged_calendar_service(monkeypatch) -> FakeCalendarService:
    service = FakeCalendarService(
        primary_items=list(PRIMARY_ITEMS),
        moodle_items=list(MOODLE_ITEMS),
        calendars=list(MOODLE_CALENDARS),
    )
    monkeypatch.setattr(
        google_calendar, "get_calendar_service", lambda _id=None: (service, None)
    )
    return service


async def test_internal_calendar_events_merge_moodle(
    client, merged_calendar_service, accounts
):
    """Regression guard for approved fix **P2**.

    ``mcp_server.get_calendar_events`` called
    ``get_moodle_events_for_api(email_account_id=...)`` while the parameter was
    still named ``user_id``. Every call raised ``TypeError``, the surrounding
    ``except`` logged it as a warning and continued, and MCP has therefore
    **never** returned a single Moodle event. The rename makes the merge real,
    so the Moodle event must now be present.
    """
    response = await client.get(
        "/internal/calendar/events",
        params={"start_date": "2026-09-01", "end_date": "2026-09-30"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["sources"] == ["primary", "moodle"]
    assert body["primary_count"] == 2
    assert body["count"] == 3

    # Flat, sorted by start, with the Moodle event interleaved between the two
    # primary ones — not appended, not grouped.
    assert isinstance(body["events"], list)
    assert [event["id"] for event in body["events"]] == ["p-early", "m-1", "p-late"]

    moodle_event = body["events"][1]
    assert moodle_event == {
        "id": "m-1",
        "title": "Coursework deadline",
        "start": "2026-09-22T12:00:00Z",
        "end": "2026-09-22T13:00:00Z",
        "description": "submit on Moodle",
        "link": "",
        "category": "Moodle",
        "source": "moodle",
    }
    assert body["events"][0]["source"] == "primary"
    assert body["events"][2]["category"] == "Career"
    assert accounts.puts  # credentials saved after the calls


async def test_internal_calendar_events_end_date_is_made_exclusive(
    client, merged_calendar_service
):
    """Google's ``timeMax`` is exclusive, so an explicit ``end_date`` gets +1
    day — otherwise events ON the end date would be dropped."""
    await client.get(
        "/internal/calendar/events",
        params={"start_date": "2026-09-01", "end_date": "2026-09-30"},
    )

    primary_call = merged_calendar_service.listed[0]
    assert primary_call["calendarId"] == "primary"
    assert primary_call["timeMin"] == "2026-09-01T00:00:00Z"
    assert primary_call["timeMax"] == "2026-10-01T00:00:00Z"
    assert primary_call["singleEvents"] is True
    assert primary_call["orderBy"] == "startTime"


async def test_internal_calendar_events_default_window_is_the_current_month(
    client, merged_calendar_service
):
    """No dates supplied -> first of this month 00:00:00 through the last day
    at 23:59:59.

    ``.replace(hour=..., minute=..., second=...)`` leaves ``microsecond``
    alone, so both bounds carry ``datetime.now()``'s microseconds into the
    ``timeMin``/``timeMax`` strings. That is today's behavior and is preserved.
    """
    await client.get("/internal/calendar/events")

    now = datetime.now()
    last_day = calendar.monthrange(now.year, now.month)[1]
    primary_call = merged_calendar_service.listed[0]

    assert primary_call["timeMin"].startswith(now.strftime("%Y-%m-01T00:00:00"))
    assert primary_call["timeMax"].startswith(
        now.strftime(f"%Y-%m-{last_day:02d}T23:59:59")
    )
    assert primary_call["timeMin"].endswith("Z")
    assert primary_call["timeMax"].endswith("Z")


async def test_internal_calendar_events_survive_a_moodle_failure(
    client, monkeypatch, calendar_service
):
    """A Moodle failure is logged and skipped — it never fails the request.

    ``calendar_service`` has an empty ``calendars`` list, so
    ``find_moodle_calendar_id`` returns ``None`` and
    ``get_moodle_events_for_api`` answers ``{"error": ..., "events": {}}``.
    """
    calendar_service.primary_items = list(PRIMARY_ITEMS)

    response = await client.get("/internal/calendar/events")

    body = response.json()
    assert body["status"] == "success"
    assert body["count"] == 2
    assert body["primary_count"] == 2
    assert [event["source"] for event in body["events"]] == ["primary", "primary"]


async def test_internal_calendar_events_service_failure(client, monkeypatch):
    monkeypatch.setattr(
        google_calendar,
        "get_calendar_service",
        lambda _id=None: (None, "Authentication required"),
    )

    response = await client.get("/internal/calendar/events")

    assert response.json() == {"status": "error", "error": "Authentication required"}


async def test_internal_and_public_calendar_shapes_differ(
    client, merged_calendar_service
):
    """Spec 3.4's boxed warning, asserted.

    The internal and public calendar routes were **not** merged: the internal
    one returns a flat, time-sorted list with Moodle merged in, the public one
    returns a mapping keyed by date with no Moodle at all. If a later change
    collapses them into one implementation, this test fails.
    """
    internal = (
        await client.get(
            "/internal/calendar/events",
            params={"start_date": "2026-09-01", "end_date": "2026-09-30"},
        )
    ).json()
    public = (
        await client.get(
            "/api/calendar/events",
            params={
                "email_account_id": EMAIL_ACCOUNT_ID,
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
            },
        )
    ).json()

    # Shape: flat list vs date-keyed mapping.
    assert isinstance(internal["events"], list)
    assert isinstance(public["events"], dict)
    assert all(
        isinstance(value, list) for value in public["events"].values()
    )

    # Sorting: the internal list is ordered by start time.
    starts = [event["start"] for event in internal["events"]]
    assert starts == sorted(starts)

    # Moodle: merged in internally, absent publicly.
    internal_sources = {event["source"] for event in internal["events"]}
    assert "moodle" in internal_sources
    public_events = [
        event for events in public["events"].values() for event in events
    ]
    assert public_events  # the public route really did return the primary events
    assert all(event.get("source") != "moodle" for event in public_events)
    assert all(event.get("category") != "Moodle" for event in public_events)

    # The internal route carries a Moodle event the public one does not.
    assert "m-1" in {event["id"] for event in internal["events"]}
    assert "m-1" not in {event["id"] for event in public_events}


# ---------------------------------------------------------------------------
# GET /internal/calendar/events/{event_id}  —  calendar://event/{id}
# ---------------------------------------------------------------------------


async def test_internal_get_single_event(client, calendar_service):
    calendar_service.existing_event = {
        "id": "evt-42",
        "summary": "Exam",
        "start": {"dateTime": "2026-09-22T09:00:00Z"},
        "end": {"dateTime": "2026-09-22T11:00:00Z"},
        "description": "Bring a calculator",
        "htmlLink": "https://cal/evt-42",
        "created": "2026-08-01T10:00:00Z",
        "updated": "2026-08-02T10:00:00Z",
        "extendedProperties": {"private": {"category": "Academic"}},
    }

    response = await client.get("/internal/calendar/events/evt-42")

    assert response.status_code == 200
    assert response.json() == {
        "id": "evt-42",
        "title": "Exam",
        "start": "2026-09-22T09:00:00Z",
        "end": "2026-09-22T11:00:00Z",
        "description": "Bring a calculator",
        "link": "https://cal/evt-42",
        "created": "2026-08-01T10:00:00Z",
        "updated": "2026-08-02T10:00:00Z",
        "category": "Academic",
    }
    assert calendar_service.fetched == ["evt-42"]


async def test_internal_get_single_event_all_day_and_untitled(
    client, calendar_service
):
    calendar_service.existing_event = {
        "id": "evt-43",
        "start": {"date": "2026-09-22"},
        "end": {"date": "2026-09-22"},
    }

    response = await client.get("/internal/calendar/events/evt-43")

    assert response.json() == {
        "id": "evt-43",
        "title": "No Title",
        "start": "2026-09-22",
        "end": "2026-09-22",
        "description": "",
        "link": None,
        "created": None,
        "updated": None,
    }


async def test_internal_get_single_event_service_failure_uses_the_resource_envelope(
    client, monkeypatch
):
    """The resource returned a bare ``{"error": ...}`` — no ``status`` key.
    That asymmetry with the tools is deliberate and preserved."""
    monkeypatch.setattr(
        google_calendar,
        "get_calendar_service",
        lambda _id=None: (None, "Authentication required"),
    )

    response = await client.get("/internal/calendar/events/evt-42")

    assert response.status_code == 200
    assert response.json() == {"error": "Authentication required"}


# ---------------------------------------------------------------------------
# Route inventory (addendum B1: enumerate via openapi(), not app.routes)
# ---------------------------------------------------------------------------


def test_every_internal_route_of_spec_3_4_is_registered():
    paths = user_data_app.openapi()["paths"]

    assert {"post"} <= set(paths["/internal/emails/sync"])
    assert {"get"} <= set(paths["/internal/emails"])
    assert {"get"} <= set(paths["/internal/emails/by-message-id/{message_id}"])
    assert {"get"} <= set(paths["/internal/emails/count"])
    assert {"get"} <= set(paths["/internal/emails/stats"])
    assert {"get"} <= set(paths["/internal/emails/search/gmail"])
    assert {"get"} <= set(paths["/internal/emails/search/semantic"])
    assert {"post", "get"} <= set(paths["/internal/calendar/events"])
    assert {"patch", "delete", "get"} <= set(
        paths["/internal/calendar/events/{event_id}"]
    )


def test_internal_routers_import_no_forbidden_module():
    """R1/R4/R5: no SQLAlchemy, no Chroma, no LangChain, no OpenAI, no OAuth
    flow inside the User_data internal surface."""
    import backend.services.user_data.routers.internal_calendar as internal_calendar
    import backend.services.user_data.routers.internal_emails as internal_emails

    forbidden = ("sqlalchemy", "chromadb", "langchain", "openai", "google_auth_oauthlib")
    for module in (internal_emails, internal_calendar):
        source = open(module.__file__, encoding="utf-8").read()
        for line in source.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            assert not any(name in stripped for name in forbidden), (
                f"{module.__name__}: {stripped}"
            )
        assert "DatabaseManager" not in source
