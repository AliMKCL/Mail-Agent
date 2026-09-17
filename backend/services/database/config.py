"""
Database service configuration (:8030).

Internal-only service; binds loopback (R6). Sole owner of the SQLAlchemy engine
and gmail_agent.db (R1).
"""

from backend.libs.common.config import settings

SERVICE_NAME = "database"
HOST = "127.0.0.1"
PORT = 8030

DATABASE_URL = settings.DATABASE_URL

__all__ = ["SERVICE_NAME", "HOST", "PORT", "DATABASE_URL", "settings"]
