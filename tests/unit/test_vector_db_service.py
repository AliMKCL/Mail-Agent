"""
Unit tests for the Vector DB service (:8040) — Spec 3.5 / Phase 3.3.

Nothing here may contact Ollama or open the real ``vector_database/`` Chroma
directory (hazard B2: the monolith opens that directory too, and two writers on
one persistent directory is the corruption case the split exists to remove).

Two layers of protection:

1. ``langchain_chroma.Chroma`` and ``langchain_ollama.OllamaEmbeddings`` are
   patched *before* ``backend.services.vector_db.store`` is imported, so the
   module-scope ``collection`` built at import time is never a real client.
2. Every test additionally monkeypatches ``store.collection`` and
   ``store.embeddings`` with recording fakes.

Routes are driven in-process through ``httpx.ASGITransport``.
"""

from typing import Any
from unittest import mock

import httpx
import pytest
from langchain_core.documents import Document

# Layer 1: no real Chroma client / embedding model is ever constructed.
with mock.patch("langchain_chroma.Chroma"), mock.patch(
    "langchain_ollama.OllamaEmbeddings"
):
    from backend.services.vector_db import store
    from backend.services.vector_db.app import app


class FakeRawCollection:
    """Stands in for ``collection._collection`` (the private Chroma handle)."""

    def __init__(self) -> None:
        self.add_calls: list[dict[str, Any]] = []

    def add(self, ids=None, embeddings=None, documents=None, metadatas=None):
        self.add_calls.append(
            {
                "ids": ids,
                "embeddings": embeddings,
                "documents": documents,
                "metadatas": metadatas,
            }
        )


class FakeCollection:
    """Stands in for the module-scope LangChain ``Chroma`` collection."""

    def __init__(self) -> None:
        self.add_documents_calls: list[tuple[list[Document], list[str]]] = []
        self.similarity_calls: list[tuple[list[float], int]] = []
        self.results: list[Document] = []
        self._collection = FakeRawCollection()

    def add_documents(self, documents, ids=None):
        self.add_documents_calls.append((documents, ids))

    def similarity_search_by_vector(self, embedding, k=None):
        self.similarity_calls.append((embedding, k))
        return self.results


class FakeEmbeddings:
    """Stands in for the ``OllamaEmbeddings`` model. Never hits the network."""

    def __init__(self) -> None:
        self.embed_query_calls: list[str] = []

    def embed_query(self, text):
        self.embed_query_calls.append(text)
        return [0.11, 0.22, 0.33]


@pytest.fixture
def collection(monkeypatch) -> FakeCollection:
    fake = FakeCollection()
    monkeypatch.setattr(store, "collection", fake)
    return fake


@pytest.fixture
def embeddings(monkeypatch) -> FakeEmbeddings:
    fake = FakeEmbeddings()
    monkeypatch.setattr(store, "embeddings", fake)
    return fake


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://vector-db"
    )


async def test_store_module_never_holds_a_real_chroma_client():
    """Import-time safety net: the live Chroma directory is never opened here."""
    assert isinstance(store.collection, mock.MagicMock)
    assert isinstance(store.embeddings, mock.MagicMock)


async def test_embed_and_store_passes_message_ids_and_four_metadata_keys(
    collection, embeddings
):
    payload = {
        "texts": ["cleaned body one", "cleaned body two"],
        "metadatas": [
            {
                "message_id": "msg-1",
                "sender": "alice@example.com",
                "subject": "Invoice",
                "date_sent": "2025-01-01T10:00:00",
            },
            {
                "message_id": "msg-2",
                "sender": "bob@example.com",
                "subject": "Lunch",
                "date_sent": "2025-01-02T11:30:00",
            },
        ],
        "ids": ["msg-1", "msg-2"],
    }

    async with _client() as client:
        response = await client.post("/embed-and-store", json=payload)

    assert response.status_code == 200
    assert response.json() == {"stored": 2}

    assert len(collection.add_documents_calls) == 1
    documents, ids = collection.add_documents_calls[0]

    # ids are the message_ids, in order (that is what dedups on re-sync)
    assert ids == ["msg-1", "msg-2"]

    assert [doc.page_content for doc in documents] == [
        "cleaned body one",
        "cleaned body two",
    ]
    for doc in documents:
        assert set(doc.metadata.keys()) == {
            "message_id",
            "sender",
            "subject",
            "date_sent",
        }
    assert documents[0].metadata == {
        "message_id": "msg-1",
        "sender": "alice@example.com",
        "subject": "Invoice",
        "date_sent": "2025-01-01T10:00:00",
    }
    assert documents[1].metadata == {
        "message_id": "msg-2",
        "sender": "bob@example.com",
        "subject": "Lunch",
        "date_sent": "2025-01-02T11:30:00",
    }

    # this path embeds through the collection, never through embed_query
    assert embeddings.embed_query_calls == []
    assert collection._collection.add_calls == []


