"""
Generic reverse proxy for the Gateway (:8000), plus the per-route timeout table.

Spec section 3.7. The Gateway holds no business logic: it forwards method, path,
query string and body to the upstream that owns the route and hands the upstream's
status, raw body and ``content-type`` straight back.

Why raw ``httpx`` and not ``backend.libs.common.http.AsyncServiceClient``:
``AsyncServiceClient`` raises ``UpstreamError`` on any non-2xx response. A reverse
proxy must forward a 404, a 422 or a 429 unchanged (R7, R8), so it needs the
response object itself, not an exception. It also needs non-JSON bodies --
``/oauth/callback`` answers with HTML.

Testability seam
----------------
Every upstream client comes from :func:`get_upstream_client`, which delegates to a
module-level factory that :func:`set_client_factory` can replace. That is how the
golden-parity suite wraps the five service ASGI apps in ``httpx.ASGITransport`` and
runs the whole stack in one process with no sockets and no network. ``upstream`` is
a small enumerable identifier (:data:`UPSTREAMS`), not a base URL, so an override
can map identifiers to apps without pattern-matching URLs.

The proxy never closes a client it was handed -- the factory owns the lifecycle.
The default factory keeps one client per upstream (Spec 7.2) and the Gateway's
lifespan closes them via :func:`close_clients`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

import httpx
from fastapi import FastAPI, Request, Response

from backend.libs.common.config import settings

# --------------------------------------------------------------------------
# Upstreams
# --------------------------------------------------------------------------

ACCOUNTS = "accounts"
USER_DATA = "user_data"
MCP = "mcp"

#: The complete set of upstreams the Gateway may reach. The Database (:8030) and
#: Vector DB (:8040) services are internal only and are deliberately absent (R6).
UPSTREAMS = (ACCOUNTS, USER_DATA, MCP)


def upstream_base_url(upstream: str) -> str:
    """Resolve an upstream identifier to its configured base URL."""
    if upstream == ACCOUNTS:
        return settings.ACCOUNTS_URL
    if upstream == USER_DATA:
        return settings.USER_DATA_URL
    if upstream == MCP:
        return settings.MCP_URL
    raise KeyError(f"unknown upstream {upstream!r}; expected one of {UPSTREAMS}")


# --------------------------------------------------------------------------
# Client provider (the override seam)
# --------------------------------------------------------------------------

ClientFactory = Callable[[str], httpx.AsyncClient]

# One client per upstream, built lazily and reused (Spec 7.2).
_default_clients: dict[str, httpx.AsyncClient] = {}


def default_client_factory(upstream: str) -> httpx.AsyncClient:
    """Production factory: one long-lived ``httpx.AsyncClient`` per upstream.

    No timeout is set here. The per-route timeout is applied at request time by
    :func:`forward`, so a single client can serve both ``/api/emails`` (60 s) and
    ``/api/sync`` (1200 s).
    """
    client = _default_clients.get(upstream)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(base_url=upstream_base_url(upstream))
        _default_clients[upstream] = client
    return client


_client_factory: ClientFactory = default_client_factory


def get_upstream_client(upstream: str) -> httpx.AsyncClient:
    """Provider function: the client used to reach ``upstream``.

    Called per request so a test override takes effect immediately. Callers must
    not close the returned client -- the factory owns its lifecycle.
    """
    return _client_factory(upstream)


def set_client_factory(factory: ClientFactory) -> ClientFactory:
    """Install ``factory`` as the upstream-client provider; return the previous one."""
    global _client_factory
    previous = _client_factory
    _client_factory = factory
    return previous


def reset_client_factory() -> None:
    """Restore the production factory."""
    global _client_factory
    _client_factory = default_client_factory


async def close_clients() -> None:
    """Close every client the default factory built. Called from the app lifespan."""
    while _default_clients:
        _, client = _default_clients.popitem()
        await client.aclose()


# --------------------------------------------------------------------------
# Headers
# --------------------------------------------------------------------------

#: RFC 7230 hop-by-hop headers. Forwarding these corrupts the response -- in
#: particular an upstream ``content-length`` alongside a re-encoded body.
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

#: Also dropped on the way out: ``host`` belongs to the Gateway's own listener and
#: ``content-length`` is recomputed by ``httpx`` from the body we hand it.
_DROPPED_REQUEST_HEADERS = HOP_BY_HOP_HEADERS | {"host", "content-length"}

#: Forwarded verbatim from the upstream response. ``tests/e2e/test_rate_limiter.py``
#: asserts all three on a 429 (R8); dropping them is a failure.
RATELIMIT_HEADERS = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After")


def build_request_headers(request: Request) -> dict[str, str]:
    """Client headers to forward upstream, minus hop-by-hop, ``host``, ``content-length``."""
    return {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _DROPPED_REQUEST_HEADERS
    }


def build_response_headers(upstream_response: httpx.Response) -> dict[str, str]:
    """Upstream headers to hand back.

    Deliberately an allow-list rather than "everything minus hop-by-hop":
    ``content-type`` travels as the response ``media_type`` and the CORS headers
    are owned by the Gateway's own ``CORSMiddleware``. Echoing an upstream's CORS
    headers on top of the Gateway's would emit duplicates and break the browser.
    """
    return {
        header: upstream_response.headers[header]
        for header in RATELIMIT_HEADERS
        if header in upstream_response.headers
    }


# --------------------------------------------------------------------------
# The proxy
# --------------------------------------------------------------------------


async def forward(request: Request, upstream: str, timeout: float) -> Response:
    """Forward ``request`` to ``upstream`` and return its response unchanged.

    Status, raw body bytes and ``content-type`` pass through untouched, so
    FastAPI's 422 validation envelopes and every ``detail`` string survive
    byte-for-byte (R7).
    """
    target = request.url.path
    if request.url.query:
        # Keep the raw query string; re-parsing would re-encode it.
        target = f"{target}?{request.url.query}"

    client = get_upstream_client(upstream)
    upstream_response = await client.request(
        request.method,
        target,
        content=await request.body(),
        headers=build_request_headers(request),
        timeout=timeout,
    )

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=build_response_headers(upstream_response),
        media_type=upstream_response.headers.get("content-type"),
    )


# --------------------------------------------------------------------------
# Route table (Spec 3.1 ownership x Spec 3.7 timeouts)
# --------------------------------------------------------------------------

ACCOUNTS_TIMEOUT = 120.0  # interactive OAuth re-auth can block the process (B7)
USER_DATA_TIMEOUT = 60.0
SYNC_TIMEOUT = 1200.0  # /api/sync calls the Go server with timeout=1000 (B5)
QUERY_TIMEOUT = 300.0
LLM_QUERY_TIMEOUT = 600.0


class ProxyRoute(NamedTuple):
    path: str
    methods: tuple[str, ...]
    upstream: str
    timeout: float


#: Explicit paths, never a catch-all: an unmapped path must 404 at the Gateway
#: instead of silently reaching a service. Nothing under ``/internal/*`` appears
#: here and nothing under ``/internal/*`` ever may (R6).
PROXY_ROUTES: tuple[ProxyRoute, ...] = (
    # --- Accounts (:8010) --------------------------------------------------
    ProxyRoute("/api/auth/signin", ("POST",), ACCOUNTS, ACCOUNTS_TIMEOUT),
    ProxyRoute("/api/auth/signup", ("POST",), ACCOUNTS, ACCOUNTS_TIMEOUT),
    ProxyRoute("/api/auth/google", ("GET",), ACCOUNTS, ACCOUNTS_TIMEOUT),
    ProxyRoute("/oauth/callback", ("GET",), ACCOUNTS, ACCOUNTS_TIMEOUT),
    ProxyRoute("/api/users", ("GET", "POST"), ACCOUNTS, ACCOUNTS_TIMEOUT),
    ProxyRoute(
        "/api/email-account/{email_account_id}", ("GET",), ACCOUNTS, ACCOUNTS_TIMEOUT
    ),
    # --- User_data (:8020) -------------------------------------------------
    ProxyRoute("/api/emails", ("GET",), USER_DATA, USER_DATA_TIMEOUT),
    ProxyRoute("/api/sync", ("GET",), USER_DATA, SYNC_TIMEOUT),
    ProxyRoute("/api/calendar/events", ("GET", "POST"), USER_DATA, USER_DATA_TIMEOUT),
    ProxyRoute(
        "/api/calendar/events/{event_id}",
        ("PUT", "DELETE"),
        USER_DATA,
        USER_DATA_TIMEOUT,
    ),
    ProxyRoute("/api/calendar/status", ("GET",), USER_DATA, USER_DATA_TIMEOUT),
    ProxyRoute("/api/calendar/moodle", ("GET",), USER_DATA, USER_DATA_TIMEOUT),
    # --- MCP (:8050) -------------------------------------------------------
    ProxyRoute("/api/query", ("GET",), MCP, QUERY_TIMEOUT),
    ProxyRoute("/api/llm-query", ("POST",), MCP, LLM_QUERY_TIMEOUT),
)


def _make_endpoint(upstream: str, timeout: float):
    """Build a parameter-less endpoint so the Gateway never validates anything.

    Path parameters are intentionally not declared: the owning service validates
    them and returns its own 404/422. A declared ``email_account_id: int`` here
    would make the Gateway answer 422 for a request the service should answer.
    """

    async def proxy_endpoint(request: Request) -> Response:
        return await forward(request, upstream=upstream, timeout=timeout)

    return proxy_endpoint


def register_proxy_routes(app: FastAPI) -> None:
    """Register every row of :data:`PROXY_ROUTES` on ``app``.

    One ``APIRoute`` per (path, method) pair rather than one route carrying
    several methods: FastAPI derives an operation id from the route name plus an
    arbitrary member of its ``methods`` *set*, so a two-method route emits the
    same id twice and produces an invalid OpenAPI document. Starlette still
    resolves the path correctly -- a method miss is a partial match and routing
    continues to the sibling route -- so a genuinely unsupported method is still
    a 405.
    """
    for route in PROXY_ROUTES:
        endpoint = _make_endpoint(route.upstream, route.timeout)
        for method in route.methods:
            app.add_api_route(
                route.path,
                endpoint,
                methods=[method],
                name=f"proxy_{route.upstream}_{method.lower()}",
            )
