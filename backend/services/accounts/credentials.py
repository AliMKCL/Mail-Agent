"""
The credential ladder — Spec section **3.3.1**, step 4.2.

This is the highest-risk function in the whole refactor. Three callers used to
reach credentials through three *different* code paths, and those paths do
**not** agree with each other. ``resolve_valid_credentials`` is the single
implementation of all three, and the differences between them are the entire
point of the module.

Parity table (Spec 3.3.1), and how each row selects itself:

====================  ============================  =========================
Row                   Original implementation       Arguments
====================  ============================  =========================
Gmail                 ``gmail_read.get_service``    ``allow_interactive=True``
                      (``gmail_read.py:48-92``)     ``require_scope=None``
                                                    ``refresh=True``
Calendar              ``setup_calendar.``           ``require_scope="calendar"``
                      ``get_calendar_service``      ``allow_interactive=False``
                      (``setup_calendar.py:66-118``) ``refresh=True``
``/api/users`` badge  ``users.py:43-47``            ``refresh=False``
====================  ============================  =========================

So the flags mean:

``refresh``
    ``False`` is the badge row: a **raw read**, no refresh, no re-auth, no
    scope check. ``True`` enables the refresh attempt *and* the interactive
    re-auth that both real rows perform when a refresh **fails**.

``allow_interactive``
    Selects the row: ``True`` is Gmail, ``False`` is Calendar. It decides what
    happens on the *missing-or-unusable-credentials* branch — ``True`` (Gmail)
    opens a browser, ``False`` (Calendar) returns ``"Authentication required"``
    and **never** opens a browser — and it also selects that row's console
    wording and its failed-refresh error string, because the two originals
    word both differently.

``require_scope``
    Calendar only. Applied after the ladder settles, to whatever credentials
    it ended up with.

⚠️ **The asymmetry, stated once, plainly:** on a *failed refresh* **both** rows
run the interactive ``InstalledAppFlow``. On *no credentials at all* only the
**Gmail** row does; the **Calendar** row returns the error string
``"Authentication required"`` with no browser. That is genuinely what
``setup_calendar.get_calendar_service`` does today (its no-credentials branch
is the ``else`` of ``if creds and creds.expired and creds.refresh_token``), and
it must not be "tidied up".

The same structure decides what happens to credentials that exist but are
invalid **and** unrefreshable (expired with no refresh token, say). Both
originals reach the *same* branch they use for "nothing stored at all",
because both guards are a bare ``else`` on
``if creds and creds.expired and creds.refresh_token``: so Gmail opens a
browser for them too, and Calendar reports ``"Authentication required"`` for
them too. See the comments at the branch itself.

The three scope-related error strings are byte-identical to the originals and
are asserted by ``tests/contract/test_accounts_service.py``.
"""

from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from backend.libs.common.http import AsyncServiceClient
from backend.services.accounts import google_oauth

#: ``setup_calendar.py:97`` — no usable credentials, and (for the Calendar row)
#: no browser either.
NO_CREDENTIALS_ERROR = "Authentication required"

#: ``setup_calendar.py:105`` — credentials carry no scope list at all.
NO_SCOPES_ERROR = "Authentication required - No scopes found"

#: ``setup_calendar.py:94`` — interactive re-auth after a failed refresh did not
#: produce credentials. This is the *Calendar* row's wording, and it is a bare
#: fragment: the *Gmail* row's wording for the same branch is the full sentence
#: built by :func:`reauthentication_failed_error`. They genuinely differ, so
#: they must not be collapsed into one constant.
REAUTH_FAILED_ERROR = "Re-authentication failed"


def authentication_failed_error(email_account_id: int) -> str:
    """``gmail_read.py:89`` verbatim — the *Gmail* row's wording.

    The trailing period is part of the string. ``/api/sync``'s error-string
    handlers match on it, so User_data re-raises rather than swallowing it.
    """
    return (
        f"Authentication failed for email account {email_account_id}. "
        "Cannot proceed without valid credentials."
    )


def reauthentication_failed_error(email_account_id: int) -> str:
    """``gmail_read.py:80`` verbatim — the *Gmail* row's failed-refresh wording.

    Distinct from :func:`authentication_failed_error`: ``gmail_read.get_service``
    raises ``"Re-authentication failed for ..."`` when a refresh failed *and*
    the re-auth that followed it failed, and ``"Authentication failed for ..."``
    only on its no-usable-credentials branch. ``/api/sync`` folds whatever it
    gets into ``detail="Error syncing emails: <msg>"``, so both are observable.
    """
    return (
        f"Re-authentication failed for email account {email_account_id}. "
        "Cannot proceed without valid credentials."
    )


#: ``require_scope`` alias -> (scope URL, "missing" error string, stdout line).
#: ``calendar`` is the only alias any caller uses; the route's query type is a
#: ``Literal`` so an unknown value is rejected before it reaches here.
_SCOPE_REQUIREMENTS: dict[str, tuple[str, str, str]] = {
    "calendar": (
        google_oauth.CALENDAR_SCOPE,
        "Authentication required - Calendar scope missing",
        "Calendar scope missing for user {email_account_id}. Current scopes: {scopes}",
    ),
}


