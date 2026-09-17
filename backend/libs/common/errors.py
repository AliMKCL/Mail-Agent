"""
Upstream error transport for service-to-service calls.

Rule R7: every public route keeps its exact status codes and ``detail`` strings.
When a downstream service answers non-2xx, the calling service must re-emit that
status and that ``detail`` string **unchanged** so the golden-parity suite still
matches. ``raise_http_from_upstream`` is the only sanctioned way to do that.
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import HTTPException


class UpstreamError(Exception):
    """A downstream service returned a non-2xx response.

    Attributes:
        status: the HTTP status code the downstream service returned.
        body: the parsed JSON body when the response was JSON, otherwise the
            raw response text.
    """

    def __init__(self, status: int, body: Any):
        self.status = status
        self.body = body
        super().__init__(f"upstream returned {status}: {body!r}")


def raise_http_from_upstream(err: UpstreamError) -> NoReturn:
    """Re-raise an :class:`UpstreamError` as a ``fastapi.HTTPException``.

    The upstream status code is preserved, and so is the upstream ``detail``
    string, verbatim (R7). If the upstream body is a JSON object carrying a
    ``detail`` key, that value becomes the detail; otherwise the whole body is
    passed through untouched.
    """
    body = err.body
    if isinstance(body, dict) and "detail" in body:
        detail: Any = body["detail"]
    else:
        detail = body
    raise HTTPException(status_code=err.status, detail=detail)


__all__ = ["UpstreamError", "raise_http_from_upstream"]
