"""
Contract tests for the MCP service's HTTP surface (agent M2).

Covers ``backend/services/mcp/http_app.py`` -- ``GET /api/query`` and
``POST /api/llm-query`` -- plus the parts of
``backend/services/mcp/llm_integration.py`` that the HTTP surface drives.
The stdio entrypoint and the 14 tool implementations are tested in
``tests/contract/test_mcp_tools.py`` (agent M1); this file never duplicates them.

How the stack is wired, and why:

* ``http_app`` runs in-process over ``httpx.ASGITransport`` -- no socket, no port
  8050.
* ``get_user_data_client()`` is overridden with a stub, because ``/api/query``'s
  only downstream is User_data's ``GET /internal/emails/search/semantic``. The
  stub returns the envelope that route really returns (addendum A2 / the U2
  contract test): a ``documents`` list of ``page_content`` + ``metadata`` pairs.
  R4 means there is no Vector DB client to override -- this service must never
  have one.
* ``get_limiter()`` is overridden with a fake that allows by default, so the two
  verbatim ``limiter.check(...)`` blocks (R8) are exercised without a rate
  limiter on :8002.
* ``llm_response`` is patched as bound in ``http_app``, and ``openai.AsyncOpenAI``
  is replaced by a fake, so neither OpenAI nor Ollama is contacted.

No test here touches the network, the real ``gmail_agent.db``, or the real Chroma
directory.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx
import pytest

# The monolith's ``llm_integration`` is imported for exactly one purpose: the
# TOOLS_MANIFEST byte-identity check below. It pulls in ``backend.mcp_server``,
# which transitively builds a Chroma client and an embedding model at module
# import time (addendum C2), so both constructors are patched for the duration
# of the import. Verified: neither ``gmail_agent.db`` nor
# ``vector_database/chroma.sqlite3`` changes md5 as a result of this import.
with mock.patch("langchain_chroma.Chroma"), mock.patch(
    "langchain_ollama.OllamaEmbeddings"
):
    from backend.llm_integration import TOOLS_MANIFEST as MONOLITH_TOOLS_MANIFEST

from backend.services.mcp import http_app as http_app_module
from backend.services.mcp import llm_integration
from backend.services.mcp.clients import get_limiter, get_user_data_client
from backend.services.mcp.http_app import app

BASE_URL = "http://mcp.test"
EMAIL_ACCOUNT_ID = 1

RATE_LIMIT_DETAIL = (
    "Rate limit exceeded! You can only view emails 10 times per hour. "
    "Wait 42 seconds."
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeLimiter:
    """``RateLimiterClient`` stand-in. Records calls; allows by default."""

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[dict[str, Any]] = []

    def check(
        self, scope, identifier, endpoint, tokens=1, capacity=None, refill_rate=None
    ):
        self.calls.append(
            {
                "scope": scope,
                "identifier": identifier,
                "endpoint": endpoint,
                "tokens": tokens,
                "capacity": capacity,
                "refill_rate": refill_rate,
            }
        )
        if self.allowed:
            return {
                "allowed": True,
                "remaining": 9,
                "limit": 10,
                "reset_after_seconds": 0,
                "retry_after_seconds": 0,
            }
        return {
            "allowed": False,
            "remaining": 0,
            "limit": 10,
            "reset_after_seconds": 3600,
            "retry_after_seconds": 42,
        }


def _document(message_id: str, page_content: str, **overrides: Any) -> dict[str, Any]:
    metadata = {
        "message_id": message_id,
        "sender": "boss@company.com",
        "subject": "Meeting Tomorrow",
        "date_sent": "2025-11-22",
    }
    metadata.update(overrides)
    return {"page_content": page_content, "metadata": metadata}


class StubUserData:
    """Stub for the User_data client (:8020).

    ``/api/query`` makes exactly one downstream call, so this only implements
    ``get``. The envelope mirrors the real
    ``GET /internal/emails/search/semantic`` response; its ``results`` key (the
    flattened projection MCP's ``search_emails`` tool consumes) is deliberately
    not modeled, because this handler reads ``documents`` only.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def get(self, path, *, params=None, timeout=None):
        self.calls.append((path, params))
        if self.error is not None:
            raise self.error
        return httpx.Response(
            200,
            json={
                "status": "success",
                "search_type": "semantic",
                "count": len(self.documents),
                "documents": self.documents,
            },
        )


class FakeLLM:
    """Stand-in for ``ask_ollama.llm_response`` (OpenAI ``gpt-5-mini``)."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.result: Any = "Based on your emails, you have a meeting tomorrow at 10am."
        self.error: Exception | None = None

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.result


class FakeAsyncOpenAI:
    """Stand-in for ``openai.AsyncOpenAI``. Never opens a socket.

    Answers every ``chat.completions.create`` with a message that requests no
    tools, which is ``process_with_openai``'s "LLM has a final answer" branch.
    """

    def __init__(self, api_key=None) -> None:
        self.api_key = api_key
        self.calls: list[dict[str, Any]] = []
        self.answer = "You have no deadlines in the next week."
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.answer, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def limiter() -> FakeLimiter:
    return FakeLimiter()


@pytest.fixture
def user_data() -> StubUserData:
    return StubUserData()


@pytest.fixture
def llm(monkeypatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(http_app_module, "llm_response", fake)
    return fake


@pytest.fixture
def openai_clients(monkeypatch) -> list[FakeAsyncOpenAI]:
    """Patch ``openai.AsyncOpenAI``; returns the list of clients constructed."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    created: list[FakeAsyncOpenAI] = []

    def factory(api_key=None):
        client = FakeAsyncOpenAI(api_key)
        created.append(client)
        return client

    monkeypatch.setattr("openai.AsyncOpenAI", factory)
    return created


@pytest.fixture
async def client(limiter, user_data):
    app.dependency_overrides[get_limiter] = lambda: limiter
    app.dependency_overrides[get_user_data_client] = lambda: user_data
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE_URL
        ) as http_client:
            yield http_client
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/llm-query  --  the P3 regression guard
# ---------------------------------------------------------------------------