async def resolve_valid_credentials(
    email_account_id: int,
    *,
    require_scope: str | None = None,
    allow_interactive: bool = False,
    refresh: bool = True,
    db: AsyncServiceClient,
) -> tuple[Credentials | None, str | None]:
    """Resolve usable credentials for one email account.

    Returns ``(credentials, None)`` on success and ``(None, error_string)`` on
    failure, mirroring ``get_calendar_service``'s ``(service, error)`` contract
    so the Calendar caller can keep returning a tuple instead of raising.

    Args:
        email_account_id: the ``email_accounts`` row id.
        require_scope: ``"calendar"`` to enforce the Calendar scope, else None.
        allow_interactive: open a browser when nothing usable is stored.
        refresh: ``False`` for the raw, side-effect-free badge read.
        db: Database-service client; the only way this module reaches storage
            (**R1** — no database manager and no SQLAlchemy engine here).
    """
    if require_scope is not None and require_scope not in _SCOPE_REQUIREMENTS:
        raise ValueError(f"Unsupported require_scope: {require_scope!r}")

    creds = await google_oauth.load_stored_credentials(db, email_account_id)

    # ---- Badge row (users.py:43-47) --------------------------------------
    # A raw read. No refresh, no re-auth, no scope check. The caller decides
    # what `creds.valid` means; `GET /api/users` reports it as a boolean and
    # keeps its bare `except: pass`.
    if not refresh:
        if creds is None:
            return None, NO_CREDENTIALS_ERROR
        return creds, None

    # ---- Refresh / re-auth ladder ----------------------------------------
    # The shape of this block is deliberately the *union* of the two originals
    # rather than a tidied-up flow chart. Read it once per row:
    #
    #   setup_calendar.get_calendar_service  -> allow_interactive=False
    #       if expired+refresh_token: try refresh, and on failure re-auth
    #                                 inline (its `except` branch)
    #       else:                     print + "Authentication required", no browser
    #
    #   gmail_read.get_service               -> allow_interactive=True
    #       if expired+refresh_token: try refresh, and on failure re-auth
    #                                 inline (its `except` branch)
    #       else:                     print + re-auth; its `else` is BARE, so
    #                                 it covers "nothing stored" *and*
    #                                 "stored but unusable" alike
    #
    # The console lines differ per row too — gmail_read prints emoji-free
    # sentences, setup_calendar prints emoji ones — and stdout is the operator
    # interface for the re-auth CLIs, so each row keeps its own wording.
    #
    # Nothing below this line runs when a refresh succeeds: both originals
    # continue straight to their scope check / build step.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                if allow_interactive:
                    print(f"Attempting to refresh expired token for email account {email_account_id}...")
                else:
                    print(f"🔄 Attempting to refresh expired token for email account {email_account_id}...")
                creds.refresh(Request())
                # Save refreshed credentials back to database
                await google_oauth.save_credentials(db, email_account_id, creds)
                if allow_interactive:
                    print(f"Token refreshed successfully for email account {email_account_id}")
                else:
                    print(f"✅ Token refreshed successfully for email account {email_account_id}")
            except Exception as e:
                if allow_interactive:
                    print(f"Token refresh failed for email account {email_account_id}: {e}")
                    print("   This usually means the refresh token is expired or revoked.")
                    print("   Triggering re-authentication...")
                else:
                    print(f"❌ Error refreshing credentials for email account {email_account_id}: {e}")
                    print("   Triggering re-authentication...")
                # Redundant — the re-auth below overwrites it immediately — but
                # kept so the failed credentials cannot survive an edit to that
                # call. (`authenticate_calendar`'s sibling branch does this
                # literally; neither service function does.)
                creds = None

                # BOTH rows run the interactive flow when a refresh FAILS.
                # Only the error string differs, because the two originals word
                # it differently: gmail_read raises "Re-authentication failed
                # for email account N. Cannot proceed...", setup_calendar
                # returns the bare "Re-authentication failed".
                creds = await google_oauth.reauthenticate_user_token_failure(
                    email_account_id, db
                )
                if not creds:
                    if allow_interactive:
                        return None, reauthentication_failed_error(email_account_id)
                    return None, REAUTH_FAILED_ERROR

        elif allow_interactive:
            # Gmail row: gmail_read's BARE `else`. THIS is where the two rows
            # diverge. It is reached both when nothing is stored at all and
            # when something is stored that is invalid and unrefreshable (no
            # refresh token), and in both cases it opens a browser. Do not
            # narrow it with an `if not creds:` guard — gmail_read has none.
            print(f"No valid credentials found for email account {email_account_id}")
            print("   Triggering initial authentication...")
            creds = await google_oauth.reauthenticate_user_token_failure(
                email_account_id, db
            )
            if not creds:
                return None, authentication_failed_error(email_account_id)

        else:
            # Calendar row: setup_calendar's `else` branch, equally bare and so
            # equally covering both "nothing stored" and "stored but unusable".
            # Error string, NO browser. Do not "improve" this into a re-auth.
            print(f"⚠️  No valid credentials for email account {email_account_id}")
            return None, NO_CREDENTIALS_ERROR

    # ---- Scope check (Calendar row only) ---------------------------------
    if require_scope is not None:
        scope_url, missing_error, warning = _SCOPE_REQUIREMENTS[require_scope]
        if creds and creds.scopes:
            if scope_url not in creds.scopes:
                print(
                    warning.format(
                        email_account_id=email_account_id, scopes=creds.scopes
                    )
                )
                return None, missing_error
        else:
            return None, NO_SCOPES_ERROR

    return creds, None


__all__ = [
    "NO_CREDENTIALS_ERROR",
    "NO_SCOPES_ERROR",
    "REAUTH_FAILED_ERROR",
    "authentication_failed_error",
    "reauthentication_failed_error",
    "resolve_valid_credentials",
]