async def test_embed_and_store_falls_back_to_metadata_message_id(collection):
    payload = {
        "texts": ["body"],
        "metadatas": [{"message_id": "meta-id", "sender": "s", "subject": "j"}],
    }

    async with _client() as client:
        response = await client.post("/embed-and-store", json=payload)

    assert response.json() == {"stored": 1}
    documents, ids = collection.add_documents_calls[0]
    assert ids == ["meta-id"]
    assert documents[0].metadata["message_id"] == "meta-id"
    # missing keys degrade to '' exactly like the original module did
    assert documents[0].metadata["date_sent"] == ""


async def test_embed_and_store_empty_list_is_a_noop(collection):
    async with _client() as client:
        response = await client.post(
            "/embed-and-store", json={"texts": [], "metadatas": [], "ids": []}
        )

    assert response.status_code == 200
    assert response.json() == {"stored": 0}
    assert collection.add_documents_calls == []
    assert collection._collection.add_calls == []


async def test_store_forwards_precomputed_vectors_aligned_by_index(collection):
    payload = {
        "texts": ["go body one", "go body two"],
        "embeddings": [[0.1, 0.2], [0.3, 0.4]],
        "metadatas": [
            {
                "message_id": "go-1",
                "sender": "alice@example.com",
                "subject": "Report",
                "date_sent": "2025-02-01T09:00:00",
            },
            {
                "message_id": "go-2",
                "sender": "bob@example.com",
                "subject": "Ping",
                "date_sent": "2025-02-02T09:00:00",
            },
        ],
        "ids": ["go-1", "go-2"],
    }

    async with _client() as client:
        response = await client.post("/store", json=payload)

    assert response.status_code == 200
    assert response.json() == {"stored": 2}

    # the precomputed vectors bypass the embedding model entirely
    assert collection.add_documents_calls == []
    assert len(collection._collection.add_calls) == 1
    call = collection._collection.add_calls[0]

    assert call["ids"] == ["go-1", "go-2"]
    assert call["embeddings"] == [[0.1, 0.2], [0.3, 0.4]]
    assert call["documents"] == ["go body one", "go body two"]
    assert call["metadatas"] == [
        {
            "message_id": "go-1",
            "sender": "alice@example.com",
            "subject": "Report",
            "date_sent": "2025-02-01T09:00:00",
        },
        {
            "message_id": "go-2",
            "sender": "bob@example.com",
            "subject": "Ping",
            "date_sent": "2025-02-02T09:00:00",
        },
    ]
    # index alignment across all four parallel lists
    for key in ("embeddings", "documents", "metadatas"):
        assert len(call[key]) == len(call["ids"])


async def test_store_empty_list_is_a_noop(collection):
    async with _client() as client:
        response = await client.post(
            "/store", json={"texts": [], "embeddings": [], "metadatas": [], "ids": []}
        )

    assert response.status_code == 200
    assert response.json() == {"stored": 0}
    assert collection._collection.add_calls == []
    assert collection.add_documents_calls == []


async def test_query_returns_page_content_metadata_shape_and_passes_top_k(
    collection, embeddings
):
    collection.results = [
        Document(
            page_content="first hit",
            metadata={
                "message_id": "q-1",
                "sender": "alice@example.com",
                "subject": "Hit",
                "date_sent": "2025-03-01T08:00:00",
            },
        ),
        Document(page_content="second hit", metadata={"message_id": "q-2"}),
    ]

    async with _client() as client:
        response = await client.post(
            "/query", json={"query": "invoice from alice", "top_k": 7}
        )

    assert response.status_code == 200
    assert response.json() == {
        "documents": [
            {
                "page_content": "first hit",
                "metadata": {
                    "message_id": "q-1",
                    "sender": "alice@example.com",
                    "subject": "Hit",
                    "date_sent": "2025-03-01T08:00:00",
                },
            },
            {"page_content": "second hit", "metadata": {"message_id": "q-2"}},
        ]
    }

    assert embeddings.embed_query_calls == ["invoice from alice"]
    assert len(collection.similarity_calls) == 1
    embedded_query, k = collection.similarity_calls[0]
    assert embedded_query == [0.11, 0.22, 0.33]
    assert k == 7  # top_k is Chroma's k


async def test_query_empty_result_set(collection, embeddings):
    async with _client() as client:
        response = await client.post("/query", json={"query": "nothing matches"})

    assert response.status_code == 200
    assert response.json() == {"documents": []}
    # contract default
    assert collection.similarity_calls[0][1] == 5


async def test_health_route_is_served_by_the_app():
    async with _client() as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "vector_db"}
