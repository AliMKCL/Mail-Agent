"""
User_data service configuration (:8020).

Binds loopback (R6). Sole caller of the Gmail API, the Google Calendar API and
the Go sync server (R3). Its only route to Chroma is the Vector DB service (R4).
"""

from backend.libs.common.config import settings

SERVICE_NAME = "user_data"
HOST = "127.0.0.1"
PORT = 8020

DATABASE_SERVICE_URL = settings.DATABASE_SERVICE_URL
ACCOUNTS_URL = settings.ACCOUNTS_URL
VECTOR_DB_URL = settings.VECTOR_DB_URL

GO_SYNC_URL = settings.GO_SYNC_URL
RATE_LIMITER_URL = settings.RATE_LIMITER_URL

# X4: the public calendar endpoints are hardcoded to email account 1 today.
CALENDAR_EMAIL_ACCOUNT_ID = settings.CALENDAR_EMAIL_ACCOUNT_ID

__all__ = [
    "SERVICE_NAME",
    "HOST",
    "PORT",
    "DATABASE_SERVICE_URL",
    "ACCOUNTS_URL",
    "VECTOR_DB_URL",
    "GO_SYNC_URL",
    "RATE_LIMITER_URL",
    "CALENDAR_EMAIL_ACCOUNT_ID",
    "settings",
]
