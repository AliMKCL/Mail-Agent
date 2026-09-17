"""
Vector DB service configuration (:8040).

Binds loopback (R6). Sole owner of chromadb / langchain_chroma /
langchain_ollama (R4). Its only client is User_data.
"""

from backend.libs.common.config import settings

SERVICE_NAME = "vector_db"
HOST = "127.0.0.1"
PORT = 8040

CHROMA_DIR = settings.CHROMA_DIR
EMBED_MODEL = settings.EMBED_MODEL
OLLAMA_BASE_URL = settings.OLLAMA_BASE_URL

__all__ = [
    "SERVICE_NAME",
    "HOST",
    "PORT",
    "CHROMA_DIR",
    "EMBED_MODEL",
    "OLLAMA_BASE_URL",
    "settings",
]
