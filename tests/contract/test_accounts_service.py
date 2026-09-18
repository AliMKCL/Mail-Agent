"""
Contract tests for the Accounts service (:8010) — Spec 3.3/3.3.1, step 4.8.

Two real services, one process, no sockets and no network:

* the **Accounts** app is driven over ``httpx.ASGITransport``;
* its ``get_database_client`` provider is overridden with an
  ``AsyncServiceClient`` whose transport is another ``ASGITransport`` wrapping
  the **real Database service app**, pointed at a throwaway SQLite file under
  ``tmp_path``.

So the credential ladder really does travel Accounts → HTTP → Database → SQLite
on every assertion below, while ``gmail_agent.db`` is never opened.

``InstalledAppFlow``, ``Flow`` and ``Credentials.refresh`` are all replaced with
fakes, so no test opens a browser, reads ``credentials.json`` or calls Google.

The heart of this module is ``TestCredentialLadder``. It covers all six branches
Spec 4.8 names and, for each, asserts **both** the outcome **and** whether the
interactive flow was invoked — because the thing most likely to be broken by a
future edit is not an error string, it is the asymmetry:

* refresh **fails** → Gmail *and* Calendar open a browser;
* **no credentials at all** → Gmail opens a browser, Calendar does **not**.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from google.oauth2.credentials import Credentials

from backend.services.accounts import credentials as credentials_module
from backend.services.accounts import google_oauth
from backend.services.accounts.app import app as accounts_app
from backend.services.accounts.clients import get_database_client

ACCOUNTS_BASE_URL = "http://accounts.test"
DATABASE_BASE_URL = "http://database.test"

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"


# --------------------------------------------------------------------------
# Fakes — nothing here touches the network, a browser or credentials.json
# --------------------------------------------------------------------------


class FakeInstalledAppFlow:
    """Stands in for ``InstalledAppFlow`` in ``google_oauth``'s namespace.

    ``from_client_secrets_file`` returns the fake itself, so one instance
    records both the secrets file it was handed and every
    ``run_local_server`` invocation. ``calls`` is the interactive-re-auth
    counter the ladder tests assert on.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.secrets_files: list[str] = []
        self.result: Credentials | None = None
        self.error: Exception | None = None

    def from_client_secrets_file(self, client_secrets_file, scopes):
        self.secrets_files.append(client_secrets_file)
        self.scopes = scopes
        return self

    def run_local_server(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result

    @property
    def invoked(self) -> bool:
        return bool(self.calls)


class FakeWebFlow:
    """Stands in for ``Flow`` — the web consent flow and the callback exchange."""

    def __init__(self):
        self.redirect_uris: list[str] = []
        self.authorization_url_kwargs: list[dict] = []
        self.fetch_token_codes: list[str] = []
        self.auth_url = "https://accounts.google.com/o/oauth2/auth?fake=1"
        self.credentials: Credentials | None = None
        self.from_file_error: Exception | None = None
        self.fetch_token_error: Exception | None = None

    def from_client_secrets_file(self, client_secrets_file, scopes):
        if self.from_file_error is not None:
            raise self.from_file_error
        self.scopes = scopes
        return self

    def __setattr__(self, name, value):
        if name == "redirect_uri" and hasattr(self, "redirect_uris"):
            self.redirect_uris.append(value)
        super().__setattr__(name, value)

    def authorization_url(self, **kwargs):
        self.authorization_url_kwargs.append(kwargs)
        return self.auth_url, kwargs.get("state")

    def fetch_token(self, code=None):
        self.fetch_token_codes.append(code)
        if self.fetch_token_error is not None:
            raise self.fetch_token_error


# --------------------------------------------------------------------------
# Credential builders
# --------------------------------------------------------------------------


#: Distinguishes "caller did not choose scopes" from "caller wants no scopes",
#: which matters because ``scopes=None`` is a *meaningful* state: it is stored
#: as SQL NULL and read back as ``[]``, which is the "No scopes found" branch.
_DEFAULT_SCOPES = object()


def _creds(
    *,
    token: str = "access-token",
    refresh_token: str | None = "refresh-token",
    scopes=_DEFAULT_SCOPES,
    expiry: datetime | None = None,
) -> Credentials:
    """A ``Credentials`` object.

    ``expiry`` must stay naive UTC: ``google.auth`` compares it against a naive
    ``utcnow()``, and that is also what the ``email_tokens.expiry`` column
    round-trips.
    """
    return Credentials(
        token=token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=[GMAIL_SCOPE, CALENDAR_SCOPE] if scopes is _DEFAULT_SCOPES else scopes,
        expiry=expiry if expiry is not None else datetime.utcnow() + timedelta(hours=1),
    )


def _valid_creds(**kwargs) -> Credentials:
    creds = _creds(**kwargs)
    assert creds.valid, "fixture bug: these credentials were meant to be valid"
    return creds


def _expired_creds(**kwargs) -> Credentials:
    kwargs.setdefault("expiry", datetime.utcnow() - timedelta(hours=1))
    creds = _creds(**kwargs)
    assert creds.expired, "fixture bug: these credentials were meant to be expired"
    return creds


def _hash(password: str) -> str:
    sha256_hash = hashlib.sha256()
    sha256_hash.update(password.encode("utf-8"))
    return sha256_hash.hexdigest()


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path):
    """A ``DatabaseManager`` on a throwaway file. Never ``gmail_agent.db``."""
    from backend.services.database.manager import DatabaseManager

    return DatabaseManager(f"sqlite:///{tmp_path / 'test_gmail_agent.db'}")


@pytest.fixture
def database_client(manager):
    """A Database-service client whose transport is the real Database app."""
    from backend.libs.common.http import AsyncServiceClient
    from backend.services.database.app import app as database_app
    from backend.services.database.manager import get_db_manager

    database_app.dependency_overrides[get_db_manager] = lambda: manager
    try:
        yield AsyncServiceClient(
            DATABASE_BASE_URL, transport=httpx.ASGITransport(app=database_app)
        )
    finally:
        database_app.dependency_overrides.clear()