async def test_llm_query_passes_email_account_id_keyword(client, monkeypatch):
    """P3: the monolith called ``process_llm_query(query, user_id=...)`` while
    the signature takes ``email_account_id=``, so **every** request raised
    ``TypeError`` and returned 500. Assert on the keywords actually passed, so
    reintroducing ``user_id=`` fails here rather than in production."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def spy(query, **kwargs):
        calls.append((query, kwargs))
        return {
            "status": "success",
            "answer": "Added 2 deadlines to your calendar.",
            "actions": [{"tool": "extract_dates_from_emails"}],
        }

    monkeypatch.setattr(llm_integration, "process_llm_query", spy)

    response = await client.post(
        "/api/llm-query",
        json={
            "query": "Find deadlines in my emails",
            "email_account_id": EMAIL_ACCOUNT_ID,
            "use_openai": True,
        },
    )

    assert response.status_code == 200
    assert calls == [
        (
            "Find deadlines in my emails",
            {"email_account_id": EMAIL_ACCOUNT_ID, "use_openai": True},
        )
    ]
    assert "user_id" not in calls[0][1]
    assert response.json() == {
        "status": "success",
        "answer": "Added 2 deadlines to your calendar.",
        "actions": [{"tool": "extract_dates_from_emails"}],
        "note": None,
    }


async def test_llm_query_reaches_the_real_orchestrator(client, openai_clients):
    """The same guard without a stubbed orchestrator: the request runs through
    the real ``process_llm_query`` -> ``process_with_openai``, so a keyword
    mismatch raises ``TypeError`` and 500s exactly as it did before P3."""
    response = await client.post(
        "/api/llm-query",
        json={
            "query": "Do I have any deadlines?",
            "email_account_id": EMAIL_ACCOUNT_ID,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "answer": "You have no deadlines in the next week.",
        "actions": [],
        "note": None,
    }

    # The email account id reached the orchestrator and landed in the system
    # prompt, which is where it changes tool behavior.
    (openai_client,) = openai_clients
    system_prompt = openai_client.calls[0]["messages"][0]["content"]
    assert f"Current email account ID: {EMAIL_ACCOUNT_ID}" in system_prompt
    assert "No email account context provided." not in system_prompt
    assert openai_client.calls[0]["model"] == "gpt-4o-mini"
    assert openai_client.calls[0]["tool_choice"] == "auto"
    assert openai_client.calls[0]["tools"] == llm_integration.TOOLS_MANIFEST


async def test_llm_query_without_email_account_id_still_succeeds(
    client, openai_clients
):
    response = await client.post(
        "/api/llm-query", json={"query": "Do I have any deadlines?"}
    )

    assert response.status_code == 200
    (openai_client,) = openai_clients
    system_prompt = openai_client.calls[0]["messages"][0]["content"]
    assert "No email account context provided." in system_prompt


async def test_llm_query_requires_query(client):
    response = await client.post(
        "/api/llm-query", json={"email_account_id": EMAIL_ACCOUNT_ID}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "query parameter is required"


async def test_llm_query_orchestrator_error_becomes_500(client, monkeypatch):
    async def failing(query, **kwargs):
        return {"status": "error", "error": "OpenAI API key not configured"}

    monkeypatch.setattr(llm_integration, "process_llm_query", failing)

    response = await client.post("/api/llm-query", json={"query": "anything"})

    assert response.status_code == 500
    assert response.json()["detail"] == "OpenAI API key not configured"


# ---------------------------------------------------------------------------
# GET /api/query
# ---------------------------------------------------------------------------


async def test_query_builds_sources_and_answer(client, user_data, llm):
    user_data.documents = [
        _document("msg_002", "Meeting tomorrow at 10am in conference room"),
        _document(
            "msg_003",
            "Project deadline is next Friday",
            sender="pm@company.com",
            subject="Project Deadline",
            date_sent="2025-11-21",
        ),
    ]

    response = await client.get(
        "/api/query", params={"query": "When is the meeting?", "top_k": 3}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "answer": "Based on your emails, you have a meeting tomorrow at 10am.",
        "sources": [
            {
                "message_id": "msg_002",
                "sender": "boss@company.com",
                "subject": "Meeting Tomorrow",
                "date_sent": "2025-11-22",
            },
            {
                "message_id": "msg_003",
                "sender": "pm@company.com",
                "subject": "Project Deadline",
                "date_sent": "2025-11-21",
            },
        ],
        "count": 2,
    }

    # Retrieval goes through User_data, never the Vector DB service (R4).
    assert user_data.calls == [
        (
            "/internal/emails/search/semantic",
            {"query": "When is the meeting?", "top_k": 3},
        )
    ]

    # Both retrieved bodies are in the prompt handed to the LLM.
    (prompt,) = llm.prompts
    assert prompt.startswith("[INST]You are a helpful email assistant.")
    assert "User Question: When is the meeting?" in prompt
    assert "Meeting tomorrow at 10am in conference room" in prompt
    assert "Project deadline is next Friday" in prompt
    assert prompt.endswith("Answer:[/INST]")


async def test_query_defaults_top_k_to_three(client, user_data, llm):
    user_data.documents = [_document("msg_002", "body")]

    await client.get("/api/query", params={"query": "anything"})

    assert user_data.calls == [
        ("/internal/emails/search/semantic", {"query": "anything", "top_k": 3})
    ]


async def test_query_metadata_gaps_fall_back(client, user_data, llm):
    user_data.documents = [{"page_content": "a body", "metadata": {}}]

    response = await client.get("/api/query", params={"query": "anything"})

    assert response.status_code == 200
    assert response.json()["sources"] == [
        {
            "message_id": "",
            "sender": "Unknown",
            "subject": "No Subject",
            "date_sent": "Unknown date",
        }
    ]


async def test_query_with_no_results(client, user_data, llm):
    user_data.documents = []

    response = await client.get("/api/query", params={"query": "anything"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "answer": "I couldn't find any relevant emails to answer your question.",
        "sources": [],
        "count": 0,
    }
    # No relevant emails means no LLM call at all.
    assert llm.prompts == []


async def test_query_requires_query(client):
    response = await client.get("/api/query", params={"query": ""})

    assert response.status_code == 400
    assert response.json()["detail"] == "Query parameter is required"


async def test_query_omitted_parameter_is_a_validation_error(client):
    """Unchanged from the monolith: ``query`` has no default, so omitting it is
    caught by FastAPI before the handler's own 400 can fire."""
    response = await client.get("/api/query")

    assert response.status_code == 422


