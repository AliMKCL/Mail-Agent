"""
Database service — /emails/* routes.

Spec section 3.2. Filled by agent D1.

Registration order matters: FastAPI matches in declaration order, so the literal
paths (``/emails/latest-date``, ``/emails/by-message-ids``, ``/emails/search``,
``/emails/count``) are declared before ``/emails/by-message-id/{message_id}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func

from backend.libs.contracts.emails import EmailDTO, EmailInputDTO
from backend.services.database.manager import DatabaseManager, get_db_manager
from backend.services.database.models import Email

router = APIRouter()


def _email_dto(email: Email) -> EmailDTO:
    return EmailDTO(
        id=email.id,
        email_account_id=email.email_account_id,
        message_id=email.message_id,
        thread_id=email.thread_id,
        subject=email.subject,
        sender=email.sender,
        recipient=email.recipient,
        date_sent=email.date_sent,
        snippet=email.snippet,
        body_text=email.body_text,
        body_html=email.body_html,
        created_at=email.created_at,
    )


def _split_ids(raw: str) -> list[str]:
    """Split a comma-separated id list, dropping empties and duplicates.

    Duplicates are collapsed with ``dict.fromkeys`` so request order survives.
    """
    return list(dict.fromkeys(part for part in raw.split(",") if part))


@router.post("/emails")
async def save_emails(
    email_account_id: int,
    payload: list[EmailInputDTO],
    db: DatabaseManager = Depends(get_db_manager),
) -> dict:
    """Insert the emails that are not already cached for this email account.

    ``save_emails`` only inserts rows whose ``(email_account_id, message_id)``
    pair is absent and returns only the objects it created, so ``count`` is
    ``len()`` of that return value — callers use it as their "new emails" count
    and that filtering is load-bearing. ``thread_id`` is accepted in the body
    and then ignored by the manager, which is why the column is always NULL
    today (issue X5, preserved).

    The returned objects are expired by the manager's ``commit()`` and detached
    by its ``close()``, so reading their attributes raises
    ``DetachedInstanceError``. The manager is a verbatim lift and must not
    change, so ``saved`` is produced by re-reading the rows it just inserted:
    the ones for this email account, with an id above the pre-call maximum, and
    a ``message_id`` from this payload.
    """
    email_data = [item.model_dump() for item in payload]
    message_ids = [item.message_id for item in payload]

    with db.get_session() as session:
        previous_max_id = session.query(func.max(Email.id)).filter(
            Email.email_account_id == email_account_id
        ).scalar()
    previous_max_id = previous_max_id or 0

    created = db.save_emails(email_account_id, email_data)

    with db.get_session() as session:
        rows = session.query(Email).filter(
            Email.email_account_id == email_account_id,
            Email.id > previous_max_id,
            Email.message_id.in_(message_ids),
        ).order_by(Email.id).all()
        saved = [_email_dto(row) for row in rows]

    return {"saved": saved, "count": len(created)}


@router.get("/emails", response_model=list[EmailDTO])
async def list_emails(
    email_account_id: int,
    limit: int = 50,
    db: DatabaseManager = Depends(get_db_manager),
) -> list[EmailDTO]:
    """Recent emails for one email account, newest ``date_sent`` first."""
    emails = db.get_email_account_emails(email_account_id, limit)
    return [_email_dto(email) for email in emails]


@router.get("/emails/latest-date")
async def latest_email_date(
    email_account_id: int,
    db: DatabaseManager = Depends(get_db_manager),
) -> dict:
    """``date_sent`` of the newest cached email, or ``null`` on an empty mailbox."""
    date_sent = db.get_latest_email_date(email_account_id)
    return {"date_sent": date_sent.isoformat() if date_sent else None}


@router.get("/emails/by-message-ids", response_model=list[EmailDTO])
async def emails_by_message_ids(
    ids: str,
    db: DatabaseManager = Depends(get_db_manager),
) -> list[EmailDTO]:
    """Batch form of ``/emails/by-message-id/{message_id}``.

    ``ids`` is comma-separated. At most one row comes back per requested id, in
    request order; ids with no cached row are simply absent rather than an
    error. This deliberately runs the same per-id ``filter_by(...).first()`` the
    loop it replaces runs — one query per id in a single session — so the
    first-match semantics (and the X3 arbitrariness noted below) are identical
    rather than merely equivalent.
    """
    requested = _split_ids(ids)
    results: list[EmailDTO] = []
    with db.get_session() as session:
        for message_id in requested:
            email = session.query(Email).filter_by(message_id=message_id).first()
            if email is not None:
                results.append(_email_dto(email))
    return results


@router.get("/emails/search", response_model=list[EmailDTO])
async def search_emails(
    email_account_id: int,
    message_ids: str,
    limit: int = 50,
    db: DatabaseManager = Depends(get_db_manager),
) -> list[EmailDTO]:
    """Emails for one email account whose ``message_id`` is in ``message_ids``.

    ``message_ids`` is comma-separated. No ordering is applied, matching the
    ``message_id.in_(ids)`` + account filter + limit the Spec names.
    """
    requested = _split_ids(message_ids)
    with db.get_session() as session:
        rows = session.query(Email).filter(
            Email.email_account_id == email_account_id,
            Email.message_id.in_(requested),
        ).limit(limit).all()
        return [_email_dto(row) for row in rows]


@router.get("/emails/count")
async def count_emails(
    email_account_id: int,
    db: DatabaseManager = Depends(get_db_manager),
) -> dict:
    """Number of cached emails for one email account."""
    with db.get_session() as session:
        count = session.query(Email).filter(
            Email.email_account_id == email_account_id
        ).count()
    return {"count": count}


@router.get("/emails/by-message-id/{message_id}", response_model=EmailDTO)
async def email_by_message_id(
    message_id: str,
    db: DatabaseManager = Depends(get_db_manager),
) -> EmailDTO:
    """Fetch one cached email by Gmail ``message_id``, with no account filter.

    Issue **X3, preserved deliberately**: ``emails.message_id`` has no unique
    constraint, so when two mailboxes have cached the same message this
    ``.first()`` returns an arbitrary one of them. ``get_email_details`` and
    ``mail://email/{id}`` behave exactly this way today.
    """
    with db.get_session() as session:
        email = session.query(Email).filter_by(message_id=message_id).first()
        if not email:
            raise HTTPException(status_code=404, detail="Email not found")
        return _email_dto(email)