@pytest.fixture
async def client(database_client):
    """ASGI client for the Accounts app, wired to the in-process Database app."""
    accounts_app.dependency_overrides[get_database_client] = lambda: database_client
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=accounts_app),
            base_url=ACCOUNTS_BASE_URL,
        ) as async_client:
            yield async_client
    finally:
        accounts_app.dependency_overrides.clear()


@pytest.fixture
def installed_flow(monkeypatch) -> FakeInstalledAppFlow:
    """Replace ``InstalledAppFlow`` in the one namespace that resolves it."""
    fake = FakeInstalledAppFlow()
    monkeypatch.setattr(google_oauth, "InstalledAppFlow", fake)
    return fake


@pytest.fixture
def web_flow(monkeypatch) -> FakeWebFlow:
    """Replace ``Flow`` in **both** namespaces that bind it.

    ``google_oauth`` uses it for ``authenticate_google_calendar`` and
    ``routers.oauth`` uses it for the callback's token exchange. Patching only
    one would leave the other pointed at the real class — the same trap Wave 0
    recorded for ``get_calendar_service``.
    """
    from backend.services.accounts.routers import oauth as oauth_router

    fake = FakeWebFlow()
    monkeypatch.setattr(google_oauth, "Flow", fake)
    monkeypatch.setattr(oauth_router, "Flow", fake)
    return fake


@pytest.fixture
def account(manager):
    """One account with one primary mailbox: ``testuser@gmail.com`` / id pair."""
    row = manager.get_or_create_account("testuser@gmail.com", _hash("testpassword"))
    email_account = manager.get_or_create_email_account(
        row.id, "testuser@gmail.com", "gmail", True
    )
    return row.id, email_account.id


# --------------------------------------------------------------------------
# Public routes — registration
# --------------------------------------------------------------------------


