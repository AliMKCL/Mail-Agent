"""
Contract tests for the User_data service (:8020) — Spec 3.4 public half / Phase 5.10.

The User_data app is driven in-process over ``httpx.ASGITransport``. Its two
data dependencies are the **real** services, also in-process:

* the Database service, pointed at a throwaway SQLite file under ``tmp_path``;
* the Vector DB service, with ``store.collection`` and ``store.embeddings``
  monkeypatched, so nothing reaches Ollama or the real ``vector_database/``
  directory (hazard B2).

The Accounts service is stubbed — it is another agent's tree and User_data only
consumes four of its routes. Gmail and Google Calendar are patched at their
single defining modules (``gmail.get_service``,
``google_calendar.get_calendar_service``); both are reached as module attributes
by every caller, which is what retires Wave 0 hazard 1. The rate limiter always
allows unless a test overrides it.

Nothing here touches the network or the real ``gmail_agent.db``.

The most valuable test in this file is
``test_sync_fallback_embeds_cleaned_bodies``: it is the regression test for the
approved fix **S1**. The monolith's Python fallback did
``await embed_and_store(db.save_emails(...))``, which could never work
(addendum C1), so that branch always 500ed. It now builds the embedding payload
from ``email_data`` with cleaned bodies.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from unittest import mock

import httpx
import pytest
import requests

# Layer 1: no real Chroma client / embedding model is ever constructed.
with mock.patch("langchain_chroma.Chroma"), mock.patch(
    "langchain_ollama.OllamaEmbeddings"
):
    from backend.services.vector_db import store
    from backend.services.vector_db.app import app as vector_db_app

from backend.libs.common.http import AsyncServiceClient
from backend.services.database.app import app as database_app
from backend.services.database.manager import DatabaseManager, get_db_manager
from backend.services.user_data import gmail, google_calendar, sync
from backend.services.user_data.app import app as user_data_app
from backend.services.user_data.clients import (
    get_accounts_client,
    get_database_client,
    get_limiter,
    get_vector_db_client,
)

BASE_URL = "http://user-data.test"
EMAIL_ACCOUNT_ID = 7
PRIMARY_EMAIL_ACCOUNT_ID = 9


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


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
        self._collection = FakeRawCollection()

    def add_documents(self, documents, ids=None):
        self.add_documents_calls.append((documents, ids))


class FakeEmbeddings:
    """Stands in for ``OllamaEmbeddings``. Never hits the network."""

    def embed_query(self, text):  # pragma: no cover - /query is not exercised here
        return [0.1, 0.2, 0.3]


class FakeLimiter:
    """``RateLimiterClient`` stand-in. Records calls; allows by default."""

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[dict[str, Any]] = []

    def check(self, scope, identifier, endpoint, tokens=1, capacity=None, refill_rate=None):
        self.calls.append(
            {
                "scope": scope,
                "identifier": identifier,
                "endpoint": endpoint,
                "tokens": tokens,
                "capacity": capacity,
                "refill_rate": refill_rate,
            }
        )
        if self.allowed:
            return {
                "allowed": True,
                "remaining": 9,
                "limit": 10,
                "reset_after_seconds": 0,
                "retry_after_seconds": 0,
            }
        return {
            "allowed": False,
            "remaining": 0,
            "limit": 10,
            "reset_after_seconds": 3600,
            "retry_after_seconds": 42,
        }


class StubAccounts:
    """Stub for the Accounts service client.

    Implements only the async ``get`` surface User_data uses:
    ``/internal/email-accounts/{id}/primary`` and ``/internal/oauth/auth-url``.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.primary_id = PRIMARY_EMAIL_ACCOUNT_ID
        self.auth_url: str | None = "https://accounts.google.com/o/oauth2/auth?stub=1"
        self.primary_status = 200

    async def get(self, path, *, params=None, timeout=None):
        self.calls.append((path, dict(params or {})))
        if path.endswith("/primary"):
            if self.primary_status != 200:
                raise RuntimeError(f"accounts returned {self.primary_status}")
            return httpx.Response(200, json={"email_account_id": self.primary_id})
        if path == "/internal/oauth/auth-url":
            return httpx.Response(
                200, json={"auth_url": self.auth_url, "state": "stub-state"}
            )
        raise AssertionError(f"unexpected Accounts call: {path}")


