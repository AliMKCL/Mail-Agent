"""
Vector DB service — /embed-and-store, /store and /query routes.

Spec section 3.5. Filled by agent V1.

Each route is a thin wrapper over ``backend.services.vector_db.store``, which is
``backend/databases/vector_database.py`` relocated. The request DTOs carry
parallel ``texts`` / ``metadatas`` / ``ids`` lists, so every handler first
rebuilds the ``mails: list[dict]`` shape the store functions expect
(``message_id``, ``sender``, ``subject``, ``date_sent``, ``body_text``, and for
``/store`` also ``embedding``).

An empty input list is a no-op returning a zero count, mirroring
``embed_and_store``'s existing ``if documents:`` guard.
"""

from typing import Any

from fastapi import APIRouter

from backend.libs.contracts.vectors import (
    EmbedAndStoreRequest,
    QueryRequest,
    QueryResponse,
    StoreRequest,
    VectorDocDTO,
)
from backend.services.vector_db import store

router = APIRouter()


def _metadata_at(metadatas: list[dict[str, Any]] | None, index: int) -> dict[str, Any]:
    if not metadatas or index >= len(metadatas):
        return {}
    return metadatas[index] or {}


def _message_id_at(
    ids: list[str] | None, metadata: dict[str, Any], index: int
) -> Any:
    if ids and index < len(ids):
        return ids[index]
    return metadata.get("message_id", "")


def _mail(text: str, metadata: dict[str, Any], message_id: Any) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "sender": metadata.get("sender", ""),
        "subject": metadata.get("subject", ""),
        "date_sent": metadata.get("date_sent", ""),
        "body_text": text,
    }


@router.post("/embed-and-store")
async def embed_and_store(payload: EmbedAndStoreRequest) -> dict[str, int]:
    """Compute embeddings and store, in one atomic Chroma call."""
    mails: list[dict[str, Any]] = []
    for index, text in enumerate(payload.texts):
        metadata = _metadata_at(payload.metadatas, index)
        mails.append(_mail(text, metadata, _message_id_at(payload.ids, metadata, index)))

    if not mails:
        return {"stored": 0}

    await store.embed_and_store(mails)
    return {"stored": len(mails)}


@router.post("/store")
async def store_vectors(payload: StoreRequest) -> dict[str, int]:
    """Store precomputed embeddings (the Go sync server path)."""
    mails: list[dict[str, Any]] = []
    for index, text in enumerate(payload.texts):
        metadata = _metadata_at(payload.metadatas, index)
        mail = _mail(text, metadata, _message_id_at(payload.ids, metadata, index))
        mail["embedding"] = (
            payload.embeddings[index] if index < len(payload.embeddings) else []
        )
        mails.append(mail)

    if not mails:
        return {"stored": 0}

    await store.store_in_vector_db(mails)
    return {"stored": len(mails)}


@router.post("/query", response_model=QueryResponse)
async def query(payload: QueryRequest) -> QueryResponse:
    """Similarity search; LangChain Documents serialized to {page_content, metadata}."""
    results = await store.query_vector_db(payload.query, top_k=payload.top_k)
    return QueryResponse(
        documents=[
            VectorDocDTO(page_content=doc.page_content, metadata=doc.metadata)
            for doc in results
        ]
    )
