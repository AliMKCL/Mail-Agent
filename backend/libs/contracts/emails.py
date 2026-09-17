"""
Email DTOs.

Field names and types mirror the ``emails`` table columns exactly. Timestamps
stay as ``datetime | None``; handlers keep doing
``email.date_sent.isoformat() if email.date_sent else None`` themselves so
public JSON is unchanged (R7).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class EmailDTO(BaseModel):
    """An ``emails`` row as returned by the Database service."""

    id: int
    email_account_id: int
    message_id: str
    thread_id: str | None = None
    subject: str | None = None
    sender: str | None = None
    recipient: str | None = None
    date_sent: datetime | None = None
    snippet: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    created_at: datetime | None = None


class EmailInputDTO(BaseModel):
    """One element of the list ``DatabaseManager.save_emails`` consumes.

    ``message_id`` is the dedupe key and is required; everything else is
    optional, matching the nullable columns.
    """

    message_id: str
    thread_id: str | None = None
    subject: str | None = None
    sender: str | None = None
    recipient: str | None = None
    date_sent: datetime | None = None
    snippet: str | None = None
    body_text: str | None = None
    body_html: str | None = None


__all__ = ["EmailDTO", "EmailInputDTO"]