class FakeGmailService:
    """A googleapiclient-shaped fake for the handful of calls we make.

    ``list_message_ids``, ``get_message_metadata`` and ``get_message_body`` run
    for real against it, so ``prepare_email_data`` (and therefore the S1
    payload) is exercised end to end rather than mocked out.
    """

    def __init__(self, mailbox: dict[str, dict[str, Any]]) -> None:
        # NOT named ``messages``: that would shadow the ``messages()`` step of the
        # googleapiclient call chain below.
        self.mailbox = mailbox
        self.list_queries: list[dict[str, Any]] = []

    # -- googleapiclient call chain ----------------------------------------
    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId=None, q=None, labelIds=None, maxResults=None, pageToken=None):
        self.list_queries.append({"q": q, "maxResults": maxResults})
        return _Executable(
            {"messages": [{"id": msg_id} for msg_id in self.mailbox]}
        )

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


class _Executable:
    def __init__(self, result: Any) -> None:
        self.result = result

    def execute(self):
        return self.result


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


class FakeCalendarService:
    """Google Calendar service fake: records bodies, replays canned results."""

    def __init__(self) -> None:
        self.listed: list[dict[str, Any]] = []
        self.items: list[dict[str, Any]] = []
        self.inserted: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []
        self.existing_event: dict[str, Any] = {}

    def events(self):
        return self

    def list(self, **kwargs):
        self.listed.append(kwargs)
        return _Executable({"items": self.items})

    def insert(self, calendarId=None, body=None):
        self.inserted.append(body)
        return _Executable({"id": "created-1", "htmlLink": "https://cal/created-1"})

    def get(self, calendarId=None, eventId=None):
        return _Executable(dict(self.existing_event))

    def update(self, calendarId=None, eventId=None, body=None):
        self.updated.append((eventId, body))
        return _Executable({"id": eventId, "htmlLink": "https://cal/updated"})

    def delete(self, calendarId=None, eventId=None):
        self.deleted.append(eventId)
        return _Executable({})


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
def limiter() -> FakeLimiter:
    return FakeLimiter()


@pytest.fixture
def accounts() -> StubAccounts:
    return StubAccounts()


@pytest.fixture
async def client(manager, collection, limiter, accounts):
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
    user_data_app.dependency_overrides[get_limiter] = lambda: limiter

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


async def _seed_email(database: AsyncServiceClient, **overrides) -> dict:
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
        params={"email_account_id": EMAIL_ACCOUNT_ID},
        json=[payload],
    )
    return response.json()


# ---------------------------------------------------------------------------
# GET /api/emails
# ---------------------------------------------------------------------------


