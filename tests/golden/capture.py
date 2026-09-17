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
* P3: ``POST /api/llm-query`` passes the wrong kwarg to ``process_llm_query`` and
  blows up at runtime. Whatever that produces today is the golden value.

--------------------------------------------------------------------------------
Environment dependencies
--------------------------------------------------------------------------------
``GET /api/auth/google`` builds its URL from ``credentials.json`` in the repo
root. That file is gitignored; without it the endpoint returns 500 instead of
200 and the golden will not replay. The real ``gmail_agent.db`` and
``vector_database/`` are never read or written: the database is redirected to a
throwaway temp file and every Chroma/Ollama/Google/Go/rate-limiter call is
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
import re
import shutil
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import requests
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import dependencies
from backend.databases.database import Base

GOLDEN_DIR = Path(__file__).resolve().parent

# The app the goldens are captured FROM. Always the monolith: the goldens record
# pre-refactor behaviour. test_parity.py has its own (Wave-6 repointable) target.
CAPTURE_APP_IMPORT = "backend.app:app"

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


def build_vector_db_mock():
    """Mock vector database query function (tests/conftest.py::mock_vector_db)."""
    mock_doc1 = MagicMock()
    mock_doc1.page_content = "Meeting tomorrow at 10am in conference room"
    mock_doc1.metadata = {
        "message_id": "msg_002",
        "sender": "boss@company.com",
        "subject": "Meeting Tomorrow",
        "date_sent": "2025-11-22",
    }

    mock_doc2 = MagicMock()
    mock_doc2.page_content = "Project deadline is next Friday"
    mock_doc2.metadata = {
        "message_id": "msg_003",
        "sender": "pm@company.com",
        "subject": "Project Deadline",
        "date_sent": "2025-11-21",
    }

    async def mock_query(query, top_k=3):
        # Return relevant docs based on query keywords
        if "meeting" in query.lower():
            return [mock_doc1]
        elif "deadline" in query.lower():
            return [mock_doc2]
        return [mock_doc1, mock_doc2]

    return mock_query


def build_llm_mock():
    """Mock LLM response function (tests/conftest.py::mock_llm)."""

    def mock_response(query):
        return "Based on your emails, you have a meeting tomorrow at 10am."

    return mock_response


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


@contextmanager
def harness(app):
    """Temp database + full mock set + ``TestClient`` for ``app``.

    The real ``gmail_agent.db`` is never touched: ``backend.dependencies.db_manager``
    is repointed at a throwaway SQLite file the same way
    ``tests/conftest.py::test_db`` does it.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="golden-capture-"))
    db_path = tmp_dir / "golden.db"
    test_engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine)

    original_engine = dependencies.db_manager.engine
    original_session_local = dependencies.db_manager.SessionLocal
    dependencies.db_manager.engine = test_engine
    dependencies.db_manager.SessionLocal = TestSessionLocal

    calendar_service, _events_store = build_calendar_service_mock()

    def fake_get_calendar_service(email_account_id=None):
        # The controllers unpack a (service, error) tuple.
        return calendar_service, None

    try:
        seed_database(dependencies.db_manager)

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "backend.controllers.emails.get_service",
                    MagicMock(return_value=build_gmail_service_mock()),
                )
            )
            stack.enter_context(
                patch(
                    "backend.controllers.emails.requests.post",
                    side_effect=fake_go_sync_post,
                )
            )
            stack.enter_context(
                patch("backend.controllers.emails.embed_and_store", AsyncMock())
            )
            stack.enter_context(
                patch("backend.controllers.emails.store_in_vector_db", AsyncMock())
            )
            stack.enter_context(
                patch(
                    "backend.controllers.calendar.get_calendar_service",
                    fake_get_calendar_service,
                )
            )
            # The Moodle passthrough resolves get_calendar_service through its OWN
            # module, where it would build a DatabaseManager against the REAL
            # gmail_agent.db and possibly refresh/write a token.
            stack.enter_context(
                patch(
                    "backend.services.moodle_calendar.get_calendar_service",
                    fake_get_calendar_service,
                )
            )
            stack.enter_context(
                patch(
                    "backend.controllers.llm.query_vector_db", build_vector_db_mock()
                )
            )
            stack.enter_context(
                patch("backend.controllers.llm.llm_response", build_llm_mock())
            )
            # flow.fetch_token() is a live POST to oauth2.googleapis.com.
            stack.enter_context(
                patch("backend.controllers.oauth.Flow", build_oauth_flow_mock())
            )
            stack.enter_context(
                patch.object(
                    dependencies.limiter,
                    "check",
                    MagicMock(return_value=dict(ALLOW_RESULT)),
                )
            )

            with TestClient(app) as client:
                yield client
    finally:
        dependencies.db_manager.engine = original_engine
        dependencies.db_manager.SessionLocal = original_session_local
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
