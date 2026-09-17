"""
Downstream clients for the Accounts service.

These are **provider functions**, not module-level singletons. They are resolved
at call time so they work as FastAPI ``Depends`` targets and can be swapped
wholesale through ``app.dependency_overrides`` in tests. This replaces the old
``dependencies.db_manager`` module-attribute convention.

Never capture the return value at import time.
"""

from backend.libs.common.config import settings
from backend.libs.common.http import AsyncServiceClient


def get_database_client() -> AsyncServiceClient:
    """Client for the Database service (:8030)."""
    return AsyncServiceClient(settings.DATABASE_SERVICE_URL)


__all__ = ["get_database_client"]