async def test_get_emails_formats_rows(client, database, limiter):
    await _seed_email(database)

    response = await client.get(
        "/api/emails", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    row = body[0]
    assert row["id"] == "msg-1"
    assert row["subject"] == "Seeded subject"
    assert row["sender"] == "sender@example.com"
    assert row["recipient"] == "me@example.com"
    assert row["date_sent"] == "2026-09-15T10:30:00"
    assert row["snippet"] == "a snippet"
    assert row["body_text"] == "plain body"
    assert row["body_html"] == "<p>html body</p>"
    assert row["created_at"]  # always present, never None in practice
    assert set(row) == {
        "id",
        "subject",
        "sender",
        "recipient",
        "date_sent",
        "snippet",
        "body_text",
        "body_html",
        "created_at",
    }

    # R8: the limiter block moved verbatim.
    assert limiter.calls == [
        {
            "scope": "global",
            "identifier": "all",
            "endpoint": "api/emails",
            "tokens": 1,
            "capacity": 10,
            "refill_rate": 10,
        }
    ]


async def test_get_emails_null_columns_use_placeholders(client, database):
    """``or "No Subject"`` / ``or "Unknown"`` / ``or ""`` are preserved verbatim."""
    await _seed_email(
        database,
        message_id="msg-null",
        subject=None,
        sender=None,
        recipient=None,
        date_sent=None,
        snippet=None,
        body_text=None,
        body_html=None,
    )

    response = await client.get(
        "/api/emails", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    row = response.json()[0]
    assert row["subject"] == "No Subject"
    assert row["sender"] == "Unknown"
    assert row["recipient"] == "Unknown"
    assert row["date_sent"] is None
    assert row["snippet"] == ""
    assert row["body_text"] == ""
    assert row["body_html"] == ""


async def test_get_emails_requires_email_account_id(client):
    response = await client.get("/api/emails")

    assert response.status_code == 400
    assert response.json()["detail"] == "email_account_id parameter is required"


async def test_get_emails_rate_limited(client, limiter):
    limiter.allowed = False

    response = await client.get(
        "/api/emails", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 429
    assert response.json()["detail"] == (
        "Rate limit exceeded! You can only view emails 10 times per hour. "
        "Wait 42 seconds."
    )
    assert response.headers["X-RateLimit-Limit"] == "10"
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert response.headers["Retry-After"] == "42"


# ---------------------------------------------------------------------------
# GET /api/sync
# ---------------------------------------------------------------------------


async def test_sync_requires_email_account_id(client):
    response = await client.get("/api/sync")

    assert response.status_code == 400
    assert response.json()["detail"] == "email_account_id parameter is required"


async def test_sync_rate_limited(client, limiter):
    limiter.allowed = False

    response = await client.get(
        "/api/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 429
    assert response.json()["detail"] == (
        "Rate limit exceeded! You can only view emails 10 times per hour. "
        "Wait 42 seconds."
    )
    assert response.headers["Retry-After"] == "42"
    assert limiter.calls[0]["endpoint"] == "api/sync"
    assert limiter.calls[0]["capacity"] == 10
    assert limiter.calls[0]["refill_rate"] == 10


async def test_sync_no_message_ids(client, monkeypatch):
    monkeypatch.setattr(gmail, "get_service", lambda _id: FakeGmailService({}))

    response = await client.get(
        "/api/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "No new emails found",
        "total_fetched": 0,
        "new_emails": 0,
    }


async def test_sync_go_server_path_stores_precomputed_embeddings(
    client, collection, monkeypatch
):
    """Go server answers 200 -> the mails it returns go to /store as-is."""
    service = FakeGmailService(
        {"go-1": _gmail_message("Subject A", "a@example.com", "text", "<p>html</p>")}
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    go_mails = [
        {
            "message_id": "go-1",
            "sender": "a@example.com",
            "subject": "Subject A",
            "date_sent": "2026-09-15T10:30:00",
            "body_text": "cleaned by go",
            "embedding": [0.5, 0.25],
        }
    ]
    posted: list[dict[str, Any]] = []

    def fake_post(url, json=None, timeout=None):
        posted.append({"url": url, "json": json, "timeout": timeout})
        return httpx.Response(200, json={"emails": go_mails})

    monkeypatch.setattr(sync.requests, "post", fake_post)

    response = await client.get(
        "/api/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Synced 1 new emails",
        "total_fetched": 1,
        "new_emails": 1,
    }

    # B5: the Go call keeps its 1000 s timeout and its request shape.
    assert posted[0]["url"] == "http://localhost:8001/fetch-emails"
    assert posted[0]["timeout"] == 1000
    assert posted[0]["json"] == {
        "email_account_id": EMAIL_ACCOUNT_ID,
        "mail_ids": ["go-1"],
    }

    # The precomputed-store route was reached with the mails Go returned.
    assert len(collection._collection.add_calls) == 1
    call = collection._collection.add_calls[0]
    assert call["ids"] == ["go-1"]
    assert call["embeddings"] == [[0.5, 0.25]]
    assert call["documents"] == ["cleaned by go"]
    assert call["metadatas"] == [
        {
            "message_id": "go-1",
            "sender": "a@example.com",
            "subject": "Subject A",
            "date_sent": "2026-09-15T10:30:00",
        }
    ]
    # embed-and-store must NOT have been touched on this branch.
    assert collection.add_documents_calls == []

    # Gmail was queried for the Primary inbox, capped at 60.
    assert service.list_queries[0]["q"] == "in:inbox category:primary"
    assert service.list_queries[0]["maxResults"] == 60


async def test_sync_fallback_embeds_cleaned_bodies(
    client, database, collection, monkeypatch
):
    """S1 regression test.

    The Go server is unreachable, so the Python fallback runs: prepare, save to
    the Database service, then embed. The monolith fed ``save_emails``' detached
    ORM objects to ``embed_and_store`` and always 500ed (addendum C1). The
    payload must now be built from ``email_data``, carry exactly
    ``message_id`` / ``sender`` / ``subject`` / ``date_sent`` / ``body_text``,
    and its ``body_text`` must be the **cleaned** body.
    """
    html_body = (
        "<html><body><div>Assignment deadline is 12-Oct-2025.</div>"
        "<div>Submit before the closing date.</div></body></html>"
    )
    service = FakeGmailService(
        {
            "fb-1": _gmail_message(
                "Deadline notice",
                "registry@example.com",
                "Assignment deadline is 12-Oct-2025.\n\n\n\nSubmit before the closing date.",
                html_body,
            )
        }
    )
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    def boom(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("go server down")

    monkeypatch.setattr(sync.requests, "post", boom)

    response = await client.get(
        "/api/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    # ``new_emails`` is ``len(email_data)`` — the number of mails PREPARED, not
    # the number newly saved. That divergence from the Database service's
    # ``count`` is pre-existing monolith behavior (one shared return site in
    # controllers/emails.py) and is deliberately untouched by this refactor.
    assert response.json() == {
        "status": "success",
        "message": "Synced 1 new emails",
        "total_fetched": 1,
        "new_emails": 1,
    }

    # The fallback saved to the Database service.
    stored = await database.get(
        "/emails", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )
    rows = stored.json()
    assert [row["message_id"] for row in rows] == ["fb-1"]
    assert rows[0]["subject"] == "Deadline notice"
    # SQLite's DateTime column drops the tzinfo the RFC 2822 header carried,
    # so the cached row reads back naive. The embedding metadata below keeps
    # the offset because it is built from the in-memory datetime.
    assert rows[0]["date_sent"] == "2026-09-15T10:30:00"

    # ...and reached embed-and-store with cleaned bodies.
    assert len(collection.add_documents_calls) == 1
    documents, ids = collection.add_documents_calls[0]
    assert ids == ["fb-1"]
    assert len(documents) == 1

    cleaned = documents[0].page_content
    # ``clean_email`` collapsed the runs of blank lines the raw body carried and
    # stripped the HTML wrapper; the deadline text survived.
    assert "Assignment deadline is 12-Oct-2025." in cleaned
    assert "Submit before the closing date." in cleaned
    assert "<html>" not in cleaned and "<div>" not in cleaned
    assert "\n\n\n" not in cleaned
    raw_body_text = (
        "Assignment deadline is 12-Oct-2025.\n\n\n\nSubmit before the closing date."
    )
    assert cleaned != raw_body_text  # it really was cleaned, not passed through

    assert documents[0].metadata == {
        "message_id": "fb-1",
        "sender": "registry@example.com",
        "subject": "Deadline notice",
        "date_sent": "2026-09-15 10:30:00+00:00",
    }
    # /store (the precomputed route) is not part of this branch.
    assert collection._collection.add_calls == []


async def test_sync_second_run_uses_newer_than_query(client, database, monkeypatch):
    """A cached email makes the query ``in:inbox category:primary newer_than:Nd``."""
    await _seed_email(database, date_sent="2020-01-01T00:00:00")

    service = FakeGmailService({})
    monkeypatch.setattr(gmail, "get_service", lambda _id: service)

    response = await client.get(
        "/api/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    query = service.list_queries[0]["q"]
    assert query.startswith("in:inbox category:primary newer_than:")
    assert query.endswith("d")


async def test_sync_reports_expired_token(client, monkeypatch):
    def explode(_id):
        raise Exception("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(gmail, "get_service", explode)

    response = await client.get(
        "/api/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "message": "OAuth token has expired. Please re-authenticate by running gmail_read.py.",
        "error": "token_expired",
    }


async def test_sync_reports_missing_credential_fields(client, monkeypatch):
    def explode(_id):
        raise Exception(
            "The credentials do not contain the necessary fields need to refresh the access token."
        )

    monkeypatch.setattr(gmail, "get_service", explode)

    response = await client.get(
        "/api/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "message": "OAuth credentials need to be refreshed. Please run the gmail_read.py script first to authenticate.",
        "error": "authentication_required",
    }


async def test_sync_other_errors_are_500(client, monkeypatch):
    def explode(_id):
        raise Exception("something else broke")

    monkeypatch.setattr(gmail, "get_service", explode)

    response = await client.get(
        "/api/sync", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Error syncing emails: something else broke"


# ---------------------------------------------------------------------------
# GET /api/calendar/events
# ---------------------------------------------------------------------------


@pytest.fixture
def calendar_service(monkeypatch) -> FakeCalendarService:
    """Patch the single ``get_calendar_service`` binding (hazard 1 is retired)."""
    service = FakeCalendarService()
    monkeypatch.setattr(
        google_calendar, "get_calendar_service", lambda _id=None: (service, None)
    )
    return service


async def test_get_calendar_events_groups_by_date_key(
    client, calendar_service, accounts
):
    calendar_service.items = [
        {
            "id": "evt-timed",
            "summary": "Standup",
            "description": "daily",
            "start": {"dateTime": "2026-09-15T09:00:00Z"},
            "end": {"dateTime": "2026-09-15T09:15:00Z"},
            "extendedProperties": {"private": {"category": "Work"}},
        },
        {
            "id": "evt-allday",
            "start": {"date": "2026-09-16"},
            "end": {"date": "2026-09-17"},
        },
    ]

    response = await client.get(
        "/api/calendar/events", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["message"] == "Calendar events retrieved successfully"
    assert sorted(body["events"]) == ["2026-09-15", "2026-09-16"]

    timed = body["events"]["2026-09-15"][0]
    assert timed["id"] == "evt-timed"
    assert timed["title"] == "Standup"
    assert timed["category"] == "Work"
    assert timed["time"] == "09:00 AM"
    assert timed["description"] == "daily"
    assert timed["start"] == "2026-09-15T09:00:00Z"
    assert timed["end"] == "2026-09-15T09:15:00Z"

    allday = body["events"]["2026-09-16"][0]
    assert allday["title"] == "No Title"
    assert allday["category"] is None
    assert allday["time"] == "All Day"

    # The calendar is always read through the primary email account.
    assert accounts.calls[0][0] == (
        f"/internal/email-accounts/{EMAIL_ACCOUNT_ID}/primary"
    )
    assert calendar_service.listed[0]["calendarId"] == "primary"
    assert calendar_service.listed[0]["maxResults"] == 2500


async def test_get_calendar_events_requires_email_account_id(client):
    response = await client.get("/api/calendar/events")

    assert response.status_code == 400
    assert response.json()["detail"] == "email_account_id parameter is required"


async def test_get_calendar_events_auth_required_uses_original_id(
    client, accounts, monkeypatch
):
    """The auth-URL request carries the ORIGINAL id, not the primary one.

    ``calendar.py:103`` passes ``email_account_id``, not
    ``primary_email_account_id``. Preserved deliberately.
    """
    monkeypatch.setattr(
        google_calendar,
        "get_calendar_service",
        lambda _id=None: (None, "Authentication required"),
    )

    response = await client.get(
        "/api/calendar/events", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "auth_required",
        "auth_url": accounts.auth_url,
        "message": "Please authenticate with Google Calendar",
    }

    auth_calls = [call for call in accounts.calls if call[0] == "/internal/oauth/auth-url"]
    assert auth_calls == [
        ("/internal/oauth/auth-url", {"email_account_id": EMAIL_ACCOUNT_ID})
    ]
    assert accounts.primary_id != EMAIL_ACCOUNT_ID  # the distinction is real


async def test_get_calendar_events_service_failure_is_500(client, monkeypatch):
    monkeypatch.setattr(
        google_calendar,
        "get_calendar_service",
        lambda _id=None: (None, "Re-authentication failed"),
    )

    response = await client.get(
        "/api/calendar/events", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 500
    assert (
        response.json()["detail"]
        == "Failed to get calendar service: Re-authentication failed"
    )


async def test_get_primary_email_account_falls_back_on_accounts_failure(
    client, accounts, calendar_service
):
    """An Accounts failure falls back to the id the caller supplied."""
    accounts.primary_status = 503

    response = await client.get(
        "/api/calendar/events", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    assert response.json()["events"] == {}


# ---------------------------------------------------------------------------
# POST /api/calendar/events
# ---------------------------------------------------------------------------


async def test_create_calendar_event_timed(client, calendar_service, limiter):
    response = await client.post(
        "/api/calendar/events",
        json={
            "email_account_id": EMAIL_ACCOUNT_ID,
            "event_data": {
                "title": "Review",
                "description": "quarterly",
                "date": "2026-09-15",
                "time": "02:30 PM",
                "category": "Work",
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Event created successfully",
        "event_id": "created-1",
        "event_link": "https://cal/created-1",
    }

    body = calendar_service.inserted[0]
    assert body["summary"] == "Review"
    assert body["description"] == "quarterly"
    assert body["start"] == {"dateTime": "2026-09-15T14:30:00", "timeZone": "UTC"}
    assert body["end"] == {"dateTime": "2026-09-15T15:30:00", "timeZone": "UTC"}
    assert body["extendedProperties"] == {"private": {"category": "Work"}}

    # R8 again: capacity/refill differ from the emails routes.
    assert limiter.calls == [
        {
            "scope": "global",
            "identifier": "all",
            "endpoint": "api/calendar/events",
            "tokens": 1,
            "capacity": 100,
            "refill_rate": 100,
        }
    ]


async def test_create_calendar_event_all_day(client, calendar_service):
    response = await client.post(
        "/api/calendar/events",
        json={
            "email_account_id": EMAIL_ACCOUNT_ID,
            "event_data": {"title": "Holiday", "date": "2026-12-25", "time": "All Day"},
        },
    )

    assert response.status_code == 200
    body = calendar_service.inserted[0]
    assert body["start"] == {"date": "2026-12-25"}
    assert body["end"] == {"date": "2026-12-25"}
    assert "extendedProperties" not in body


async def test_create_calendar_event_rate_limited(client, limiter, calendar_service):
    limiter.allowed = False

    response = await client.post(
        "/api/calendar/events",
        json={
            "email_account_id": EMAIL_ACCOUNT_ID,
            "event_data": {"title": "Blocked", "date": "2026-09-15"},
        },
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
    assert calendar_service.inserted == []


# ---------------------------------------------------------------------------
# PUT / DELETE /api/calendar/events/{event_id}
# ---------------------------------------------------------------------------


async def test_update_calendar_event(client, calendar_service):
    calendar_service.existing_event = {
        "id": "evt-1",
        "summary": "Old",
        "start": {"date": "2026-09-01"},
        "end": {"date": "2026-09-01"},
    }

    response = await client.put(
        "/api/calendar/events/evt-1",
        json={
            "email_account_id": EMAIL_ACCOUNT_ID,
            "event_data": {
                "title": "New title",
                "description": "new description",
                "date": "2026-09-20",
                "time": "09:00 AM",
                "category": "Personal",
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Event updated successfully",
        "event_link": "https://cal/updated",
    }

    event_id, body = calendar_service.updated[0]
    assert event_id == "evt-1"
    assert body["summary"] == "New title"
    assert body["description"] == "new description"
    assert body["extendedProperties"]["private"]["category"] == "Personal"
    assert body["start"] == {"dateTime": "2026-09-20T09:00:00", "timeZone": "UTC"}
    assert body["end"] == {"dateTime": "2026-09-20T10:00:00", "timeZone": "UTC"}


async def test_delete_calendar_event(client, calendar_service):
    response = await client.delete(
        "/api/calendar/events/evt-9",
        params={"email_account_id": EMAIL_ACCOUNT_ID},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Event deleted successfully",
    }
    assert calendar_service.deleted == ["evt-9"]


async def test_delete_calendar_event_requires_email_account_id(client):
    response = await client.delete("/api/calendar/events/evt-9")

    assert response.status_code == 422  # FastAPI: required query parameter


# ---------------------------------------------------------------------------
# GET /api/calendar/status  (X4)
# ---------------------------------------------------------------------------


async def test_calendar_status_success(client, calendar_service):
    response = await client.get(
        "/api/calendar/status", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": f"Calendar service is working for email account {EMAIL_ACCOUNT_ID}",
        "email_account_id": EMAIL_ACCOUNT_ID,
    }


async def test_calendar_status_defaults_to_account_one(client, monkeypatch):
    """X4: with no id supplied the endpoint falls back to email account 1."""
    seen: list[Any] = []

    def fake(email_account_id=None):
        seen.append(email_account_id)
        return None, "Authentication required"

    monkeypatch.setattr(google_calendar, "get_calendar_service", fake)

    response = await client.get("/api/calendar/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "message": "Calendar service failed: Authentication required",
        "email_account_id": 1,
        "error": "Authentication required",
    }
    assert seen == [1]


# ---------------------------------------------------------------------------
# GET /api/calendar/moodle  (X4 + P2)
# ---------------------------------------------------------------------------


class MoodleCalendarService(FakeCalendarService):
    """Adds the ``calendarList`` surface ``moodle.py`` needs."""

    def __init__(self, calendars: list[dict[str, Any]]) -> None:
        super().__init__()
        self.calendars = calendars

    def calendarList(self):
        return self

    def list(self, **kwargs):  # serves both calendarList().list() and events().list()
        if not kwargs:
            return _Executable({"items": self.calendars})
        self.listed.append(kwargs)
        return _Executable({"items": self.items})


async def test_moodle_events_grouped_by_date(client, monkeypatch):
    service = MoodleCalendarService(
        [{"id": "moodle-cal", "summary": "Moodle — MSc"}]
    )
    service.items = [
        {
            "id": "moodle-evt",
            "summary": "Coursework due",
            "description": "submit",
            "start": {"dateTime": "2026-10-01T23:59:00Z"},
            "end": {"dateTime": "2026-10-02T00:00:00Z"},
        }
    ]
    seen: list[Any] = []

    def fake(email_account_id=None):
        seen.append(email_account_id)
        return service, None

    # Patching the ONE binding reaches moodle.py too — the point of hazard 1.
    monkeypatch.setattr(google_calendar, "get_calendar_service", fake)

    response = await client.get("/api/calendar/moodle")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["calendar_id"] == "moodle-cal"
    assert body["count"] == 1
    event = body["events"]["2026-10-01"][0]
    assert event["id"] == "moodle-evt"
    assert event["title"] == "Coursework due"
    assert event["category"] == "Moodle"
    assert event["source"] == "moodle"
    assert event["time"] == "11:59 PM"
    # X4: no id supplied -> email account 1. P2: the parameter is now
    # ``email_account_id``, so the call no longer TypeErrors.
    assert seen and set(seen) == {1}


async def test_moodle_events_missing_calendar_is_500(client, monkeypatch):
    service = MoodleCalendarService([{"id": "other", "summary": "Personal"}])
    monkeypatch.setattr(
        google_calendar, "get_calendar_service", lambda _id=None: (service, None)
    )

    response = await client.get(
        "/api/calendar/moodle", params={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Calendar 'Moodle' not found"


async def test_moodle_get_events_for_api_accepts_email_account_id_keyword():
    """P2 directly: the keyword MCP calls with must exist.

    ``mcp_server.get_calendar_events`` called
    ``get_moodle_events_for_api(email_account_id=...)`` against a parameter named
    ``user_id`` and raised ``TypeError``, swallowed as a warning, so MCP never
    returned Moodle events.
    """
    from backend.services.user_data import moodle

    with mock.patch.object(
        google_calendar,
        "get_calendar_service",
        return_value=(None, "Authentication required"),
    ):
        result = moodle.get_moodle_events_for_api(email_account_id=3)

    assert result == {"error": "Authentication required", "events": {}}


# ---------------------------------------------------------------------------
# The two Google service getters keep their original shapes
# ---------------------------------------------------------------------------


def test_get_service_is_synchronous_and_reraises_upstream_409(monkeypatch):
    """``gmail.get_service(email_account_id)`` — sync, raises on 409 (Spec 3.3.1)."""
    import inspect

    from backend.libs.common.errors import UpstreamError

    assert not inspect.iscoroutinefunction(gmail.get_service)
    assert list(inspect.signature(gmail.get_service).parameters) == ["email_account_id"]

    class Failing:
        def get(self, path, *, params=None, timeout=None):
            raise UpstreamError(
                409,
                {
                    "error": "Authentication failed for email account 7. "
                    "Cannot proceed without valid credentials."
                },
            )

    monkeypatch.setattr(gmail, "get_accounts_sync_client", lambda: Failing())

    with pytest.raises(Exception) as excinfo:
        gmail.get_service(7)

    assert str(excinfo.value) == (
        "Authentication failed for email account 7. "
        "Cannot proceed without valid credentials."
    )


def test_get_calendar_service_is_synchronous_and_returns_error_tuple(monkeypatch):
    """``get_calendar_service`` returns ``(None, error)`` — it never raises."""
    import inspect

    from backend.libs.common.errors import UpstreamError

    assert not inspect.iscoroutinefunction(google_calendar.get_calendar_service)
    assert list(
        inspect.signature(google_calendar.get_calendar_service).parameters
    ) == ["email_account_id"]

    assert google_calendar.get_calendar_service(None) == (
        None,
        "Email account ID is required",
    )

    class Failing:
        def get(self, path, *, params=None, timeout=None):
            raise UpstreamError(409, {"error": "Authentication required"})

    monkeypatch.setattr(google_calendar, "get_accounts_sync_client", lambda: Failing())

    assert google_calendar.get_calendar_service(7) == (None, "Authentication required")


def test_get_calendar_service_builds_and_saves_credentials(monkeypatch):
    """On success it builds the client and PUTs the creds back (setup_calendar.py:110-114)."""
    creds_payload = {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
        "expiry": datetime(2030, 1, 1, 0, 0, 0).isoformat(),
    }
    puts: list[tuple[str, Any]] = []

    class Stub:
        def get(self, path, *, params=None, timeout=None):
            assert params == {"require_scope": "calendar", "allow_interactive": "false"}
            return httpx.Response(200, json=creds_payload)

        def put(self, path, *, params=None, json=None, timeout=None):
            puts.append((path, json))
            return httpx.Response(204)

    monkeypatch.setattr(google_calendar, "get_accounts_sync_client", lambda: Stub())
    built: list[dict[str, Any]] = []
    monkeypatch.setattr(
        google_calendar,
        "build",
        lambda name, version, credentials=None: built.append(
            {"name": name, "version": version, "credentials": credentials}
        )
        or "calendar-service",
    )

    service, error = google_calendar.get_calendar_service(7)

    assert service == "calendar-service"
    assert error is None
    assert built[0]["name"] == "calendar"
    assert built[0]["version"] == "v3"
    assert built[0]["credentials"].token == "access-token"
    assert puts[0][0] == "/internal/email-accounts/7/credentials"
    assert puts[0][1]["token"] == "access-token"
    assert puts[0][1]["refresh_token"] == "refresh-token"
