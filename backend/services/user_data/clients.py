"""
Downstream clients for the User_data service.

These are **provider functions**, not module-level singletons. They are resolved
at call time so they work as FastAPI ``Depends`` targets and can be swapped
wholesale through ``app.dependency_overrides`` in tests. This replaces the old
``dependencies.db_manager`` / ``dependencies.limiter`` module-attribute
convention.

Never capture the return value at import time.

``get_accounts_sync_client`` exists because ``gmail.get_service()`` and
``google_calendar.get_calendar_service()`` must keep their synchronous
signatures (Spec 3.4 / X10) and therefore cannot await an async client.
"""

from ratelimiter.client.ratelimiter_client import RateLimiterClient

from backend.libs.common.config import settings
from backend.libs.common.http import AsyncServiceClient, ServiceClient


def get_database_client() -> AsyncServiceClient:
    """Client for the Database service (:8030)."""
    return AsyncServiceClient(settings.DATABASE_SERVICE_URL)


def get_accounts_client() -> AsyncServiceClient:
    """Client for the Accounts service (:8010)."""
    return AsyncServiceClient(settings.ACCOUNTS_URL)


def get_accounts_sync_client() -> ServiceClient:
    """Blocking client for the Accounts service, for the two sync service getters."""
    return ServiceClient(settings.ACCOUNTS_URL)


def get_vector_db_client() -> AsyncServiceClient:
    """Client for the Vector DB service (:8040)."""
    return AsyncServiceClient(settings.VECTOR_DB_URL)


def get_limiter() -> RateLimiterClient:
    """Rate limiter client. ``limiter.check(...)`` blocks move here verbatim (R8)."""
    return RateLimiterClient(settings.RATE_LIMITER_URL)


__all__ = [
    "get_database_client",
    "get_accounts_client",
    "get_accounts_sync_client",
    "get_vector_db_client",
    "get_limiter",
]