async def test_query_llm_failure_falls_back(client, user_data, llm):
    user_data.documents = [_document("msg_002", "body")]
    llm.error = RuntimeError("openai exploded")

    response = await client.get("/api/query", params={"query": "anything"})

    assert response.status_code == 200
    assert response.json()["answer"] == (
        "I found relevant emails but encountered an error generating a response. "
        "Please try again."
    )
    # The sources survive the failure.
    assert response.json()["count"] == 1


async def test_query_unwraps_a_dict_response(client, user_data, llm):
    user_data.documents = [_document("msg_002", "body")]
    llm.result = {"response": "unwrapped"}

    response = await client.get("/api/query", params={"query": "anything"})

    assert response.json()["answer"] == "unwrapped"


async def test_query_stringifies_a_list_response(client, user_data, llm):
    user_data.documents = [_document("msg_002", "body")]
    llm.result = ["one", "two"]

    response = await client.get("/api/query", params={"query": "anything"})

    assert response.json()["answer"] == "['one', 'two']"


async def test_query_retrieval_failure_becomes_500(client, user_data, llm):
    user_data.error = RuntimeError("user_data unavailable")

    response = await client.get("/api/query", params={"query": "anything"})

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Error querying vector database: user_data unavailable"
    )


# ---------------------------------------------------------------------------
# Rate limiting (R8) -- both endpoints share the one ``server_total`` bucket
# ---------------------------------------------------------------------------


