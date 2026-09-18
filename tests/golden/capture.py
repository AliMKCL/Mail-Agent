"""
Golden-file capture for the pre-refactor monolith (Phase 0 safety net).

This module is the single source of truth for

  * ``REQUESTS``   -- the fixed, ordered request list (every row of Spec 3.1 plus
                      the error cases named in the Phase 0 brief),
  * ``normalize`` -- the per-run volatility scrubber,
  * ``harness``   -- the temp database + mock set the requests are driven against,

and it is imported by ``tests/golden/test_parity.py`` so that capture and replay
can never drift apart.

Running it (``uv run python tests/golden/capture.py``) rewrites
``tests/golden/<slug>.json`` from the app named by ``CAPTURE_APP_IMPORT``.

--------------------------------------------------------------------------------
Ordering is part of the fixture
--------------------------------------------------------------------------------
Several requests mutate state (sign-up creates account 3, ``POST /api/users``
creates email account 4, the OAuth callback stores a token for email account 1,
the Go-server-down sync path saves an email for email account 2). ``REQUESTS`` is
therefore an ORDERED list and must be replayed start-to-finish against a freshly
built harness. Do not reorder, insert in the middle, or run a subset.

--------------------------------------------------------------------------------
Normalization (deliberately minimal -- over-normalizing destroys the safety net)
--------------------------------------------------------------------------------
Exactly two things are scrubbed to ``"<VOLATILE>"``:

1. Values of the keys ``created_at``, ``updated_at``, ``date_sent`` and
   ``expiry`` *when* the value looks like an ISO-8601 date-*time*
   (``YYYY-MM-DDTHH:MM``). These come from ``datetime.utcnow()`` column
   defaults and from the ``datetime.now()``-based seed emails, so they differ on
   every run. The date-only ``date_sent`` strings inside ``/api/query``
   ``sources`` (e.g. ``"2025-11-22"``) are fixture constants and are NOT
   scrubbed.
2. The ``client_id`` query parameter *inside* the ``auth_url`` value. It is read
   from the gitignored ``credentials.json``, so it is both a secret and
   machine-dependent. Every other part of that URL (scope list, redirect_uri,
   access_type, include_granted_scopes, prompt, state, response_type) is left
   byte-for-byte intact -- it is real contract surface.

Everything else -- autoincrement ids, event ids, counters, print-driven message
strings, error ``detail`` strings -- is deterministic given a fresh temp database
and the fixed request order, and is asserted verbatim.

--------------------------------------------------------------------------------
Known-bug behaviour captured ON PURPOSE (do not "fix" these)
--------------------------------------------------------------------------------
* X1: ``POST /api/auth/signup`` with an existing email and a *different*
  password returns 200 with the pre-existing ``account_id``.
* X2: ``GET /api/users`` with no ``account_id`` returns every tenant's mailbox.
* P3 is the ONE exception. The monolith's ``POST /api/llm-query`` passed
  ``user_id=`` to a ``process_llm_query`` whose signature takes
  ``email_account_id=``, so it 500d on every request. Wave 5 fixed it
  deliberately, so ``post__api_llm_query.json`` is the only golden that was
  re-baselined for the split. Every other golden is byte-identical to the
  monolith capture.

--------------------------------------------------------------------------------
The stack the requests are driven against (Wave 6)
--------------------------------------------------------------------------------
``harness`` builds all six services in ONE process and wires them together with
``httpx.ASGITransport``. No socket is ever bound, so ports 8000/8010/8020/8030/
8040/8050 stay down for the whole run:

* Database (:8030) — the real app, ``get_db_manager`` overridden to a
  ``DatabaseManager`` on a throwaway temp SQLite file. This fixture is the only
  sanctioned place outside ``backend/services/database/`` that may construct one
  (R1).
* Vector DB (:8040) — the real app with ``store.collection`` and
  ``store.embeddings`` replaced by in-memory fakes. ``langchain_chroma.Chroma``
  and ``langchain_ollama.OllamaEmbeddings`` are patched for the duration of the
  import too, so the real ``vector_database/`` directory is never opened. That
  ends the addendum-C2 behaviour where a golden run changed the md5 of
  ``vector_database/chroma.sqlite3``.
* Accounts (:8010), User_data (:8020), MCP (:8050) — the real apps, with each
  service's client providers overridden through ``app.dependency_overrides`` to
  ``AsyncServiceClient``s carrying an ``ASGITransport``.
* Gateway (:8000) — the real app, with ``proxy.set_client_factory`` installing
  ASGITransport-backed clients for its three upstreams.

--------------------------------------------------------------------------------
Externals, and where they are mocked
--------------------------------------------------------------------------------
Everything is patched at the binding the SERVICES resolve, never the deleted
``backend.controllers.*`` paths:

* Gmail — ``backend.services.user_data.gmail.get_service``.
* Google Calendar — ``backend.services.user_data.google_calendar
  .get_calendar_service``. That is the single binding: ``moodle.py`` and
  ``routers/calendar.py`` both reach it as a module attribute, which is what
  retires CONTRACT_FREEZE hazard 1 (the monolith had two independent bindings).
* The Go sync server — ``requests.post`` inside
  ``backend.services.user_data.sync``.
* OAuth token exchange — ``Flow`` in ``backend.services.accounts.routers.oauth``.
  Deliberately NOT ``google_oauth.Flow``: ``GET /api/auth/google`` must keep
  building its real URL from ``credentials.json`` (that call is offline), and the
  golden asserts that URL with only ``client_id`` scrubbed.
* The rate limiter — ``get_limiter`` on User_data and MCP.
* OpenAI — ``openai.AsyncOpenAI``. ``backend.services.mcp.llm_integration``
  imports it inside ``process_with_openai``, so the module attribute is the only
  correct patch point. This one is non-negotiable: ``.env`` holds a real
  ``OPENAI_API_KEY`` and ``ask_ollama`` calls ``load_dotenv()`` at import, so an
  unmocked path would make a live billed call and produce a non-deterministic
  golden. ``OPENAI_API_KEY`` is additionally overwritten with a dummy value for
  the duration of the harness.
* The local LLM helpers — ``llm_response`` in
  ``backend.services.mcp.http_app`` and ``slm_response`` in
  ``backend.services.mcp.ask_ollama`` (imported at call time).

--------------------------------------------------------------------------------
Environment dependencies
--------------------------------------------------------------------------------
``GET /api/auth/google`` builds its URL from ``credentials.json`` in the repo
root. That file is gitignored; without it the endpoint returns 500 instead of
200 and the golden will not replay. The real ``gmail_agent.db`` and
``vector_database/`` are never read or written: the database is redirected to a
throwaway temp file and every Chroma/Ollama/Google/Go/OpenAI/rate-limiter call is
mocked.
"""