class TestRegistration:
    async def test_signup_creates_account_and_primary_mailbox(self, client, manager):
        response = await client.post(
            "/api/auth/signup",
            json={"email": "newuser@gmail.com", "password": "hunter2"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["message"] == "Sign-up successful"
        assert isinstance(body["account_id"], int)
        assert isinstance(body["email_account_id"], int)

        stored = manager.get_account_by_email("newuser@gmail.com")
        assert stored.password_hash == _hash("hunter2")

    async def test_signup_with_existing_email_and_other_password_is_a_takeover(
        self, client, manager, account
    ):
        """🔴 **KNOWN BUG X1, PRESERVED ON PURPOSE — DO NOT "FIX" THIS.**

        ``get_or_create_account`` returns the *caller's* ``password_hash``
        attached to the *pre-existing* row's ``id``. So an attacker who knows
        someone's email address gets back a 200 and that person's
        ``account_id``, i.e. a working session for another tenant. The stored
        row is untouched, which is why the victim's password still works.

        This is a real account-takeover vector. It is deliberately out of scope
        for the microservices split, it is captured as
        ``tests/golden/post__api_auth_signup__existing_email_other_password.json``,
        and it has its own ticket. This test exists to pin the behavior so the
        split cannot silently change it — **not** to bless it.
        """
        account_id, email_account_id = account

        response = await client.post(
            "/api/auth/signup",
            json={"email": "testuser@gmail.com", "password": "attacker-password"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "success",
            "message": "Sign-up successful",
            "account_id": account_id,
            "email_account_id": email_account_id,
        }

        # The victim's stored hash is NOT overwritten — the leak is the
        # response body, not the row.
        stored = manager.get_account_by_email("testuser@gmail.com")
        assert stored.password_hash == _hash("testpassword")

    async def test_signin_valid(self, client, account):
        account_id, email_account_id = account

        response = await client.post(
            "/api/auth/signin",
            json={"email": "testuser@gmail.com", "password": "testpassword"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "success",
            "message": "Sign-in successful",
            "account_id": account_id,
            "email_account_id": email_account_id,
        }

    async def test_signin_wrong_password(self, client, account):
        response = await client.post(
            "/api/auth/signin",
            json={"email": "testuser@gmail.com", "password": "not-the-password"},
        )

        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid email or password"}

    async def test_signin_unknown_email(self, client, account):
        response = await client.post(
            "/api/auth/signin",
            json={"email": "nobody@gmail.com", "password": "testpassword"},
        )

        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid email or password"}

    async def test_signin_prefers_the_primary_mailbox(self, client, manager, account):
        """The scan returns the ``is_primary`` mailbox, not merely the first."""
        account_id, primary_id = account
        manager.get_or_create_email_account(account_id, "aaa@gmail.com", "gmail", False)

        response = await client.post(
            "/api/auth/signin",
            json={"email": "testuser@gmail.com", "password": "testpassword"},
        )

        assert response.status_code == 200
        assert response.json()["email_account_id"] == primary_id

    async def test_signin_falls_back_to_first_mailbox_when_none_is_primary(
        self, client, manager
    ):
        row = manager.get_or_create_account("nop@gmail.com", _hash("pw"))
        first = manager.get_or_create_email_account(row.id, "aaa@gmail.com", "gmail", False)
        manager.get_or_create_email_account(row.id, "zzz@gmail.com", "gmail", False)

        response = await client.post(
            "/api/auth/signin", json={"email": "nop@gmail.com", "password": "pw"}
        )

        assert response.status_code == 200
        # get_account_email_accounts orders is_primary DESC, then email.
        assert response.json()["email_account_id"] == first.id

    async def test_signin_with_account_but_no_mailbox_returns_null_id(
        self, client, manager
    ):
        manager.get_or_create_account("lonely@gmail.com", _hash("pw"))

        response = await client.post(
            "/api/auth/signin", json={"email": "lonely@gmail.com", "password": "pw"}
        )

        assert response.status_code == 200
        assert response.json()["email_account_id"] is None


# --------------------------------------------------------------------------
# Public routes — OAuth
# --------------------------------------------------------------------------


class TestOAuthRoutes:
    async def test_initiate_google_oauth(self, client, web_flow, account):
        _, email_account_id = account

        response = await client.get(
            "/api/auth/google", params={"email_account_id": email_account_id}
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "success",
            "auth_url": web_flow.auth_url,
            "state": str(email_account_id),
        }
        # Redirect URI is the literal registered in Google Cloud Console.
        assert web_flow.redirect_uris == ["http://localhost:8000/oauth/callback"]
        assert web_flow.authorization_url_kwargs == [
            {
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "state": str(email_account_id),
            }
        ]

    async def test_initiate_google_oauth_failure_is_500(self, client, web_flow):
        """The nested ``detail`` string is a preserved quirk, not a mistake.

        ``controllers/oauth.py`` has no ``except HTTPException: raise`` clause,
        so the 500 it raises for a falsy ``auth_url`` is immediately caught by
        its own ``except Exception`` and re-wrapped. ``str(HTTPException(...))``
        is ``"500: <detail>"``, which is how ``500: `` ends up in the middle of
        the message. R7 keeps it exactly as-is.
        """
        web_flow.from_file_error = RuntimeError("no client secrets")

        response = await client.get("/api/auth/google", params={"email_account_id": 1})

        assert response.status_code == 500
        assert response.json() == {
            "detail": (
                "Error initiating OAuth: 500: Failed to generate auth URL: "
                "no client secrets"
            )
        }

    async def test_oauth_callback_saves_credentials_and_returns_golden_html(
        self, client, web_flow, manager, account
    ):
        _, email_account_id = account
        web_flow.credentials = _valid_creds(token="from-callback")

        response = await client.get(
            "/oauth/callback",
            params={"code": "golden-auth-code", "state": str(email_account_id)},
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"

        # Byte-identical to the captured monolith response (R7).
        golden = json.loads((GOLDEN_DIR / "get__oauth_callback.json").read_text())
        assert response.text == golden["body"]

        assert web_flow.fetch_token_codes == ["golden-auth-code"]
        stored = manager.get_email_account_credentials(email_account_id)
        assert stored is not None
        assert stored.token == "from-callback"

    async def test_oauth_callback_without_numeric_state_saves_nothing(
        self, client, web_flow, manager, account
    ):
        _, email_account_id = account
        web_flow.credentials = _valid_creds(token="orphan")

        response = await client.get(
            "/oauth/callback", params={"code": "code", "state": "not-a-number"}
        )

        assert response.status_code == 200
        assert manager.get_email_account_credentials(email_account_id) is None

    async def test_oauth_callback_error_returns_500_html(self, client, web_flow):
        web_flow.fetch_token_error = RuntimeError("bad code")

        response = await client.get(
            "/oauth/callback", params={"code": "nope", "state": "1"}
        )

        assert response.status_code == 500
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.text == """
        <html>
            <head><title>Authorization Error</title></head>
            <body>
                <h2>Authorization failed</h2>
                <p>Error: bad code</p>
                <p>Please try again.</p>
            </body>
        </html>
        """


# --------------------------------------------------------------------------
# Public routes — email accounts
# --------------------------------------------------------------------------


class TestEmailAccountRoutes:
    async def test_get_users_for_account(self, client, account):
        account_id, email_account_id = account

        response = await client.get("/api/users", params={"account_id": account_id})

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["id"] == email_account_id
        assert body[0]["account_id"] == account_id
        assert body[0]["email"] == "testuser@gmail.com"
        assert body[0]["provider"] == "gmail"
        assert body[0]["is_primary"] == 1
        assert body[0]["has_oauth_credentials"] is False
        assert body[0]["created_at"]

    async def test_get_users_oauth_badge_is_true_for_valid_credentials(
        self, client, manager, account, installed_flow
    ):
        account_id, email_account_id = account
        manager.save_email_token(email_account_id, _valid_creds())

        response = await client.get("/api/users", params={"account_id": account_id})

        assert response.status_code == 200
        assert response.json()[0]["has_oauth_credentials"] is True
        # The badge is a raw read: no refresh, no browser.
        assert not installed_flow.invoked

    async def test_get_users_oauth_badge_is_false_for_expired_credentials(
        self, client, manager, account, installed_flow
    ):
        """Expired-with-refresh-token still reports ``false``.

        This is the point of ``refresh=false``: the badge must not refresh, and
        must not re-auth, even though the ladder could.
        """
        account_id, email_account_id = account
        manager.save_email_token(email_account_id, _expired_creds())

        response = await client.get("/api/users", params={"account_id": account_id})

        assert response.status_code == 200
        assert response.json()[0]["has_oauth_credentials"] is False
        assert not installed_flow.invoked

    async def test_get_users_without_account_id_returns_every_tenant(
        self, client, manager, account
    ):
        """X2, preserved: no ``account_id`` leaks every mailbox."""
        other = manager.get_or_create_account("other@gmail.com", _hash("pw"))
        manager.get_or_create_email_account(other.id, "other@gmail.com", "gmail", True)

        response = await client.get("/api/users")

        assert response.status_code == 200
        assert {row["email"] for row in response.json()} == {
            "testuser@gmail.com",
            "other@gmail.com",
        }

    async def test_get_email_account_info(self, client, account):
        account_id, email_account_id = account

        response = await client.get(f"/api/email-account/{email_account_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == email_account_id
        assert body["account_id"] == account_id
        assert body["email"] == "testuser@gmail.com"
        assert body["provider"] == "gmail"
        assert body["is_primary"] == 1
        assert "created_at" in body
        # No OAuth badge on this route, unlike /api/users.
        assert "has_oauth_credentials" not in body

    async def test_get_email_account_info_missing_is_404(self, client):
        response = await client.get("/api/email-account/99999")

        assert response.status_code == 404
        assert response.json() == {"detail": "Email account not found"}

    async def test_create_user(self, client, account):
        account_id, _ = account

        response = await client.post(
            "/api/users",
            json={"email": "extra@gmail.com", "name": "Extra Mailbox", "account_id": account_id},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "extra@gmail.com"
        assert body["account_id"] == account_id
        assert body["is_primary"] == 0
        assert body["message"] == (
            "Email account added successfully. Please authenticate with Google."
        )
        # The route returns no "provider" key — matches the monolith exactly.
        assert "provider" not in body

    async def test_create_user_without_account_id_is_400(self, client):
        response = await client.post(
            "/api/users", json={"email": "orphan@gmail.com", "name": "Orphan"}
        )

        assert response.status_code == 400
        assert response.json() == {
            "detail": "account_id is required. User must be logged in to add email accounts."
        }

    async def test_create_user_with_unknown_account_is_404(self, client):
        response = await client.post(
            "/api/users", json={"email": "nobody@gmail.com", "account_id": 4242}
        )

        assert response.status_code == 404
        # This route's own wording, not the Database service's.
        assert response.json() == {"detail": "Account with id 4242 not found"}

    async def test_create_user_with_empty_email_is_400(self, client, account):
        account_id, _ = account

        response = await client.post(
            "/api/users", json={"email": "", "account_id": account_id}
        )

        assert response.status_code == 400
        assert response.json() == {"detail": "Email is required"}


# --------------------------------------------------------------------------
# THE CREDENTIAL LADDER — Spec 3.3.1
# --------------------------------------------------------------------------

CREDENTIALS_PATH = "/internal/email-accounts/{id}/credentials"

GMAIL_PARAMS = {"allow_interactive": "true"}
CALENDAR_PARAMS = {"require_scope": "calendar", "allow_interactive": "false"}
BADGE_PARAMS = {"refresh": "false"}


class TestCredentialLadder:
    """All six branches of Spec 4.8, for each of the three parity rows.

    Every test asserts the outcome **and** ``installed_flow.invoked``. The
    second half is what pins the asymmetry; without it a refactor could move the
    browser prompt onto the Calendar path and every status code would still
    match.
    """

    # ---- branch 1: valid --------------------------------------------------

    async def test_valid_credentials_gmail(self, client, manager, account, installed_flow):
        _, email_account_id = account
        manager.save_email_token(email_account_id, _valid_creds(token="good"))

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )

        assert response.status_code == 200
        assert response.json()["token"] == "good"
        assert not installed_flow.invoked

    async def test_valid_credentials_calendar(
        self, client, manager, account, installed_flow
    ):
        _, email_account_id = account
        manager.save_email_token(email_account_id, _valid_creds(token="good"))

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )

        assert response.status_code == 200
        body = response.json()
        assert body["token"] == "good"
        assert CALENDAR_SCOPE in body["scopes"]
        assert not installed_flow.invoked

    async def test_valid_credentials_badge(self, client, manager, account, installed_flow):
        _, email_account_id = account
        manager.save_email_token(email_account_id, _valid_creds(token="good"))

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=BADGE_PARAMS
        )

        assert response.status_code == 200
        assert response.json()["token"] == "good"
        assert not installed_flow.invoked

    # ---- branch 2: expired, refresh works ---------------------------------

    @staticmethod
    def _working_refresh(monkeypatch):
        def refresh(self, request):
            self.token = "refreshed-token"
            self.expiry = datetime.utcnow() + timedelta(hours=1)

        monkeypatch.setattr(Credentials, "refresh", refresh)

    async def test_expired_with_working_refresh_gmail(
        self, client, manager, account, installed_flow, monkeypatch
    ):
        _, email_account_id = account
        manager.save_email_token(email_account_id, _expired_creds(token="stale"))
        self._working_refresh(monkeypatch)

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )

        assert response.status_code == 200
        assert response.json()["token"] == "refreshed-token"
        assert not installed_flow.invoked
        # The refreshed token is written back through the Database service.
        assert manager.get_email_account_credentials(email_account_id).token == (
            "refreshed-token"
        )

    async def test_expired_with_working_refresh_calendar(
        self, client, manager, account, installed_flow, monkeypatch
    ):
        _, email_account_id = account
        manager.save_email_token(email_account_id, _expired_creds(token="stale"))
        self._working_refresh(monkeypatch)

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )

        assert response.status_code == 200
        assert response.json()["token"] == "refreshed-token"
        assert not installed_flow.invoked

    # ---- branch 3: expired, refresh fails ---------------------------------

    @staticmethod
    def _failing_refresh(monkeypatch):
        def refresh(self, request):
            raise RuntimeError("invalid_grant")

        monkeypatch.setattr(Credentials, "refresh", refresh)

    async def test_expired_with_failing_refresh_gmail_runs_interactive(
        self, client, manager, account, installed_flow, monkeypatch
    ):
        _, email_account_id = account
        manager.save_email_token(email_account_id, _expired_creds(token="stale"))
        self._failing_refresh(monkeypatch)
        installed_flow.result = _valid_creds(token="reauthed")

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )

        assert response.status_code == 200
        assert response.json()["token"] == "reauthed"
        assert installed_flow.invoked

    async def test_expired_with_failing_refresh_calendar_ALSO_runs_interactive(
        self, client, manager, account, installed_flow, monkeypatch
    ):
        """⚠️ Half of the asymmetry: Calendar **does** open a browser here.

        ``allow_interactive=false`` does *not* mean "never interactive". It
        means "not interactive when there are no credentials". On a failed
        refresh ``setup_calendar.get_calendar_service`` calls
        ``reauthenticate_user_token_failure`` directly, and so must this.
        """
        _, email_account_id = account
        manager.save_email_token(email_account_id, _expired_creds(token="stale"))
        self._failing_refresh(monkeypatch)
        installed_flow.result = _valid_creds(token="reauthed")

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )

        assert response.status_code == 200
        assert response.json()["token"] == "reauthed"
        assert installed_flow.invoked, (
            "Calendar must run interactive re-auth when a refresh FAILS "
            "(Spec 3.3.1); only the no-credentials branch skips the browser"
        )

    async def test_failing_refresh_then_failing_reauth_gmail_error_string(
        self, client, manager, account, installed_flow, monkeypatch
    ):
        """``gmail_read.py:80`` — the *failed-refresh* wording, not ``:89``'s.

        ``get_service`` raises ``"Re-authentication failed for email account
        N. ..."`` from the ``except`` branch of its refresh attempt;
        ``"Authentication failed for email account N. ..."`` belongs to its bare
        ``else``. Two different strings for two different branches, and
        ``/api/sync`` folds both into its ``detail``, so both are observable and
        neither may be collapsed into the other.
        """
        _, email_account_id = account
        manager.save_email_token(email_account_id, _expired_creds(token="stale"))
        self._failing_refresh(monkeypatch)
        installed_flow.error = RuntimeError("user closed the browser")

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )

        assert response.status_code == 409
        assert response.json() == {
            "error": (
                f"Re-authentication failed for email account {email_account_id}. "
                "Cannot proceed without valid credentials."
            )
        }
        assert installed_flow.invoked

    async def test_failing_refresh_then_failing_reauth_calendar_error_string(
        self, client, manager, account, installed_flow, monkeypatch
    ):
        """Calendar's wording differs from Gmail's — both are load-bearing."""
        _, email_account_id = account
        manager.save_email_token(email_account_id, _expired_creds(token="stale"))
        self._failing_refresh(monkeypatch)
        installed_flow.error = RuntimeError("user closed the browser")

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )

        assert response.status_code == 409
        assert response.json() == {"error": "Re-authentication failed"}
        assert installed_flow.invoked

    async def test_badge_never_refreshes_even_when_it_could(
        self, client, manager, account, installed_flow, monkeypatch
    ):
        _, email_account_id = account
        manager.save_email_token(email_account_id, _expired_creds(token="stale"))
        self._working_refresh(monkeypatch)

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=BADGE_PARAMS
        )

        assert response.status_code == 200
        assert response.json()["token"] == "stale"
        assert not installed_flow.invoked
        assert manager.get_email_account_credentials(email_account_id).token == "stale"

    # ---- branch 4: no credentials at all ----------------------------------

    async def test_no_credentials_gmail_opens_the_browser(
        self, client, account, installed_flow
    ):
        """⚠️ The other half of the asymmetry: Gmail **does** open a browser."""
        _, email_account_id = account
        installed_flow.result = _valid_creds(token="first-time")

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )

        assert response.status_code == 200
        assert response.json()["token"] == "first-time"
        assert installed_flow.invoked

    async def test_no_credentials_calendar_does_NOT_open_the_browser(
        self, client, account, installed_flow
    ):
        """⚠️ **The single most important assertion in this file.**

        ``setup_calendar.get_calendar_service`` reaches its ``else`` branch when
        nothing is stored and returns ``(None, "Authentication required")``
        *without* running an OAuth flow. Reproducing that exactly is the whole
        reason ``allow_interactive`` exists.
        """
        _, email_account_id = account
        installed_flow.result = _valid_creds(token="should-never-be-used")

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )

        assert response.status_code == 409
        assert response.json() == {"error": "Authentication required"}
        assert not installed_flow.invoked, (
            "Calendar must NOT open a browser when there are no credentials "
            "(Spec 3.3.1) — that is the asymmetry this refactor must preserve"
        )

    async def test_no_credentials_gmail_with_failing_flow_is_409(
        self, client, account, installed_flow
    ):
        _, email_account_id = account
        installed_flow.error = RuntimeError("consent denied")

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )

        assert response.status_code == 409
        assert response.json() == {
            "error": (
                f"Authentication failed for email account {email_account_id}. "
                "Cannot proceed without valid credentials."
            )
        }
        assert installed_flow.invoked

    async def test_no_credentials_badge_is_409_without_a_browser(
        self, client, account, installed_flow
    ):
        _, email_account_id = account

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=BADGE_PARAMS
        )

        assert response.status_code == 409
        assert response.json() == {"error": "Authentication required"}
        assert not installed_flow.invoked

    async def test_invalid_unrefreshable_credentials_calendar_is_authentication_required(
        self, client, manager, account, installed_flow
    ):
        """Expired with **no** refresh token: no refresh is possible.

        ``setup_calendar.py:95-97``'s ``else`` is bare too, so it catches these
        unusable credentials exactly as it catches "nothing stored": Calendar
        reports ``"Authentication required"`` and never opens a browser. The
        next test feeds the *same* credentials to the Gmail row, which consents
        instead — the two together pin the asymmetry.
        """
        _, email_account_id = account
        manager.save_email_token(
            email_account_id, _expired_creds(token="stale", refresh_token=None)
        )

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )

        assert response.status_code == 409
        assert response.json() == {"error": "Authentication required"}
        assert not installed_flow.invoked

    async def test_invalid_unrefreshable_credentials_gmail_opens_the_browser(
        self, client, manager, account, installed_flow
    ):
        """Expired with **no** refresh token: Gmail opens a browser anyway.

        ``gmail_read.py:81-89`` guards this branch with a **bare** ``else`` on
        ``if creds and creds.expired and creds.refresh_token``, so "nothing
        stored" and "stored but unusable" land in exactly the same place and
        both run ``reauthenticate_user_token_failure``. There is no
        ``if not creds:`` narrowing it, and nothing returns the unusable
        credentials to the caller.

        The failure wording here is ``gmail_read.py:89``'s *no-credentials*
        sentence, ``"Authentication failed for ..."`` — not ``:80``'s
        ``"Re-authentication failed for ..."``, which is raised only from the
        refresh ``except`` branch.

        Read this together with its mirror,
        ``test_invalid_unrefreshable_credentials_calendar_is_authentication_required``
        above: identical stored credentials, opposite row, opposite outcome —
        Gmail consents, Calendar reports ``"Authentication required"`` and never
        opens a browser. That pair is the asymmetry ``allow_interactive`` exists
        to preserve.
        """
        _, email_account_id = account
        manager.save_email_token(
            email_account_id, _expired_creds(token="stale", refresh_token=None)
        )
        installed_flow.error = RuntimeError("consent denied")

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )

        assert response.status_code == 409
        assert response.json() == {
            "error": (
                f"Authentication failed for email account {email_account_id}. "
                "Cannot proceed without valid credentials."
            )
        }
        assert installed_flow.invoked, (
            "gmail_read's else is bare: credentials that are present but "
            "unusable must still open the browser (gmail_read.py:81-89)"
        )

    # ---- branches 5 and 6: scope checks -----------------------------------

    async def test_missing_calendar_scope(self, client, manager, account, installed_flow):
        _, email_account_id = account
        manager.save_email_token(email_account_id, _valid_creds(scopes=[GMAIL_SCOPE]))

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )

        assert response.status_code == 409
        assert response.json() == {
            "error": "Authentication required - Calendar scope missing"
        }
        assert not installed_flow.invoked

    async def test_no_scopes_present(self, client, manager, account, installed_flow):
        """``scopes=None`` is stored as SQL NULL and read back as ``[]``."""
        _, email_account_id = account
        manager.save_email_token(email_account_id, _valid_creds(scopes=None))

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )

        assert response.status_code == 409
        assert response.json() == {"error": "Authentication required - No scopes found"}
        assert not installed_flow.invoked

    async def test_gmail_row_does_not_apply_a_scope_check(
        self, client, manager, account, installed_flow
    ):
        """No ``require_scope`` means no scope check, even with empty scopes."""
        _, email_account_id = account
        manager.save_email_token(email_account_id, _valid_creds(scopes=None))

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )

        assert response.status_code == 200
        assert response.json()["scopes"] is None
        assert not installed_flow.invoked

    async def test_unsupported_require_scope_is_rejected_by_validation(
        self, client, account
    ):
        _, email_account_id = account

        response = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id),
            params={"require_scope": "drive"},
        )

        assert response.status_code == 422

    # ---- the flow's own inputs -------------------------------------------

    async def test_interactive_flow_uses_the_configured_secrets_file_and_port(
        self, client, account, installed_flow
    ):
        _, email_account_id = account
        installed_flow.result = _valid_creds()

        await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )

        assert installed_flow.secrets_files == ["credentials.json"]
        assert installed_flow.scopes == [GMAIL_SCOPE, CALENDAR_SCOPE]
        kwargs = installed_flow.calls[0]
        assert kwargs["host"] == "localhost"
        assert kwargs["port"] == 8080
        assert kwargs["open_browser"] is True
        assert kwargs["access_type"] == "offline"
        assert kwargs["prompt"] == "consent"

    # ---- regression guards: the per-row wording cannot be unified ---------

    async def test_gmail_row_console_output_is_gmail_reads_wording(
        self, client, manager, account, installed_flow, monkeypatch, capsys
    ):
        """``gmail_read`` prints emoji-free sentences, and stdout is the
        operator interface for the re-auth CLIs, so the wording is a contract.

        Both Gmail branches are covered: the refresh ``except`` block
        (``gmail_read.py:72-74``) and the bare ``else``
        (``gmail_read.py:82-83``). The negative assertions are the point — the
        Calendar row's emoji lines *contain* the Gmail wording as a substring,
        so only ruling them out catches a collapse back to one wording.
        """
        _, email_account_id = account
        installed_flow.error = RuntimeError("consent denied")

        # Branch: nothing usable stored -> gmail_read.py:82-83.
        await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )
        out = capsys.readouterr().out

        assert f"No valid credentials found for email account {email_account_id}" in out
        assert "   Triggering initial authentication..." in out
        assert f"⚠️  No valid credentials for email account {email_account_id}" not in out

        # Branch: expired, refresh raises -> gmail_read.py:65 and 72-74.
        manager.save_email_token(email_account_id, _expired_creds(token="stale"))
        self._failing_refresh(monkeypatch)

        await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )
        out = capsys.readouterr().out

        assert (
            "Attempting to refresh expired token for email account "
            f"{email_account_id}..." in out
        )
        assert (
            f"Token refresh failed for email account {email_account_id}: invalid_grant"
            in out
        )
        assert "   This usually means the refresh token is expired or revoked." in out
        assert "   Triggering re-authentication..." in out
        assert "🔄 Attempting to refresh expired token" not in out
        assert "❌ Error refreshing credentials" not in out

    async def test_calendar_row_console_output_is_setup_calendars_wording(
        self, client, manager, account, installed_flow, monkeypatch, capsys
    ):
        """``setup_calendar`` prints the emoji variants of the same events.

        Companion to the Gmail test above: same two branches
        (``setup_calendar.py:81``, ``87-88`` and ``96``), different strings.
        Together they make the ``allow_interactive`` branching in
        ``credentials.resolve_valid_credentials`` unremovable.
        """
        _, email_account_id = account
        installed_flow.error = RuntimeError("consent denied")

        # Branch: nothing usable stored -> setup_calendar.py:96.
        await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )
        out = capsys.readouterr().out

        assert f"⚠️  No valid credentials for email account {email_account_id}" in out
        assert (
            f"No valid credentials found for email account {email_account_id}" not in out
        )
        assert "   Triggering initial authentication..." not in out

        # Branch: expired, refresh raises -> setup_calendar.py:81 and 87-88.
        manager.save_email_token(email_account_id, _expired_creds(token="stale"))
        self._failing_refresh(monkeypatch)

        await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )
        out = capsys.readouterr().out

        assert (
            "🔄 Attempting to refresh expired token for email account "
            f"{email_account_id}..." in out
        )
        assert (
            "❌ Error refreshing credentials for email account "
            f"{email_account_id}: invalid_grant" in out
        )
        assert "   Triggering re-authentication..." in out
        assert "This usually means the refresh token is expired or revoked." not in out

    async def test_failed_refresh_error_strings_differ_between_rows(
        self, client, manager, account, installed_flow, monkeypatch
    ):
        """Both values asserted side by side, so neither can be "tidied up".

        Same branch, same stored credentials, same failing flow: ``gmail_read
        .py:80`` raises a full sentence naming the account, ``setup_calendar
        .py:94`` returns the bare fragment. A future reader who thinks one of
        them is a typo has to delete this assertion to act on it.
        """
        _, email_account_id = account
        manager.save_email_token(email_account_id, _expired_creds(token="stale"))
        self._failing_refresh(monkeypatch)
        installed_flow.error = RuntimeError("user closed the browser")

        gmail = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=GMAIL_PARAMS
        )
        calendar = await client.get(
            CREDENTIALS_PATH.format(id=email_account_id), params=CALENDAR_PARAMS
        )

        assert gmail.status_code == 409
        assert calendar.status_code == 409
        gmail_error = gmail.json()["error"]
        calendar_error = calendar.json()["error"]

        assert gmail_error == (
            f"Re-authentication failed for email account {email_account_id}. "
            "Cannot proceed without valid credentials."
        )
        assert calendar_error == "Re-authentication failed"
        assert gmail_error != calendar_error