async def test_query_rate_limited(client, limiter, user_data, llm):
    limiter.allowed = False

    response = await client.get("/api/query", params={"query": "anything"})

    assert response.status_code == 429
    assert response.json()["detail"] == RATE_LIMIT_DETAIL
    assert response.headers["X-RateLimit-Limit"] == "10"
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert response.headers["Retry-After"] == "42"
    # Denied before retrieval.
    assert user_data.calls == []


async def test_llm_query_rate_limited(client, limiter, monkeypatch):
    limiter.allowed = False
    calls: list[Any] = []

    async def spy(query, **kwargs):
        calls.append(kwargs)
        return {"status": "success", "answer": "unreachable", "actions": []}

    monkeypatch.setattr(llm_integration, "process_llm_query", spy)

    response = await client.post(
        "/api/llm-query",
        json={"query": "anything", "email_account_id": EMAIL_ACCOUNT_ID},
    )

    assert response.status_code == 429
    assert response.json()["detail"] == RATE_LIMIT_DETAIL
    assert response.headers["X-RateLimit-Limit"] == "10"
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert response.headers["Retry-After"] == "42"
    assert calls == []


async def test_both_endpoints_share_the_server_total_bucket(
    client, limiter, user_data, llm, openai_clients
):
    """``tests/e2e/test_rate_limiter.py`` asserts the shared bucket, so the two
    endpoints must not be given separate endpoint keys (R8)."""
    await client.get("/api/query", params={"query": "anything"})
    await client.post("/api/llm-query", json={"query": "anything"})

    assert len(limiter.calls) == 2
    assert limiter.calls[0] == limiter.calls[1]
    assert limiter.calls[0] == {
        "scope": "global",
        "identifier": "all",
        "endpoint": "server_total",
        "tokens": 2,
        "capacity": 20,
        "refill_rate": 20,
    }


