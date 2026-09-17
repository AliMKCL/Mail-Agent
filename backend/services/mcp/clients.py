"""
Downstream clients for the MCP service.

These are **provider functions**, not module-level singletons. They are resolved
at call time so they work as FastAPI ``Depends`` targets in ``http_app.py`` and
can be swapped wholesale through ``app.dependency_overrides`` in tests. The
stdio entrypoint (``mcp_server.py``) calls them directly.

Never capture the return value at import time.

There is deliberately no Vector DB client here: MCP reaches semantic search
through User_data's ``/internal/emails/search/semantic`` (R4).
"""

from ratelimiter.client.ratelimiter_client import RateLimiterClient

from backend.libs.common.config import settings
from backend.libs.common.http import AsyncServiceClient


def get_accounts_client() -> AsyncServiceClient:
    """Client for the Accounts service (:8010)."""
    return AsyncServiceClient(settings.ACCOUNTS_URL)


def get_user_data_client() -> AsyncServiceClient:
    """Client for the User_data service (:8020)."""
    return AsyncServiceClient(settings.USER_DATA_URL)


def get_limiter() -> RateLimiterClient:
    """Rate limiter client. ``limiter.check(...)`` blocks move here verbatim (R8)."""
    return RateLimiterClient(settings.RATE_LIMITER_URL)


__all__ = ["get_accounts_client", "get_user_data_client", "get_limiter"]
