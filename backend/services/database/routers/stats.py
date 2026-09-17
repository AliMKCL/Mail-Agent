"""
Database service — /stats/accounts and /stats/emails.

Spec section 3.2. Filled by agent D1.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.libs.contracts.stats import AccountStatsDTO, EmailStatsDTO
from backend.services.database.manager import DatabaseManager, get_db_manager
from backend.services.database.models import Account, Email, EmailAccount

router = APIRouter()


@router.get("/stats/accounts", response_model=AccountStatsDTO)
async def account_stats(db: DatabaseManager = Depends(get_db_manager)) -> AccountStatsDTO:
    """Row counts for ``accounts`` and ``email_accounts``."""
    with db.get_session() as session:
        return AccountStatsDTO(
            total_accounts=session.query(Account).count(),
            total_email_accounts=session.query(EmailAccount).count(),
        )


@router.get("/stats/emails", response_model=EmailStatsDTO)
async def email_stats(db: DatabaseManager = Depends(get_db_manager)) -> EmailStatsDTO:
    """Total cached emails plus the newest ``date_sent`` across all accounts.

    The latest date is read the same way ``get_latest_email_date`` reads a
    single mailbox's — newest ``date_sent`` first, then that row's value — only
    without the account filter.
    """
    with db.get_session() as session:
        total_emails = session.query(Email).count()
        latest_email = session.query(Email).order_by(Email.date_sent.desc()).first()
        return EmailStatsDTO(
            total_emails=total_emails,
            latest_email_date=latest_email.date_sent if latest_email else None,
        )