# ---------------------------------------------------------------------------
# llm_integration parity
# ---------------------------------------------------------------------------


def test_tools_manifest_is_byte_identical_to_the_monolith():
    """The manifest is the OpenAI function-calling schema: a whitespace or
    description edit changes tool selection. Compared against the original
    module rather than a hand-copied snapshot."""
    assert llm_integration.TOOLS_MANIFEST == MONOLITH_TOOLS_MANIFEST


def test_tool_registry_covers_the_manifest():
    manifest_names = [tool["function"]["name"] for tool in MONOLITH_TOOLS_MANIFEST]

    assert list(llm_integration.TOOL_REGISTRY) == manifest_names
    assert len(manifest_names) == 14
    assert all(callable(fn) for fn in llm_integration.TOOL_REGISTRY.values())


async def test_execute_tool_rejects_unknown_tools():
    result = await llm_integration.execute_tool("nope", {})

    assert result == {"status": "error", "error": "Unknown tool: nope"}


async def test_execute_tool_unwrap_guard_accepts_plain_coroutines(monkeypatch):
    """``TOOL_REGISTRY`` now holds plain ``async def``s instead of FastMCP
    ``FunctionTool``s; the ``hasattr(tool_obj, 'fn')`` guard must keep working
    for both shapes."""
    seen: list[dict[str, Any]] = []

    async def plain(**kwargs):
        seen.append(kwargs)
        return {"status": "success"}

    monkeypatch.setitem(llm_integration.TOOL_REGISTRY, "search_emails", plain)
    plain_result = await llm_integration.execute_tool(
        "search_emails", {"query": "hello"}
    )

    wrapped = SimpleNamespace(fn=plain)
    monkeypatch.setitem(llm_integration.TOOL_REGISTRY, "search_emails", wrapped)
    wrapped_result = await llm_integration.execute_tool(
        "search_emails", {"query": "hello"}
    )

    assert plain_result == wrapped_result == {"status": "success"}
    assert seen == [{"query": "hello"}, {"query": "hello"}]


async def test_ollama_mode_does_not_execute_tools(client, monkeypatch):
    """``use_openai: false`` takes the Ollama branch, which describes tools but
    never runs them -- including its verbatim ``note``."""
    prompts: list[str] = []

    def fake_slm(prompt):
        prompts.append(prompt)
        return "I would use search_emails."

    monkeypatch.setattr(
        "backend.services.mcp.ask_ollama.slm_response", fake_slm, raising=True
    )

    response = await client.post(
        "/api/llm-query", json={"query": "anything", "use_openai": False}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "answer": "I would use search_emails.",
        "actions": [],
        "note": "Ollama mode - tools not auto-executed. Use OpenAI for full functionality.",
    }
    assert prompts[0].startswith(
        "You are an AI assistant with access to these tools:\n"
    )
    assert "- list_accounts: " in prompts[0]


# ---------------------------------------------------------------------------
# App surface
# ---------------------------------------------------------------------------


async def test_health(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "mcp"}


def test_route_inventory():
    """Enumerated through the OpenAPI schema: filtering ``app.routes`` on
    ``hasattr(r, "methods")`` hides every router route on this FastAPI version
    (addendum B1)."""
    paths = app.openapi()["paths"]

    assert sorted(paths) == ["/api/llm-query", "/api/query", "/health"]
    assert {"get"} <= set(paths["/api/query"])
    assert {"post"} <= set(paths["/api/llm-query"])
