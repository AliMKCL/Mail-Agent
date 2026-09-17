"""
MCP service configuration (:8050 + stdio).

Binds loopback (R6). Sole caller of openai and Ollama chat (R5). It must never
call the Vector DB service directly — semantic search goes through User_data
(R4).
"""

from backend.libs.common.config import settings

SERVICE_NAME = "mcp"
HOST = "127.0.0.1"
PORT = 8050

ACCOUNTS_URL = settings.ACCOUNTS_URL
USER_DATA_URL = settings.USER_DATA_URL

OLLAMA_BASE_URL = settings.OLLAMA_BASE_URL
# X6: literal preserved verbatim, including its two trailing spaces.
OLLAMA_CHAT_MODEL = settings.OLLAMA_CHAT_MODEL

RATE_LIMITER_URL = settings.RATE_LIMITER_URL

__all__ = [
    "SERVICE_NAME",
    "HOST",
    "PORT",
    "ACCOUNTS_URL",
    "USER_DATA_URL",
    "OLLAMA_BASE_URL",
    "OLLAMA_CHAT_MODEL",
    "RATE_LIMITER_URL",
    "settings",
]
