"""
Stats DTOs for ``/stats/accounts`` and ``/stats/emails`` (Spec 3.2) and the
Accounts ``/internal/stats`` route (Spec 3.3).

``latest_email_date`` stays a ``datetime | None``; the handler that serves
``system://status`` keeps doing its own ``.isoformat()`` (R7).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AccountStatsDTO(BaseModel):
    """Response of ``GET /stats/accounts`` and ``GET /internal/stats``."""

    total_accounts: int
    total_email_accounts: int


class EmailStatsDTO(BaseModel):
    """Response of ``GET /stats/emails``."""

    total_emails: int
    latest_email_date: datetime | None = None


__all__ = ["AccountStatsDTO", "EmailStatsDTO"]