from __future__ import annotations

# Run as a bare script (`uv run python tests/golden/capture.py`) there is no repo
# root on sys.path and `import backend` fails. Rule R9 forbids sys.path.insert in
# new files, so re-exec as a package module from the repo root instead, which
# puts the repo root on sys.path the normal way.
if __name__ == "__main__" and __package__ in (None, ""):
    import pathlib
    import subprocess
    import sys

    raise SystemExit(
        subprocess.call(
            [sys.executable, "-m", "tests.golden.capture", *sys.argv[1:]],
            cwd=str(pathlib.Path(__file__).resolve().parents[2]),
        )
    )

import base64
import hashlib
import importlib
import json
import os
import re
import shutil
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import requests
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials

# The Vector DB service builds its Chroma client and its Ollama embedding model
# at module import time, against the REAL ``vector_database/`` directory. Both
# constructors are patched for the duration of the import so the directory is
# never opened -- this is what stops a golden run from changing the md5 of
# ``vector_database/chroma.sqlite3`` (addendum C2). The module-scope objects are
# then replaced wholesale by in-memory fakes inside ``harness``.
with patch("langchain_chroma.Chroma"), patch("langchain_ollama.OllamaEmbeddings"):
    from backend.services.vector_db import store
    from backend.services.vector_db.app import app as vector_db_app

from backend.gateway import proxy
from backend.libs.common.http import AsyncServiceClient
from backend.services.accounts import clients as accounts_clients
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
from backend.services.user_data import sync as user_data_sync
from backend.services.user_data.app import app as user_data_app

GOLDEN_DIR = Path(__file__).resolve().parent

# The app the goldens are captured FROM. Wave 6: the monolith is gone as a
# target; the goldens are now captured from -- and replayed against -- the
# Gateway in front of the six-service stack ``harness`` builds.
CAPTURE_APP_IMPORT = "backend.gateway.app:app"

