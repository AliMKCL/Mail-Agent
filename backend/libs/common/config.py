"""
Single environment-driven settings object shared by every Mail Agent service.

Every default below is byte-identical to the value that was hardcoded in the
monolith, so importing this module changes no behavior. Overrides come from the
environment with the ``MAIL_AGENT_`` prefix, e.g. ``MAIL_AGENT_DATABASE_URL``.

Use the module-level ``settings`` instance; do not instantiate ``Settings``
yourself in service code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_PREFIX = "MAIL_AGENT_"

# Repo root. This file lives at <repo_root>/backend/libs/common/config.py, so the
# root is four parents up. The old backend/databases/vector_database.py computed
# the same directory as Path(__file__).resolve().parent.parent.parent.
REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_CHROMA_DIR = str(REPO_ROOT / "vector_database")


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(ENV_PREFIX + name)
    return default if value is None else value


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(ENV_PREFIX + name)
    return default if value is None or value == "" else int(value)


@dataclass(frozen=True)
class Settings:
    """Immutable configuration snapshot, resolved from the environment at import."""

    # --- Persistence -----------------------------------------------------
    DATABASE_URL: str = field(default_factory=lambda: _env_str("DATABASE_URL", "sqlite:///gmail_agent.db"))

    # --- Vector store ----------------------------------------------------
    CHROMA_DIR: str = field(default_factory=lambda: _env_str("CHROMA_DIR", DEFAULT_CHROMA_DIR))
    EMBED_MODEL: str = field(default_factory=lambda: _env_str("EMBED_MODEL", "mxbai-embed-large"))

    # --- Google OAuth ----------------------------------------------------
    # Deliberately relative: today's code calls
    # Flow.from_client_secrets_file("credentials.json", ...) and
    # os.path.exists('credentials.json') relative to the process cwd.
    # An absolute override is honored as-is.
    GOOGLE_CREDENTIALS_FILE: str = field(default_factory=lambda: _env_str("GOOGLE_CREDENTIALS_FILE", "credentials.json"))
    OAUTH_HOST: str = field(default_factory=lambda: _env_str("OAUTH_HOST", "localhost"))
    OAUTH_PORT: int = field(default_factory=lambda: _env_int("OAUTH_PORT", 8080))
    OAUTH_REDIRECT_URI: str = field(
        default_factory=lambda: _env_str("OAUTH_REDIRECT_URI", "http://localhost:8000/oauth/callback")
    )

    # --- Service base URLs (all loopback; only the Gateway binds 0.0.0.0, R6) ---
    ACCOUNTS_URL: str = field(default_factory=lambda: _env_str("ACCOUNTS_URL", "http://127.0.0.1:8010"))
    USER_DATA_URL: str = field(default_factory=lambda: _env_str("USER_DATA_URL", "http://127.0.0.1:8020"))
    DATABASE_SERVICE_URL: str = field(
        default_factory=lambda: _env_str("DATABASE_SERVICE_URL", "http://127.0.0.1:8030")
    )
    VECTOR_DB_URL: str = field(default_factory=lambda: _env_str("VECTOR_DB_URL", "http://127.0.0.1:8040"))
    MCP_URL: str = field(default_factory=lambda: _env_str("MCP_URL", "http://127.0.0.1:8050"))

    # --- External processes (managed separately from scripts/run_all.sh) ---
    GO_SYNC_URL: str = field(default_factory=lambda: _env_str("GO_SYNC_URL", "http://localhost:8001"))
    RATE_LIMITER_URL: str = field(default_factory=lambda: _env_str("RATE_LIMITER_URL", "http://localhost:8002"))
    OLLAMA_BASE_URL: str = field(default_factory=lambda: _env_str("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))

    # X6: the current literal carries two trailing spaces. Preserved verbatim;
    # only now it is overridable.
    OLLAMA_CHAT_MODEL: str = field(default_factory=lambda: _env_str("OLLAMA_CHAT_MODEL", "qwen3.8:27b-mlx  "))

    # X4: calendar endpoints are hardcoded to email account 1 today.
    CALENDAR_EMAIL_ACCOUNT_ID: int = field(default_factory=lambda: _env_int("CALENDAR_EMAIL_ACCOUNT_ID", 1))


settings = Settings()

__all__ = ["ENV_PREFIX", "REPO_ROOT", "DEFAULT_CHROMA_DIR", "Settings", "settings"]
