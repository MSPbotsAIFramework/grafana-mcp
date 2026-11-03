# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Per-request credentials, taken only from request headers.

The gateway sends each tenant's Grafana base URL and Service Account token on every
request. They live in a ContextVar for the duration of that request and are never
stored anywhere else (no environment variable fallback, no on-disk cache).

Neither header blocks the request at the transport level (see CredentialMiddleware):
``initialize`` and ``tools/list`` must keep working even before a tenant has entered
credentials, the same way the platform's admin console refreshes a connector's tool
catalog with placeholder/absent headers. A missing or malformed value is instead
reported as a structured tool-call error (``not_configured`` / ``invalid_argument``)
by ``tools/_common.py:run`` -- see PRD-21261 TC7 / Self-test notes for why this
connector does not use an HTTP 401 middleware gate the way some other connectors do.
"""

import contextvars
from dataclasses import dataclass
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import BASE_URL_HEADER, TOKEN_HEADER


@dataclass(frozen=True)
class Credentials:
    token: str
    base_url: str
    # Set when the base URL header was present but malformed: tools report it as
    # invalid_argument (and never call Grafana), while initialize / tools/list still work.
    header_error: str | None = None


_current: contextvars.ContextVar[Credentials | None] = contextvars.ContextVar("grafana_credentials", default=None)


def current() -> Credentials | None:
    return _current.get()


class CredentialError(ValueError):
    pass


def normalize_base_url(raw: str) -> str:
    """Validate and normalize a tenant-supplied Grafana base URL.

    Grafana instances are self-hosted or Cloud, on whatever host the tenant runs --
    unlike a single-vendor-domain connector, there is no fixed suffix to allow-list.
    Only the URL shape is validated: absolute, http(s), with a host. http:// is
    accepted (not just https://) because many self-hosted MSP instances sit behind a
    private network without TLS.
    """
    url = raw.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise CredentialError(
            f"{BASE_URL_HEADER} must be an absolute http(s) URL, e.g. https://acme.grafana.net."
        )
    return f"{parsed.scheme}://{parsed.netloc}"


class CredentialMiddleware:
    """Reads the Grafana headers on /mcp requests and binds them to the request context."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = (request.headers.get(TOKEN_HEADER) or "").strip()
        raw_base_url = (request.headers.get(BASE_URL_HEADER) or "").strip()

        base_url = ""
        header_error: str | None = None
        if raw_base_url:
            try:
                base_url = normalize_base_url(raw_base_url)
            except CredentialError as exc:
                header_error = str(exc)

        bound = _current.set(Credentials(token=token, base_url=base_url, header_error=header_error))
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(bound)
