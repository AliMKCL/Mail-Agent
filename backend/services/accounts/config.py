"""
Accounts service configuration (:8010).

Binds loopback (R6). Sole credential authority: only this service reads/writes
email_tokens, runs an OAuth flow, or reads credentials.json (R2).
"""

from backend.libs.common.config import settings

SERVICE_NAME = "accounts"
HOST = "127.0.0.1"
PORT = 8010

DATABASE_SERVICE_URL = settings.DATABASE_SERVICE_URL

GOOGLE_CREDENTIALS_FILE = settings.GOOGLE_CREDENTIALS_FILE
OAUTH_HOST = settings.OAUTH_HOST
OAUTH_PORT = settings.OAUTH_PORT
OAUTH_REDIRECT_URI = settings.OAUTH_REDIRECT_URI

__all__ = [
    "SERVICE_NAME",
    "HOST",
    "PORT",
    "DATABASE_SERVICE_URL",
    "GOOGLE_CREDENTIALS_FILE",
    "OAUTH_HOST",
    "OAUTH_PORT",
    "OAUTH_REDIRECT_URI",
    "settings",
]