# Base URLs for the in-process clients. The path component must be empty: the
# Gateway proxy forwards ``request.url.path`` as a RELATIVE url and ``httpx``
# merges it onto the client's base_url.
ACCOUNTS_BASE_URL = "http://accounts.golden"
USER_DATA_BASE_URL = "http://user-data.golden"
MCP_BASE_URL = "http://mcp.golden"
DATABASE_BASE_URL = "http://database.golden"
VECTOR_DB_BASE_URL = "http://vector-db.golden"

PLACEHOLDER = "<VOLATILE>"

# Email account that the fake Go sync server answers successfully for; every
# other account id makes it raise RequestException (the Python fallback path).
GO_SYNC_OK_EMAIL_ACCOUNT_ID = 1

ALLOW_RESULT = {
    "allowed": True,
    "limit": 100,
    "remaining": 99,
    "reset_after_seconds": 0,
    "retry_after_seconds": 0,
}

RATELIMIT_HEADERS = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After")


# ============================================================================
# REQUEST LIST (ordered -- see module docstring)
# ============================================================================

REQUESTS: tuple[dict, ...] = (
    # --- Gateway-local routes -------------------------------------------------
    {"slug": "get__root", "method": "GET", "path": "/"},
    {"slug": "get__static_styles_css", "method": "GET", "path": "/static/styles.css"},
    # --- Accounts: auth -------------------------------------------------------
    {
        "slug": "post__api_auth_signin__valid",
        "method": "POST",
        "path": "/api/auth/signin",
        "json": {"email": "testuser@gmail.com", "password": "testpassword"},
    },
    {
        "slug": "post__api_auth_signin__wrong_password",
        "method": "POST",
        "path": "/api/auth/signin",
        "json": {"email": "testuser@gmail.com", "password": "not-the-password"},
    },
    {
        # X1: account takeover -- returns 200 with the EXISTING account_id.
        "slug": "post__api_auth_signup__existing_email_other_password",
        "method": "POST",
        "path": "/api/auth/signup",
        "json": {"email": "testuser@gmail.com", "password": "attacker-password"},
    },
    {
        "slug": "post__api_auth_signup__new_account",
        "method": "POST",
        "path": "/api/auth/signup",
        "json": {"email": "thirduser@gmail.com", "password": "testpassword3"},
    },
    # --- Accounts: OAuth ------------------------------------------------------
    {
        "slug": "get__api_auth_google",
        "method": "GET",
        "path": "/api/auth/google",
        "params": {"email_account_id": 1},
    },
    {
        "slug": "get__oauth_callback",
        "method": "GET",
        "path": "/oauth/callback",
        "params": {"code": "golden-auth-code", "state": "1"},
    },
    # --- Accounts: users ------------------------------------------------------
    {
        # X2: no account_id -> every tenant's email accounts.
        "slug": "get__api_users__all",
        "method": "GET",
        "path": "/api/users",
    },
    {
        "slug": "get__api_users__account_1",
        "method": "GET",
        "path": "/api/users",
        "params": {"account_id": 1},
    },
    {
        "slug": "post__api_users__valid",
        "method": "POST",
        "path": "/api/users",
        "json": {"email": "extra@gmail.com", "name": "Extra Mailbox", "account_id": 1},
    },
    {
        "slug": "post__api_users__missing_account_id",
        "method": "POST",
        "path": "/api/users",
        "json": {"email": "orphan@gmail.com", "name": "Orphan"},
    },
    {
        "slug": "get__api_email_account__existing",
        "method": "GET",
        "path": "/api/email-account/1",
    },
    {
        "slug": "get__api_email_account__missing",
        "method": "GET",
        "path": "/api/email-account/99999",
    },
    # --- User_data: emails ----------------------------------------------------
    {
        "slug": "get__api_emails__with_account",
        "method": "GET",
        "path": "/api/emails",
        "params": {"email_account_id": 1},
    },
    {"slug": "get__api_emails__no_params", "method": "GET", "path": "/api/emails"},
    {"slug": "get__api_sync__no_params", "method": "GET", "path": "/api/sync"},
    {
        # Go sync server answers 200 -> store_in_vector_db branch.
        "slug": "get__api_sync__go_server_ok",
        "method": "GET",
        "path": "/api/sync",
        "params": {"email_account_id": GO_SYNC_OK_EMAIL_ACCOUNT_ID},
    },
    {
        # Go sync server unreachable -> Python fallback + embed_and_store branch.
        "slug": "get__api_sync__go_server_down",
        "method": "GET",
        "path": "/api/sync",
        "params": {"email_account_id": 2},
    },
    # --- User_data: calendar --------------------------------------------------
    {
        "slug": "get__api_calendar_events__with_account",
        "method": "GET",
        "path": "/api/calendar/events",
        "params": {"email_account_id": 1},
    },
    {
        "slug": "get__api_calendar_events__no_params",
        "method": "GET",
        "path": "/api/calendar/events",
    },
    {
        "slug": "post__api_calendar_events",
        "method": "POST",
        "path": "/api/calendar/events",
        "json": {
            "email_account_id": 1,
            "event_data": {
                "title": "Golden Event",
                "description": "Created by the golden capture",
                "date": "2025-11-24",
                "time": "02:30 PM",
                "category": "Academic",
            },
        },
    },
    {
        "slug": "put__api_calendar_events__event_001",
        "method": "PUT",
        "path": "/api/calendar/events/event_001",
        "json": {
            "email_account_id": 1,
            "event_data": {
                "title": "Existing Event (updated)",
                "description": "Updated by the golden capture",
                "date": "2025-11-25",
                "time": "All Day",
                "category": "Personal",
            },
        },
    },
    {
        "slug": "delete__api_calendar_events__event_001",
        "method": "DELETE",
        "path": "/api/calendar/events/event_001",
        "params": {"email_account_id": 1},
    },
    {
        "slug": "get__api_calendar_status",
        "method": "GET",
        "path": "/api/calendar/status",
        "params": {"email_account_id": 1},
    },
    {
        "slug": "get__api_calendar_moodle",
        "method": "GET",
        "path": "/api/calendar/moodle",
        "params": {"email_account_id": 1},
    },
    # --- MCP ------------------------------------------------------------------
    {
        "slug": "get__api_query",
        "method": "GET",
        "path": "/api/query",
        "params": {"query": "When is the meeting?", "top_k": 3},
    },
    {
        # P3: wrong kwarg to process_llm_query -- capture today's failure.
        "slug": "post__api_llm_query",
        "method": "POST",
        "path": "/api/llm-query",
        "json": {
            "query": "Find deadlines in my emails",
            "email_account_id": 1,
            "use_openai": True,
        },
    },
)


