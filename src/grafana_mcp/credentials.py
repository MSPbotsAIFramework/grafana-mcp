# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Per-request credentials, taken only from request headers.

The gateway sends each tenant's Grafana base URL and Service Account token on every
request. They live in a ContextVar for the duration of that request and are never
stored anywhere else (no environment variable fallback, no on-disk cache).

A request to ``/mcp`` that is missing a required header is rejected with HTTP 401
before it ever reaches a tool, listing the missing header name(s) in the body (SOP
sec 3.2; PRD-21261 code review round 1, finding 1), the same shape itglue-mcp uses.
The one exception is ``grafana_get_health``: Grafana's own ``/api/health`` needs no
auth at all, so a ``tools/call`` for that specific tool is let through without a
token (it still needs the base URL header, since the tool has to know which
instance to probe) -- see ``_NO_TOKEN_TOOLS`` below.

A base URL header that is *present* but malformed is not rejected at this layer: it
is instead reported as a structured tool-call error (``invalid_argument``) by
``tools/_common.py:run``, so ``initialize``/``tools/list`` keep working and only the
actual tool call fails. That in-band path never applies to an absent header -- only
to one that was sent with a value Grafana itself would reject.
"""

import contextvars
import json
from dataclasses import dataclass
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import BASE_URL_HEADER, TOKEN_HEADER

# Tools whose underlying Grafana endpoint needs no auth; see module docstring.
_NO_TOKEN_TOOLS = frozenset({"grafana_get_health"})


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


def _reject(missing: list[str]) -> JSONResponse:
    return JSONResponse(
        {
            "error": "Missing required credentials",
            "message": f"Send the following header(s): {', '.join(missing)}.",
            "required_headers": missing,
        },
        status_code=401,
    )


class CredentialMiddleware:
    """Reads the Grafana headers on /mcp requests and binds them to the request context.

    Rejects (401) a request that is missing a header required for it -- see module
    docstring. Everything else (malformed-but-present values, concurrent requests,
    the eventual tool-call result) is unchanged by this check.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        token = (request.headers.get(TOKEN_HEADER) or "").strip()
        raw_base_url = (request.headers.get(BASE_URL_HEADER) or "").strip()

        # Buffer the body so we can peek at the JSON-RPC method/tool name (to apply the
        # grafana_get_health exception) and still let the real app read it afterwards --
        # ASGI `receive` is a one-shot stream, so downstream gets a replay callable, not
        # the original one.
        body = await request.body()
        sent = False

        async def _replay() -> dict:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        token_required = True
        if body:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = None
            if isinstance(payload, dict) and payload.get("method") == "tools/call":
                name = (payload.get("params") or {}).get("name")
                if name in _NO_TOKEN_TOOLS:
                    token_required = False

        missing = []
        if not raw_base_url:
            missing.append(BASE_URL_HEADER)
        if token_required and not token:
            missing.append(TOKEN_HEADER)
        if missing:
            await _reject(missing)(scope, _replay, send)
            return

        base_url = ""
        header_error: str | None = None
        if raw_base_url:
            try:
                base_url = normalize_base_url(raw_base_url)
            except CredentialError as exc:
                header_error = str(exc)

        bound = _current.set(Credentials(token=token, base_url=base_url, header_error=header_error))
        try:
            await self.app(scope, _replay, send)
        finally:
            _current.reset(bound)
