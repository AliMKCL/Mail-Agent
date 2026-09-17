"""
Vector DB service request/response DTOs (Spec 3.5).

``/embed-and-store`` computes embeddings and stores in one Chroma call.
``/store`` takes precomputed embeddings (the Go sync path).
``/query`` returns ``{"documents": [{"page_content", "metadata"}]}``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class VectorDocDTO(BaseModel):
    """One retrieved document, matching the current ``/api/query`` shape."""

    page_content: str
    metadata: dict[str, Any]


class EmbedAndStoreRequest(BaseModel):
    """Body for ``POST /embed-and-store``: texts plus parallel metadata/ids."""

    texts: list[str]
    metadatas: list[dict[str, Any]] | None = None
    ids: list[str] | None = None


class StoreRequest(BaseModel):
    """Body for ``POST /store``: embeddings already computed upstream."""

    texts: list[str]
    embeddings: list[list[float]]
    metadatas: list[dict[str, Any]] | None = None
    ids: list[str] | None = None


class QueryRequest(BaseModel):
    """Body for ``POST /query``."""

    query: str
    top_k: int = 5
    where: dict[str, Any] | None = None


class QueryResponse(BaseModel):
    """Response of ``POST /query``."""

    documents: list[VectorDocDTO]


__all__ = [
    "VectorDocDTO",
    "EmbedAndStoreRequest",
    "StoreRequest",
    "QueryRequest",
    "QueryResponse",
]