# ============================================================================
# NORMALIZATION
# ============================================================================

VOLATILE_TIMESTAMP_KEYS = frozenset({"created_at", "updated_at", "date_sent", "expiry"})

_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_CLIENT_ID_RE = re.compile(r"([?&]client_id=)[^&]*")


def normalize(value, key: str | None = None):
    """Replace per-run values with ``"<VOLATILE>"``. See module docstring."""
    if isinstance(value, dict):
        return {k: normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, key) for v in value]
    if isinstance(value, str):
        if key in VOLATILE_TIMESTAMP_KEYS and _ISO_DATETIME_RE.match(value):
            return PLACEHOLDER
        if key == "auth_url":
            return _CLIENT_ID_RE.sub(rf"\1{PLACEHOLDER}", value)
    return value


# ============================================================================
# SEED DATA (copied verbatim from tests/conftest.py)
# ============================================================================


def _hash_password(password: str) -> str:
    """Helper function to hash passwords for test accounts."""
    sha256_hash = hashlib.sha256()
    sha256_hash.update(password.encode("utf-8"))
    return sha256_hash.hexdigest()


def seed_database(db_manager) -> None:
    """Seed 2 accounts / 2 email accounts / 3 emails, exactly as conftest does."""
    # tests/conftest.py::test_account
    account = db_manager.get_or_create_account(
        "testuser@gmail.com", _hash_password("testpassword")
    )
    email_account = db_manager.get_or_create_email_account(
        account_id=account.id,
        email="testuser@gmail.com",
        provider="gmail",
        is_primary=True,
    )

    # tests/conftest.py::second_account
    second = db_manager.get_or_create_account(
        "seconduser@gmail.com", _hash_password("testpassword2")
    )
    second_email_account = db_manager.get_or_create_email_account(
        account_id=second.id,
        email="seconduser@gmail.com",
        provider="gmail",
        is_primary=True,
    )

    # tests/conftest.py::user_with_emails
    db_manager.save_emails(
        email_account.id,
        [
            {
                "message_id": "msg_001",
                "subject": "Test Email 1",
                "sender": "sender1@example.com",
                "recipient": "testuser@gmail.com",
                "date_sent": datetime.now() - timedelta(days=1),
                "snippet": "This is a test snippet",
                "body_text": "This is the full body of test email 1. It contains important information.",
                "body_html": "<p>This is the full body of test email 1.</p>",
            },
            {
                "message_id": "msg_002",
                "subject": "Meeting Tomorrow",
                "sender": "boss@company.com",
                "recipient": "testuser@gmail.com",
                "date_sent": datetime.now(),
                "snippet": "Meeting at 10am",
                "body_text": "Please join the meeting tomorrow at 10am in conference room A.",
                "body_html": None,
            },
        ],
    )

    # tests/conftest.py::second_user_with_emails
    db_manager.save_emails(
        second_email_account.id,
        [
            {
                "message_id": "msg_100",
                "subject": "Second User Email",
                "sender": "friend@example.com",
                "recipient": "seconduser@gmail.com",
                "date_sent": datetime.now(),
                "snippet": "Email for second user",
                "body_text": "This email belongs to the second user only.",
                "body_html": None,
            }
        ],
    )


