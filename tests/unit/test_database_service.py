"""
Unit tests for the Database service (:8030) — Spec 3.2, step 2.4.

The service is driven in-process over ``httpx.ASGITransport`` against
``backend.services.database.app:app``, with ``get_db_manager`` overridden to a
``DatabaseManager`` pointed at a throwaway SQLite file under ``tmp_path``. The
real ``gmail_agent.db`` is never opened by these tests.

Besides covering every route in the Spec 3.2 table, this module pins the
behaviors the refactor must preserve: the X1 sign-up quirk, ``save_emails``
idempotency on ``(email_account_id, message_id)``, the X3 missing unique
constraint on ``emails.message_id``, result ordering, and a token round-trip.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from backend.services.database.app import app
from backend.services.database.manager import DatabaseManager, get_db_manager
from backend.services.database.models import Account, Email

BASE_URL = "http://database.test"


@pytest.fixture
def manager(tmp_path) -> DatabaseManager:
    """A ``DatabaseManager`` on a throwaway database, never the real one."""
    return DatabaseManager(f"sqlite:///{tmp_path / 'test_gmail_agent.db'}")


@pytest.fixture
async def client(manager: DatabaseManager):
    """ASGI client whose service resolves ``get_db_manager`` to ``manager``."""
    app.dependency_overrides[get_db_manager] = lambda: manager
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE_URL
        ) as async_client:
            yield async_client
    finally:
        app.dependency_overrides.clear()


async def _make_account(client, email: str = "owner@example.com", password_hash: str = "hash-1") -> int:
    response = await client.post(
        "/accounts/get-or-create",
        json={"primary_email": email, "password_hash": password_hash},
    )
    assert response.status_code == 200
    return response.json()["id"]


async def _make_email_account(
    client, account_id: int, email: str, provider: str = "gmail", is_primary: bool = False
) -> int:
    response = await client.post(
        "/email-accounts/get-or-create",
        json={
            "account_id": account_id,
            "email": email,
            "provider": provider,
            "is_primary": is_primary,
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def _email_payload(message_id: str, **overrides) -> dict:
    payload = {
        "message_id": message_id,
        "subject": f"subject {message_id}",
        "sender": "sender@example.com",
        "recipient": "owner@example.com",
        "date_sent": "2026-01-01T10:00:00",
        "snippet": "snippet",
        "body_text": "body text",
        "body_html": "<p>body html</p>",
    }
    payload.update(overrides)
    return payload


# --- health -----------------------------------------------------------------


async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "database"}


# --- /accounts --------------------------------------------------------------


async def test_get_or_create_account_creates_then_reuses(client):
    first = await client.post(
        "/accounts/get-or-create",
        json={"primary_email": "new@example.com", "password_hash": "hash-1"},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["primary_email"] == "new@example.com"
    assert body["created_at"] is not None
    # The manager returns a never-flushed object, so updated_at is absent today.
    assert body["updated_at"] is None
    # The secret is not echoed back on this route.
    assert "password_hash" not in body

    second = await client.post(
        "/accounts/get-or-create",
        json={"primary_email": "new@example.com", "password_hash": "hash-1"},
    )
    assert second.json()["id"] == body["id"]


async def test_get_or_create_account_preserves_x1(client, manager):
    """X1: an existing email + any password still yields the existing id.

    The stored hash must be untouched, which is what makes this an
    account-takeover vector rather than a password reset. Preserved on purpose.
    """
    victim_id = await _make_account(client, "victim@example.com", "victim-hash")

    attacker = await client.post(
        "/accounts/get-or-create",
        json={"primary_email": "victim@example.com", "password_hash": "attacker-hash"},
    )
    assert attacker.status_code == 200
    assert attacker.json()["id"] == victim_id

    with manager.get_session() as session:
        stored = session.query(Account).filter(Account.id == victim_id).first()
        assert stored.password_hash == "victim-hash"

    secrets = await client.get("/accounts", params={"include_secrets": True})
    assert [row["password_hash"] for row in secrets.json()] == ["victim-hash"]


async def test_list_accounts_orders_by_primary_email_and_hides_secrets(client):
    await _make_account(client, "zoe@example.com", "hash-z")
    await _make_account(client, "adam@example.com", "hash-a")

    response = await client.get("/accounts")
    assert response.status_code == 200
    rows = response.json()
    assert [row["primary_email"] for row in rows] == ["adam@example.com", "zoe@example.com"]
    assert all("password_hash" not in row for row in rows)


async def test_list_accounts_include_secrets(client):
    await _make_account(client, "adam@example.com", "hash-a")

    response = await client.get("/accounts", params={"include_secrets": "true"})
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "primary_email": "adam@example.com",
            "password_hash": "hash-a",
            "created_at": response.json()[0]["created_at"],
            "updated_at": response.json()[0]["updated_at"],
        }
    ]


async def test_list_accounts_by_primary_email(client):
    account_id = await _make_account(client, "owner@example.com", "hash-1")
    await _make_account(client, "other@example.com", "hash-2")

    hit = await client.get("/accounts", params={"primary_email": "owner@example.com"})
    assert hit.status_code == 200
    assert [row["id"] for row in hit.json()] == [account_id]

    miss = await client.get("/accounts", params={"primary_email": "nobody@example.com"})
    assert miss.status_code == 200
    assert miss.json() == []


async def test_get_account_by_id(client):
    account_id = await _make_account(client)

    response = await client.get(f"/accounts/{account_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == account_id
    assert body["primary_email"] == "owner@example.com"
    # Direct query, so the persisted updated_at is present here.
    assert body["updated_at"] is not None

    missing = await client.get("/accounts/9999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Account not found"}


# --- /email-accounts --------------------------------------------------------


async def test_get_or_create_email_account_creates_then_reuses(client):
    account_id = await _make_account(client)

    first = await client.post(
        "/email-accounts/get-or-create",
        json={
            "account_id": account_id,
            "email": "mail@gmail.com",
            "provider": "gmail",
            "is_primary": True,
        },
    )
    assert first.status_code == 200
    body = first.json()
    assert body["account_id"] == account_id
    assert body["email"] == "mail@gmail.com"
    assert body["provider"] == "gmail"
    assert body["is_primary"] == 1

    second = await client.post(
        "/email-accounts/get-or-create",
        json={"account_id": account_id, "email": "mail@gmail.com"},
    )
    assert second.status_code == 200
    # Lookup is by email alone; the existing row wins and keeps is_primary=1.
    assert second.json()["id"] == body["id"]
    assert second.json()["is_primary"] == 1


async def test_list_email_accounts_all_and_by_account(client):
    first_account = await _make_account(client, "one@example.com", "hash-1")
    second_account = await _make_account(client, "two@example.com", "hash-2")
    await _make_email_account(client, first_account, "b@gmail.com")
    await _make_email_account(client, second_account, "a@gmail.com")

    every = await client.get("/email-accounts")
    assert every.status_code == 200
    # X2 preserved: no account filter means every tenant, ordered by email.
    assert [row["email"] for row in every.json()] == ["a@gmail.com", "b@gmail.com"]

    scoped = await client.get("/email-accounts", params={"account_id": second_account})
    assert [row["email"] for row in scoped.json()] == ["a@gmail.com"]


async def test_list_email_accounts_orders_by_is_primary_desc_then_email(client):
    """Ordering is ``is_primary`` DESC, then ``email`` — not email alone."""
    account_id = await _make_account(client)
    await _make_email_account(client, account_id, "aaa@gmail.com", is_primary=False)
    await _make_email_account(client, account_id, "zzz@gmail.com", is_primary=True)
    await _make_email_account(client, account_id, "mmm@gmail.com", is_primary=False)

    response = await client.get("/email-accounts", params={"account_id": account_id})
    assert response.status_code == 200
    rows = response.json()
    assert [row["email"] for row in rows] == ["zzz@gmail.com", "aaa@gmail.com", "mmm@gmail.com"]
    assert [row["is_primary"] for row in rows] == [1, 0, 0]


async def test_get_email_account_by_id(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")

    response = await client.get(f"/email-accounts/{email_account_id}")
    assert response.status_code == 200
    assert response.json()["email"] == "mail@gmail.com"

    missing = await client.get("/email-accounts/9999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Email account not found"}


# --- /email-accounts/{id}/token ---------------------------------------------


async def test_token_round_trip_preserves_scopes_and_expiry(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")

    expiry = datetime(2030, 1, 2, 3, 4, 5)
    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar",
    ]
    stored = {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": scopes,
        "expiry": expiry.isoformat(),
    }

    put = await client.put(f"/email-accounts/{email_account_id}/token", json=stored)
    assert put.status_code == 204
    assert put.content == b""

    got = await client.get(f"/email-accounts/{email_account_id}/token")
    assert got.status_code == 200
    body = got.json()
    assert body["scopes"] == scopes
    assert datetime.fromisoformat(body["expiry"]) == expiry
    assert body["token"] == "access-token"
    assert body["refresh_token"] == "refresh-token"
    assert body["token_uri"] == "https://oauth2.googleapis.com/token"
    assert body["client_id"] == "client-id"
    assert body["client_secret"] == "client-secret"


async def test_token_put_updates_existing_row(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")

    await client.put(
        f"/email-accounts/{email_account_id}/token",
        json={"token": "first", "scopes": ["scope-a"]},
    )
    second = await client.put(
        f"/email-accounts/{email_account_id}/token",
        json={"token": "second", "scopes": ["scope-b"]},
    )
    assert second.status_code == 204

    body = (await client.get(f"/email-accounts/{email_account_id}/token")).json()
    assert body["token"] == "second"
    assert body["scopes"] == ["scope-b"]


async def test_get_token_404_when_absent(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")

    response = await client.get(f"/email-accounts/{email_account_id}/token")
    assert response.status_code == 404
    assert response.json() == {"detail": "Credentials not found"}


# --- /emails ----------------------------------------------------------------


async def test_save_emails_returns_only_new_rows_and_is_idempotent(client, manager):
    """Idempotent on ``(email_account_id, message_id)``: 1 saved, then 0."""
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")

    first = await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[_email_payload("msg-1")],
    )
    assert first.status_code == 200
    body = first.json()
    assert body["count"] == 1
    assert len(body["saved"]) == 1
    saved = body["saved"][0]
    assert saved["message_id"] == "msg-1"
    assert saved["email_account_id"] == email_account_id
    assert saved["subject"] == "subject msg-1"
    assert saved["sender"] == "sender@example.com"
    assert saved["recipient"] == "owner@example.com"
    assert saved["snippet"] == "snippet"
    assert saved["body_text"] == "body text"
    assert saved["body_html"] == "<p>body html</p>"
    assert saved["date_sent"] == "2026-01-01T10:00:00"
    # X5 preserved: save_emails never writes thread_id.
    assert saved["thread_id"] is None

    second = await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[_email_payload("msg-1", subject="changed")],
    )
    assert second.status_code == 200
    assert second.json() == {"saved": [], "count": 0}

    with manager.get_session() as session:
        rows = session.query(Email).filter(Email.message_id == "msg-1").all()
        assert len(rows) == 1
        assert rows[0].subject == "subject msg-1"


async def test_save_emails_mixed_batch_returns_only_the_new_one(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")

    await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[_email_payload("msg-1")],
    )
    response = await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[_email_payload("msg-1"), _email_payload("msg-2")],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert [row["message_id"] for row in body["saved"]] == ["msg-2"]


async def test_save_emails_empty_batch(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")

    response = await client.post(
        "/emails", params={"email_account_id": email_account_id}, json=[]
    )
    assert response.status_code == 200
    assert response.json() == {"saved": [], "count": 0}


async def test_list_emails_orders_by_date_sent_desc_and_honors_limit(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")
    other_email_account_id = await _make_email_account(client, account_id, "other@gmail.com")

    await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[
            _email_payload("old", date_sent="2026-01-01T00:00:00"),
            _email_payload("newest", date_sent="2026-03-01T00:00:00"),
            _email_payload("middle", date_sent="2026-02-01T00:00:00"),
        ],
    )
    await client.post(
        "/emails",
        params={"email_account_id": other_email_account_id},
        json=[_email_payload("elsewhere", date_sent="2026-04-01T00:00:00")],
    )

    response = await client.get("/emails", params={"email_account_id": email_account_id})
    assert response.status_code == 200
    assert [row["message_id"] for row in response.json()] == ["newest", "middle", "old"]

    limited = await client.get(
        "/emails", params={"email_account_id": email_account_id, "limit": 2}
    )
    assert [row["message_id"] for row in limited.json()] == ["newest", "middle"]


async def test_latest_email_date(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")

    empty = await client.get(
        "/emails/latest-date", params={"email_account_id": email_account_id}
    )
    assert empty.status_code == 200
    assert empty.json() == {"date_sent": None}

    await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[
            _email_payload("old", date_sent="2026-01-01T00:00:00"),
            _email_payload("newest", date_sent="2026-03-01T09:30:00"),
        ],
    )

    filled = await client.get(
        "/emails/latest-date", params={"email_account_id": email_account_id}
    )
    assert filled.json() == {"date_sent": "2026-03-01T09:30:00"}


async def test_email_by_message_id(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")
    await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[_email_payload("msg-1")],
    )

    response = await client.get("/emails/by-message-id/msg-1")
    assert response.status_code == 200
    assert response.json()["message_id"] == "msg-1"

    missing = await client.get("/emails/by-message-id/nope")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Email not found"}


async def test_duplicate_message_id_across_mailboxes_preserves_x3(client, manager):
    """X3: ``emails.message_id`` has no unique constraint.

    Two mailboxes may cache the same message; the by-message-id lookup has no
    account filter and uses ``.first()``, so it returns one arbitrary row rather
    than erroring, and the batch route agrees with it.
    """
    account_id = await _make_account(client)
    first_email_account = await _make_email_account(client, account_id, "one@gmail.com")
    second_email_account = await _make_email_account(client, account_id, "two@gmail.com")

    for email_account_id in (first_email_account, second_email_account):
        response = await client.post(
            "/emails",
            params={"email_account_id": email_account_id},
            json=[_email_payload("dup")],
        )
        assert response.json()["count"] == 1

    with manager.get_session() as session:
        assert session.query(Email).filter(Email.message_id == "dup").count() == 2

    single = await client.get("/emails/by-message-id/dup")
    assert single.status_code == 200
    assert single.json()["message_id"] == "dup"
    assert single.json()["email_account_id"] in (first_email_account, second_email_account)

    batch = await client.get("/emails/by-message-ids", params={"ids": "dup"})
    assert batch.status_code == 200
    assert len(batch.json()) == 1
    assert batch.json()[0]["message_id"] == "dup"
    assert batch.json()[0]["id"] == single.json()["id"]


async def test_emails_by_message_ids_batch(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")
    await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[_email_payload("msg-1"), _email_payload("msg-2")],
    )

    response = await client.get(
        "/emails/by-message-ids", params={"ids": "msg-2,absent,msg-1,msg-2"}
    )
    assert response.status_code == 200
    # Request order is preserved, duplicates collapse, missing ids are absent.
    assert [row["message_id"] for row in response.json()] == ["msg-2", "msg-1"]

    empty = await client.get("/emails/by-message-ids", params={"ids": ""})
    assert empty.status_code == 200
    assert empty.json() == []


async def test_search_emails_filters_by_account_and_limits(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")
    other_email_account_id = await _make_email_account(client, account_id, "other@gmail.com")

    await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[_email_payload("msg-1"), _email_payload("msg-2")],
    )
    await client.post(
        "/emails",
        params={"email_account_id": other_email_account_id},
        json=[_email_payload("msg-3")],
    )

    response = await client.get(
        "/emails/search",
        params={
            "email_account_id": email_account_id,
            "message_ids": "msg-1,msg-2,msg-3",
        },
    )
    assert response.status_code == 200
    assert sorted(row["message_id"] for row in response.json()) == ["msg-1", "msg-2"]

    limited = await client.get(
        "/emails/search",
        params={
            "email_account_id": email_account_id,
            "message_ids": "msg-1,msg-2",
            "limit": 1,
        },
    )
    assert len(limited.json()) == 1


async def test_count_emails(client):
    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")
    other_email_account_id = await _make_email_account(client, account_id, "other@gmail.com")

    empty = await client.get("/emails/count", params={"email_account_id": email_account_id})
    assert empty.status_code == 200
    assert empty.json() == {"count": 0}

    await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[_email_payload("msg-1"), _email_payload("msg-2")],
    )
    await client.post(
        "/emails",
        params={"email_account_id": other_email_account_id},
        json=[_email_payload("msg-3")],
    )

    response = await client.get("/emails/count", params={"email_account_id": email_account_id})
    assert response.json() == {"count": 2}


# --- /stats -----------------------------------------------------------------


async def test_stats_accounts(client):
    empty = await client.get("/stats/accounts")
    assert empty.status_code == 200
    assert empty.json() == {"total_accounts": 0, "total_email_accounts": 0}

    first_account = await _make_account(client, "one@example.com", "hash-1")
    await _make_account(client, "two@example.com", "hash-2")
    await _make_email_account(client, first_account, "mail@gmail.com")

    response = await client.get("/stats/accounts")
    assert response.json() == {"total_accounts": 2, "total_email_accounts": 1}


async def test_stats_emails(client):
    empty = await client.get("/stats/emails")
    assert empty.status_code == 200
    assert empty.json() == {"total_emails": 0, "latest_email_date": None}

    account_id = await _make_account(client)
    email_account_id = await _make_email_account(client, account_id, "mail@gmail.com")
    other_email_account_id = await _make_email_account(client, account_id, "other@gmail.com")
    await client.post(
        "/emails",
        params={"email_account_id": email_account_id},
        json=[_email_payload("msg-1", date_sent="2026-01-01T00:00:00")],
    )
    await client.post(
        "/emails",
        params={"email_account_id": other_email_account_id},
        json=[_email_payload("msg-2", date_sent="2026-05-05T12:00:00")],
    )

    response = await client.get("/stats/emails")
    assert response.json() == {
        "total_emails": 2,
        "latest_email_date": "2026-05-05T12:00:00",
    }
