"""
Thin HTTP clients for service-to-service calls.

``AsyncServiceClient`` is the default for every internal call. ``ServiceClient``
is the synchronous escape hatch; see its docstring for the single reason it
exists.

Both raise :class:`backend.libs.common.errors.UpstreamError` on any non-2xx
response, so callers can translate with ``raise_http_from_upstream`` and keep
``detail`` strings verbatim (R7).
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from backend.libs.common.errors import UpstreamError

#: Default per-client timeout in seconds. Long-running routes (``/api/sync``,
#: ``/api/llm-query``, interactive OAuth) override it per call.
DEFAULT_TIMEOUT: float = 30.0

#: Sentinel meaning "use the client's configured timeout" for a per-call
#: override. ``None`` cannot be used because ``None`` is a meaningful httpx
#: timeout value (wait forever).
_UNSET = object()


def _parse_body(response: httpx.Response) -> Any:
    """Parsed JSON when possible, otherwise the raw text."""
    try:
        return response.json()
    except ValueError:
        return response.text


def _check(response: httpx.Response) -> httpx.Response:
    if not response.is_success:
        raise UpstreamError(response.status_code, _parse_body(response))
    return response


class AsyncServiceClient:
    """Async HTTP client bound to one downstream service's base URL.

    This is the default client for all service-to-service calls.

    Args:
        base_url: the downstream service root, e.g. ``http://127.0.0.1:8030``.
        timeout: default timeout in seconds for every request.
        transport: optional ``httpx`` transport. This exists so tests can inject
            ``httpx.ASGITransport(app=...)`` and run the whole service stack
            in-process without binding sockets.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.transport = transport

    def _client(self, timeout: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout if timeout is _UNSET else timeout,
            transport=self.transport,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        """Issue one request and return the response, raising on non-2xx."""
        async with self._client(timeout) as client:
            response = await client.request(method, path, params=params, json=json)
        return _check(response)

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        return await self.request("GET", path, params=params, timeout=timeout)

    async def post(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        return await self.request("POST", path, params=params, json=json, timeout=timeout)

    async def put(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        return await self.request("PUT", path, params=params, json=json, timeout=timeout)

    async def patch(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        return await self.request("PATCH", path, params=params, json=json, timeout=timeout)

    async def delete(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        return await self.request("DELETE", path, params=params, timeout=timeout)


class ServiceClient:
    """Synchronous HTTP client bound to one downstream service's base URL.

    This class exists for exactly one reason. ``gmail.get_service()`` and
    ``google_calendar.get_calendar_service()`` in the User_data service must keep
    their current **synchronous** signatures: existing tests patch them by name,
    and ``moodle.py`` calls them from synchronous functions. Making them async
    would break both. So those two functions fetch credentials from the Accounts
    service through this blocking client instead of ``AsyncServiceClient``.

    Do not reach for this anywhere else — use :class:`AsyncServiceClient`.
    """

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url
        self.timeout = timeout

    def _client(self, timeout: Any) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout if timeout is _UNSET else timeout,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        """Issue one request and return the response, raising on non-2xx."""
        with self._client(timeout) as client:
            response = client.request(method, path, params=params, json=json)
        return _check(response)

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        return self.request("GET", path, params=params, timeout=timeout)

    def post(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        return self.request("POST", path, params=params, json=json, timeout=timeout)

    def put(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        return self.request("PUT", path, params=params, json=json, timeout=timeout)

    def patch(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        return self.request("PATCH", path, params=params, json=json, timeout=timeout)

    def delete(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: Any = _UNSET,
    ) -> httpx.Response:
        return self.request("DELETE", path, params=params, timeout=timeout)


__all__ = ["DEFAULT_TIMEOUT", "AsyncServiceClient", "ServiceClient"]