# ============================================================================
# MOCKS
# ============================================================================

_GMAIL_MESSAGE_ID = "gm_001"
_GMAIL_MESSAGE = {
    "id": _GMAIL_MESSAGE_ID,
    "snippet": "Golden capture snippet",
    "payload": {
        "headers": [
            {"name": "From", "value": "golden-sender@example.com"},
            {"name": "To", "value": "golden-recipient@example.com"},
            {"name": "Subject", "value": "Golden Capture Email"},
            {"name": "Date", "value": "Mon, 01 Sep 2025 10:00:00 +0000"},
        ],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {
                    "data": base64.urlsafe_b64encode(
                        b"Golden capture plain text body."
                    ).decode("utf-8")
                },
            },
            {
                "mimeType": "text/html",
                "body": {
                    "data": base64.urlsafe_b64encode(
                        b"<p>Golden capture html body.</p>"
                    ).decode("utf-8")
                },
            },
        ],
    },
}

_GO_SERVER_EMAILS = [
    {
        "message_id": "go_001",
        "subject": "From Go Server 1",
        "sender": "go1@example.com",
        "recipient": "testuser@gmail.com",
        "snippet": "Go server email 1",
        "body_text": "Body of Go server email 1.",
        "body_html": None,
    },
    {
        "message_id": "go_002",
        "subject": "From Go Server 2",
        "sender": "go2@example.com",
        "recipient": "testuser@gmail.com",
        "snippet": "Go server email 2",
        "body_text": "Body of Go server email 2.",
        "body_html": None,
    },
]


def build_gmail_service_mock() -> MagicMock:
    """A MagicMock Gmail service that yields exactly one deterministic message."""
    service = MagicMock()

    def list_messages(**kwargs):
        mock_list = MagicMock()
        mock_list.execute.return_value = {"messages": [{"id": _GMAIL_MESSAGE_ID}]}
        return mock_list

    def get_message(**kwargs):
        mock_get = MagicMock()
        mock_get.execute.return_value = _GMAIL_MESSAGE
        return mock_get

    service.users().messages().list = list_messages
    service.users().messages().get = get_message
    return service


def fake_go_sync_post(url, json=None, timeout=None, **kwargs):
    """Stand-in for the local Go sync server (`requests.post`).

    Email account ``GO_SYNC_OK_EMAIL_ACCOUNT_ID`` gets a 200 JSON payload (the
    ``store_in_vector_db`` branch); anything else raises ``RequestException``
    (the Python-fallback ``embed_and_store`` branch).
    """
    payload = json or {}
    if payload.get("email_account_id") == GO_SYNC_OK_EMAIL_ACCOUNT_ID:
        response = MagicMock()
        response.status_code = 200
        response.text = "OK"
        response.json.return_value = {"emails": list(_GO_SERVER_EMAILS)}
        return response
    raise requests.exceptions.RequestException(
        "golden capture: simulated Go sync server outage"
    )


