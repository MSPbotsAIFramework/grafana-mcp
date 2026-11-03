# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Shared plumbing for tool handlers."""

import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from .. import _json
from ..api import ApiError, GrafanaClient
from ..config import BASE_URL_HEADER, TOKEN_HEADER, Settings
from ..credentials import current

log = logging.getLogger("grafana_mcp")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
# Annotation creation is additive (never updates/deletes) but Grafana does not dedupe repeated
# calls, so it is not idempotent either.
CREATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

Limit = Annotated[
    int | None,
    Field(description=f"Max items to return (default {DEFAULT_PAGE_SIZE}, hard cap {MAX_PAGE_SIZE}).", ge=1),
]


def clamp_limit(limit: int | None) -> int:
    return min(max(limit or DEFAULT_PAGE_SIZE, 1), MAX_PAGE_SIZE)


def cut(items: list, limit: int) -> tuple[list, bool]:
    """Split an (optionally over-fetched) list into a page plus a has_more flag.

    Callers that can ask Grafana for ``limit + 1`` rows pass that list here so "is there
    another page" is known without a second round trip. Callers that fetch the full list
    (Grafana's provisioning endpoints have no limit param) pass the already-filtered list
    and get the same shape back.
    """
    has_more = len(items) > limit
    return items[:limit], has_more


_UID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ToolInputError(ValueError):
    """Raised by handlers for arguments Grafana would reject anyway."""


def seg(value: object) -> str:
    """Path segment for a Grafana UID. Rejects anything that is not a plain uid token,
    so a crafted value cannot alter the URL path (e.g. path traversal)."""
    text = str(value).strip()
    if not text or not _UID_RE.match(text):
        raise ToolInputError(f"Expected a Grafana UID, got {text!r}.")
    return text


Handler = Callable[[GrafanaClient], Awaitable[Any]]

REDACTED = "[redacted]"


def redact(text: str, secrets: Iterable[str | None]) -> str:
    """Replace every occurrence of the given secret values in ``text``."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


_MISSING_CREDENTIALS = f"No Grafana credentials. Send both {TOKEN_HEADER} and {BASE_URL_HEADER}."


async def run(
    settings: Settings, handler: Handler, secrets: Iterable[str | None] = (), require_token: bool = True
) -> str:
    """Execute ``handler`` with a client bound to this request's credentials.

    Always returns a string: the JSON result, or an error envelope -- tools never raise.
    A request missing a header this tool actually requires is already rejected with
    HTTP 401 by ``CredentialMiddleware`` before it gets here (code review round 1,
    finding 1), so the ``not_configured`` branch below is a defense-in-depth fallback,
    not the primary gate. A malformed base URL that was actually sent (header present,
    value rejected by Grafana) is ``invalid_argument``. ``secrets`` (plus the request's
    own token) are masked out of any error message.
    """
    secrets = [s for s in secrets if s]
    creds = current()
    if creds is None or (require_token and not creds.token) or not (creds.base_url or creds.header_error):
        return _json.error("not_configured", _MISSING_CREDENTIALS)
    if creds.header_error:
        return _json.error("invalid_argument", creds.header_error)
    try:
        async with GrafanaClient(creds, settings) as client:
            result = await handler(client)
    except ApiError as exc:
        return _json.error(exc.code, redact(exc.message, [*secrets, creds.token]), exc.retryable)
    except ToolInputError as exc:
        return _json.error("invalid_argument", redact(str(exc), secrets))
    except Exception as exc:  # noqa: BLE001 - tools must never raise
        log.exception("unexpected error in tool handler: %s", type(exc).__name__)
        return _json.error("upstream_error", "Unexpected error while calling Grafana.", True)
    return result if isinstance(result, str) else _json.dump(result)