# --------------------------------------------------------------------------
# Internal routes
# --------------------------------------------------------------------------


class TestInternalCredentialWrites:
    async def test_put_credentials_round_trip(self, client, manager, account):
        _, email_account_id = account
        creds = _valid_creds(token="pushed-back")

        from backend.libs.contracts.accounts import credentials_to_dto

        response = await client.put(
            f"/internal/email-accounts/{email_account_id}/credentials",
            json=credentials_to_dto(creds).model_dump(mode="json"),
        )

        assert response.status_code == 204
        assert response.content == b""
        stored = manager.get_email_account_credentials(email_account_id)
        assert stored.token == "pushed-back"
        assert stored.refresh_token == "refresh-token"
        assert sorted(stored.scopes) == sorted([GMAIL_SCOPE, CALENDAR_SCOPE])

    async def test_reauth_forces_the_flow_and_returns_credentials(
        self, client, manager, account, installed_flow
    ):
        """Unconditional: stored valid credentials are not consulted."""
        _, email_account_id = account
        manager.save_email_token(email_account_id, _valid_creds(token="already-fine"))
        installed_flow.result = _valid_creds(token="forced")

        response = await client.post(
            f"/internal/email-accounts/{email_account_id}/reauth"
        )

        assert response.status_code == 200
        assert response.json()["token"] == "forced"
        assert installed_flow.invoked
        assert manager.get_email_account_credentials(email_account_id).token == "forced"

    async def test_reauth_failure_is_409_with_the_gmail_wording(
        self, client, account, installed_flow
    ):
        _, email_account_id = account
        installed_flow.error = RuntimeError("nope")

        response = await client.post(
            f"/internal/email-accounts/{email_account_id}/reauth"
        )

        assert response.status_code == 409
        assert response.json() == {
            "error": (
                f"Authentication failed for email account {email_account_id}. "
                "Cannot proceed without valid credentials."
            )
        }

    async def test_auth_url(self, client, web_flow):
        response = await client.get(
            "/internal/oauth/auth-url", params={"email_account_id": 7}
        )

        assert response.status_code == 200
        assert response.json() == {"auth_url": web_flow.auth_url, "state": "7"}

    async def test_auth_url_without_id_uses_the_default_state(self, client, web_flow):
        response = await client.get("/internal/oauth/auth-url")

        assert response.status_code == 200
        assert response.json() == {"auth_url": web_flow.auth_url, "state": "default"}

    async def test_auth_url_failure_is_200_with_the_exception_text(
        self, client, web_flow
    ):
        """``authenticate_google_calendar`` returns ``(None, str(e))``."""
        web_flow.from_file_error = RuntimeError("missing credentials.json")

        response = await client.get("/internal/oauth/auth-url")

        assert response.status_code == 200
        assert response.json() == {
            "auth_url": None,
            "state": "missing credentials.json",
        }