def build_calendar_service_mock():
    """Mock Google Calendar API service.

    Body copied from ``tests/conftest.py::mock_calendar_service``; the only
    addition is an explicit empty ``calendarList()`` so that
    ``/api/calendar/moodle`` deterministically fails to find the Moodle calendar
    instead of depending on MagicMock iteration behaviour.
    """
    service = MagicMock()

    # Store events in memory for CRUD testing
    events_store = {
        "event_001": {
            "id": "event_001",
            "summary": "Existing Event",
            "description": "Test description",
            "start": {"dateTime": "2025-11-23T10:00:00Z"},
            "end": {"dateTime": "2025-11-23T11:00:00Z"},
            "extendedProperties": {"private": {"category": "Academic"}},
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
            "htmlLink": f"https://calendar.google.com/event?id={new_id}",
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
            events_store[event_id]["htmlLink"] = (
                f"https://calendar.google.com/event?id={event_id}"
            )
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

    # Not in conftest: the Moodle passthrough looks up calendars by name.
    service.calendarList().list().execute.return_value = {"items": []}

    return service, events_store


#: The two documents ``tests/conftest.py::mock_vector_db`` returned, verbatim.
#: They are the corpus ``GET /api/query`` retrieves from.
_VECTOR_DOCUMENTS = (
    SimpleNamespace(
        page_content="Meeting tomorrow at 10am in conference room",
        metadata={
            "message_id": "msg_002",
            "sender": "boss@company.com",
            "subject": "Meeting Tomorrow",
            "date_sent": "2025-11-22",
        },
    ),
    SimpleNamespace(
        page_content="Project deadline is next Friday",
        metadata={
            "message_id": "msg_003",
            "sender": "pm@company.com",
            "subject": "Project Deadline",
            "date_sent": "2025-11-21",
        },
    ),
)


def _vector_hits(query: str) -> list[SimpleNamespace]:
    """``tests/conftest.py::mock_vector_db``'s keyword branching, unchanged."""
    doc1, doc2 = _VECTOR_DOCUMENTS
    if "meeting" in query.lower():
        return [doc1]
    if "deadline" in query.lower():
        return [doc2]
    return [doc1, doc2]


class GoldenEmbeddings:
    """Stands in for ``store.embeddings`` (``OllamaEmbeddings``). No network.

    ``store.query_vector_db`` embeds the query and then hands only the VECTOR to
    ``similarity_search_by_vector``, so the query text has to travel out of band
    for the keyword branching above to survive. It is recorded here and read by
    :class:`GoldenCollection`.
    """

    def __init__(self) -> None:
        self.last_query = ""

    def embed_query(self, text):
        self.last_query = text
        return [0.1, 0.2, 0.3]

    def embed_documents(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class GoldenRawCollection:
    """Stands in for ``collection._collection`` — the Go-sync ``/store`` path."""

    def __init__(self) -> None:
        self.add_calls: list[dict] = []

    def add(self, ids=None, embeddings=None, documents=None, metadatas=None):
        self.add_calls.append(
            {
                "ids": ids,
                "embeddings": embeddings,
                "documents": documents,
                "metadatas": metadatas,
            }
        )


class GoldenCollection:
    """Stands in for ``store.collection`` (the LangChain ``Chroma`` handle).

    Purely in-memory: the real ``vector_database/`` directory is never opened,
    read or written, which is the addendum-C2 fix.
    """

    def __init__(self, embeddings: GoldenEmbeddings) -> None:
        self._embeddings = embeddings
        self._collection = GoldenRawCollection()
        self.add_documents_calls: list[tuple] = []

    def add_documents(self, documents, ids=None):
        self.add_documents_calls.append((documents, ids))

    def similarity_search_by_vector(self, embedding, k=None):
        return _vector_hits(self._embeddings.last_query)


def build_vector_store_fakes() -> tuple[GoldenCollection, GoldenEmbeddings]:
    """The ``(collection, embeddings)`` pair ``store`` is monkeypatched with."""
    embeddings = GoldenEmbeddings()
    return GoldenCollection(embeddings), embeddings


def build_llm_mock():
    """Mock LLM response function (tests/conftest.py::mock_llm)."""

    def mock_response(query):
        return "Based on your emails, you have a meeting tomorrow at 10am."

    return mock_response


#: The answer the fake OpenAI client gives ``process_with_openai``. Same value as
#: ``tests/contract/test_mcp_http.py::FakeAsyncOpenAI``, so the two suites agree.
OPENAI_ANSWER = "You have no deadlines in the next week."


class GoldenAsyncOpenAI:
    """Stands in for ``openai.AsyncOpenAI``. Never opens a socket.

    Answers every ``chat.completions.create`` with a message that requests no
    tools, which is ``process_with_openai``'s "LLM has a final answer" branch:
    ``answer`` is the content and ``actions`` stays empty.
    """

    def __init__(self, api_key=None) -> None:
        self.api_key = api_key
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    async def _create(self, **kwargs):
        message = SimpleNamespace(content=OPENAI_ANSWER, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def build_oauth_flow_mock():
    """Mock ``google_auth_oauthlib.flow.Flow`` so the callback stays offline."""
    credentials = Credentials(
        token="golden-access-token",
        refresh_token="golden-refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="golden-client-id",
        client_secret="golden-client-secret",
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar",
        ],
    )
    flow = MagicMock()
    flow.credentials = credentials
    flow_class = MagicMock()
    flow_class.from_client_secrets_file.return_value = flow
    return flow_class


# ============================================================================
# HARNESS
# ============================================================================


class GoldenLimiter:
    """Always-allow ``RateLimiterClient`` stand-in.

    ``check`` returns the same dict shape the real client returns, so the
    ``result["allowed"]`` / ``result["limit"]`` / ``result["retry_after_seconds"]``
    reads in every ``limiter.check`` block keep working (R8). Nothing reaches the
    rate limiter on :8002.
    """

    def check(self, **kwargs) -> dict:
        return dict(ALLOW_RESULT)


@contextmanager
def _overridden(app, overrides: dict):
    """Install ``overrides`` into ``app.dependency_overrides`` and take them out
    again, leaving any pre-existing entries alone."""
    app.dependency_overrides.update(overrides)
    try:
        yield
    finally:
        for provider in overrides:
            app.dependency_overrides.pop(provider, None)


@contextmanager
def _gateway_clients(clients: dict[str, httpx.AsyncClient]):
    """Point the Gateway's three upstreams at in-process apps.

    ``backend.gateway.proxy.set_client_factory`` is the contractual override
    seam; it returns the previous factory, which is restored on the way out.
    The proxy never closes what the factory hands it, and an ``ASGITransport``
    owns no socket, so the clients simply go out of scope here.
    """
    previous = proxy.set_client_factory(clients.__getitem__)
    try:
        yield
    finally:
        proxy.set_client_factory(previous)


def _asgi_client(base_url: str, target) -> AsyncServiceClient:
    return AsyncServiceClient(base_url, transport=httpx.ASGITransport(app=target))


@contextmanager
def harness(app):
    """The whole six-service stack, in one process, plus a ``TestClient`` for
    ``app`` (the Gateway).

    Nothing binds a socket: every hop is an ``httpx.ASGITransport``. Nothing
    touches the real ``gmail_agent.db`` (the Database service is bound to a
    throwaway temp file) or the real ``vector_database/`` directory (the Vector
    DB service's Chroma handle and embedding model are in-memory fakes).

    See the module docstring for the full mock inventory and why each patch
    target is the one the services actually resolve.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="golden-capture-"))
    db_path = tmp_dir / "golden.db"

    # The sanctioned R1 seam: a test fixture MAY construct a DatabaseManager to
    # bind the Database service to a temp file. Production code may not.
    manager = DatabaseManager(f"sqlite:///{db_path}")

    calendar_service, _events_store = build_calendar_service_mock()

    def fake_get_calendar_service(email_account_id=None):
        # Callers unpack a (service, error) tuple.
        return calendar_service, None

    collection, embeddings = build_vector_store_fakes()
    limiter = GoldenLimiter()

    database_client = _asgi_client(DATABASE_BASE_URL, database_app)
    vector_db_client = _asgi_client(VECTOR_DB_BASE_URL, vector_db_app)
    accounts_client = _asgi_client(ACCOUNTS_BASE_URL, accounts_app)
    user_data_client = _asgi_client(USER_DATA_BASE_URL, user_data_app)

    try:
        seed_database(manager)

        with ExitStack() as stack:
            # ---------------- service-to-service wiring ----------------
            stack.enter_context(
                _overridden(database_app, {get_db_manager: lambda: manager})
            )
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
                    MagicMock(return_value=build_gmail_service_mock()),
                )
            )
            # The Go sync server on :8001.
            stack.enter_context(
                patch(
                    "backend.services.user_data.sync.requests.post",
                    side_effect=fake_go_sync_post,
                )
            )
            # Google Calendar. ONE binding, unlike the monolith: the public
            # calendar router, ``moodle.py`` and the CLI all resolve
            # ``google_calendar.get_calendar_service`` as a module attribute
            # (CONTRACT_FREEZE hazard 1, retired).
            stack.enter_context(
                patch.object(
                    google_calendar, "get_calendar_service", fake_get_calendar_service
                )
            )
            # Chroma + Ollama. Replaced wholesale so the real
            # ``vector_database/`` directory is never opened (addendum C2).
            stack.enter_context(patch.object(store, "collection", collection))
            stack.enter_context(patch.object(store, "embeddings", embeddings))
            # flow.fetch_token() is a live POST to oauth2.googleapis.com. Only
            # the callback's binding is patched: ``GET /api/auth/google`` goes
            # through ``google_oauth.authenticate_google_calendar``, which builds
            # its URL offline from the real credentials.json and is asserted.
            stack.enter_context(
                patch.object(accounts_oauth, "Flow", build_oauth_flow_mock())
            )
            # OpenAI. ``.env`` holds a real key and ``ask_ollama`` calls
            # ``load_dotenv()`` at import, so an unmocked path here bills money
            # and yields a non-deterministic golden. ``process_with_openai``
            # imports ``AsyncOpenAI`` at call time, so the module attribute on
            # ``openai`` is the binding it resolves.
            stack.enter_context(patch("openai.AsyncOpenAI", GoldenAsyncOpenAI))
            # Belt and braces: even an unpatched client cannot authenticate.
            stack.enter_context(
                patch.dict(
                    os.environ,
                    {"OPENAI_API_KEY": "golden-capture-not-a-real-key"},
                )
            )
            # ``/api/query``'s answer generator (itself an OpenAI call) and the
            # Ollama branch of ``process_llm_query``, which imports
            # ``slm_response`` at call time.
            stack.enter_context(
                patch.object(mcp_http_app, "llm_response", build_llm_mock())
            )
            stack.enter_context(
                patch.object(mcp_ask_ollama, "slm_response", build_llm_mock())
            )

            with TestClient(app) as client:
                yield client
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def load_app(target: str):
    """Resolve a ``"module:attribute"`` string into an ASGI app object."""
    module_name, _, attribute = target.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def perform(client: TestClient, spec: dict) -> dict:
    """Issue one request and return its normalized golden record."""
    response = client.request(
        spec["method"],
        spec["path"],
        params=spec.get("params"),
        json=spec.get("json"),
    )

    content_type = response.headers.get("content-type")
    if content_type and content_type.split(";")[0].strip() == "application/json":
        body = response.json()
    else:
        body = response.text

    return {
        "request": {
            "method": spec["method"],
            "path": spec["path"],
            "params": spec.get("params"),
            "json": spec.get("json"),
        },
        "status": response.status_code,
        "content_type": content_type,
        "ratelimit_headers": {
            header: response.headers[header]
            for header in RATELIMIT_HEADERS
            if header in response.headers
        },
        "body": normalize(body),
    }


def replay(app) -> dict[str, dict]:
    """Drive the whole ordered request list against ``app``; slug -> record."""
    records: dict[str, dict] = {}
    with harness(app) as client:
        for spec in REQUESTS:
            records[spec["slug"]] = perform(client, spec)
    return records


def golden_path(slug: str) -> Path:
    return GOLDEN_DIR / f"{slug}.json"


def write_schema_snapshot(db_file: Path, out_file: Path) -> int:
    """Snapshot `sqlite3 <db> .schema` for later diffing. Returns table count."""
    import sqlite3

    connection = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        statements = [
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
            )
        ]
    finally:
        connection.close()

    out_file.write_text("".join(f"{sql};\n" for sql in statements), encoding="utf-8")
    return sum(1 for sql in statements if sql.lstrip().upper().startswith("CREATE TABLE"))


def main() -> None:
    app = load_app(CAPTURE_APP_IMPORT)
    records = replay(app)

    for slug, record in records.items():
        golden_path(slug).write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print(f"\nwrote {len(records)} golden files to {GOLDEN_DIR}")
    for slug, record in records.items():
        print(f"  {record['status']}  {slug}.json")

    live_db = GOLDEN_DIR.parents[1] / "gmail_agent.db"
    schema_file = GOLDEN_DIR / "schema.sql"
    table_count = write_schema_snapshot(live_db, schema_file)
    print(f"wrote {schema_file} ({table_count} CREATE TABLE statements)")


if __name__ == "__main__":
    main()