class TestInternalIdentity:
    async def test_list_accounts_omits_secrets(self, client, account):
        response = await client.get("/internal/accounts")

        assert response.status_code == 200
        body = response.json()
        assert [row["primary_email"] for row in body] == ["testuser@gmail.com"]
        assert "password_hash" not in body[0]

    async def test_account_detail(self, client, manager, account):
        account_id, email_account_id = account
        manager.get_or_create_email_account(account_id, "extra@gmail.com", "gmail", False)

        response = await client.get(f"/internal/accounts/{account_id}/detail")

        assert response.status_code == 200
        body = response.json()
        assert body["account"]["id"] == account_id
        assert body["account"]["primary_email"] == "testuser@gmail.com"
        assert "password_hash" not in body["account"]
        assert {row["email"] for row in body["email_accounts"]} == {
            "testuser@gmail.com",
            "extra@gmail.com",
        }

    async def test_account_detail_missing_is_404(self, client):
        response = await client.get("/internal/accounts/4242/detail")

        assert response.status_code == 404
        assert response.json() == {"detail": "Account not found"}

    async def test_list_email_accounts_filtered_and_unfiltered(
        self, client, manager, account
    ):
        account_id, _ = account
        other = manager.get_or_create_account("other@gmail.com", _hash("pw"))
        manager.get_or_create_email_account(other.id, "other@gmail.com", "gmail", True)

        filtered = await client.get(
            "/internal/email-accounts", params={"account_id": account_id}
        )
        assert filtered.status_code == 200
        assert [row["email"] for row in filtered.json()] == ["testuser@gmail.com"]

        everything = await client.get("/internal/email-accounts")
        assert everything.status_code == 200
        assert {row["email"] for row in everything.json()} == {
            "testuser@gmail.com",
            "other@gmail.com",
        }

    async def test_get_email_account(self, client, account):
        account_id, email_account_id = account

        response = await client.get(f"/internal/email-accounts/{email_account_id}")

        assert response.status_code == 200
        assert response.json()["email"] == "testuser@gmail.com"
        assert response.json()["account_id"] == account_id

    async def test_get_email_account_missing_is_404(self, client):
        response = await client.get("/internal/email-accounts/99999")

        assert response.status_code == 404
        assert response.json() == {"detail": "Email account not found"}

    async def test_email_account_detail_carries_the_parent_primary_email(
        self, client, account
    ):
        _, email_account_id = account

        response = await client.get(
            f"/internal/email-accounts/{email_account_id}/detail"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["email_account"]["id"] == email_account_id
        assert body["primary_email"] == "testuser@gmail.com"

    async def test_email_account_detail_missing_is_404(self, client):
        response = await client.get("/internal/email-accounts/99999/detail")

        assert response.status_code == 404
        assert response.json() == {"detail": "Email account not found"}

    async def test_stats(self, client, manager, account):
        account_id, _ = account
        manager.get_or_create_email_account(account_id, "extra@gmail.com", "gmail", False)

        response = await client.get("/internal/stats")

        assert response.status_code == 200
        assert response.json() == {"total_accounts": 1, "total_email_accounts": 2}


class TestPrimaryEmailAccount:
    """``get_primary_email_account_id`` moved verbatim — fallbacks included."""

    async def test_returns_the_primary_mailbox(self, client, manager, account):
        account_id, primary_id = account
        secondary = manager.get_or_create_email_account(
            account_id, "extra@gmail.com", "gmail", False
        )

        response = await client.get(
            f"/internal/email-accounts/{secondary.id}/primary"
        )

        assert response.status_code == 200
        assert response.json() == {"email_account_id": primary_id}

    async def test_missing_email_account_falls_back_to_the_input_id(self, client):
        """Fallback 1 — never a 404, by contract."""
        response = await client.get("/internal/email-accounts/99999/primary")

        assert response.status_code == 200
        assert response.json() == {"email_account_id": 99999}

    async def test_no_primary_falls_back_to_the_first_mailbox(self, client, manager):
        row = manager.get_or_create_account("nop@gmail.com", _hash("pw"))
        first = manager.get_or_create_email_account(row.id, "aaa@gmail.com", "gmail", False)
        last = manager.get_or_create_email_account(row.id, "zzz@gmail.com", "gmail", False)

        response = await client.get(f"/internal/email-accounts/{last.id}/primary")

        assert response.status_code == 200
        assert response.json() == {"email_account_id": first.id}


# --------------------------------------------------------------------------
# google_oauth helpers exercised directly
# --------------------------------------------------------------------------


class TestAuthenticateCalendar:
    """``authenticate_calendar`` — the operator bootstrap, not a route.

    It has a pre-check ``get_calendar_service`` lacks: valid credentials that
    are missing the Calendar scope are discarded and re-acquired interactively
    rather than reported as an error.
    """

    async def test_valid_credentials_short_circuit(
        self, database_client, manager, account, installed_flow
    ):
        _, email_account_id = account
        manager.save_email_token(email_account_id, _valid_creds())

        assert await google_oauth.authenticate_calendar(
            email_account_id, db=database_client
        )
        assert not installed_flow.invoked

    async def test_valid_credentials_without_calendar_scope_force_reauth(
        self, database_client, manager, account, installed_flow
    ):
        _, email_account_id = account
        manager.save_email_token(email_account_id, _valid_creds(scopes=[GMAIL_SCOPE]))
        installed_flow.result = _valid_creds()

        assert await google_oauth.authenticate_calendar(
            email_account_id, db=database_client
        )
        assert installed_flow.invoked

    async def test_returns_false_when_the_secrets_file_is_absent(
        self, database_client, account, installed_flow, monkeypatch
    ):
        """The ``os.path.exists`` guard, kept from ``setup_calendar``."""
        _, email_account_id = account
        monkeypatch.setattr(google_oauth.os.path, "exists", lambda path: False)

        assert (
            await google_oauth.authenticate_calendar(
                email_account_id, db=database_client
            )
            is False
        )
        assert not installed_flow.invoked

    async def test_returns_false_when_reauth_fails(
        self, database_client, account, installed_flow, monkeypatch
    ):
        _, email_account_id = account
        monkeypatch.setattr(google_oauth.os.path, "exists", lambda path: True)
        installed_flow.error = RuntimeError("denied")

        assert (
            await google_oauth.authenticate_calendar(
                email_account_id, db=database_client
            )
            is False
        )
        assert installed_flow.invoked


class TestReauthAllEmailAccounts:
    async def test_empty_database(self, database_client, installed_flow):
        assert await google_oauth.reauth_all_email_accounts(database_client) == {}
        assert not installed_flow.invoked

    async def test_reports_per_account_success(
        self, database_client, manager, account, installed_flow
    ):
        account_id, primary_id = account
        secondary = manager.get_or_create_email_account(
            account_id, "extra@gmail.com", "gmail", False
        )
        installed_flow.result = _valid_creds()

        results = await google_oauth.reauth_all_email_accounts(database_client)

        assert results == {primary_id: True, secondary.id: True}
        assert len(installed_flow.calls) == 2

    async def test_reports_failure_without_raising(
        self, database_client, account, installed_flow
    ):
        _, primary_id = account
        installed_flow.error = RuntimeError("denied")

        results = await google_oauth.reauth_all_email_accounts(database_client)

        assert results == {primary_id: False}


class TestResolveValidCredentialsGuards:
    async def test_unknown_require_scope_raises(self, database_client, account):
        _, email_account_id = account

        with pytest.raises(ValueError, match="Unsupported require_scope"):
            await credentials_module.resolve_valid_credentials(
                email_account_id, require_scope="drive", db=database_client
            )

    def test_gmail_error_string_is_byte_identical_to_gmail_read(self):
        assert credentials_module.authentication_failed_error(7) == (
            "Authentication failed for email account 7. "
            "Cannot proceed without valid credentials."
        )

    def test_scope_error_strings_are_byte_identical_to_setup_calendar(self):
        assert credentials_module.NO_CREDENTIALS_ERROR == "Authentication required"
        assert credentials_module.NO_SCOPES_ERROR == (
            "Authentication required - No scopes found"
        )
        assert credentials_module.REAUTH_FAILED_ERROR == "Re-authentication failed"
        assert credentials_module._SCOPE_REQUIREMENTS["calendar"][1] == (
            "Authentication required - Calendar scope missing"
        )
        assert credentials_module._SCOPE_REQUIREMENTS["calendar"][0] == (
            "https://www.googleapis.com/auth/calendar"
        )
