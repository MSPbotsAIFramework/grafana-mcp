# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Thin async client for Grafana's legacy /api/* HTTP API.

Reference: https://grafana.com/docs/grafana/latest/developers/http_api/ (fetched 2026-11-02).
Auth: ``Authorization: Bearer <service account token>``. The legacy surface this client
targets is documented as "not being disabled" and works the same way across self-hosted
and Cloud instances -- see PRD-21261 tech design, Option analysis.
"""

import asyncio
import email.utils
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .credentials import Credentials

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_BACKOFF = 20.0


@dataclass
class ApiError(Exception):
    code: str
    message: str
    retryable: bool = False
    status: int | None = None

    def __str__(self) -> str:
        return self.message


def _status_to_error(status: int, detail: str) -> ApiError:
    if status in (401, 403):
        return ApiError(
            "unauthorized",
            f"Grafana rejected the Service Account token ({status}). Check the token and its role. {detail}".strip(),
            False,
            status,
        )
    if status == 404:
        return ApiError("not_found", f"Not found in Grafana. {detail}".strip(), False, status)
    if status in (400, 405, 409, 415, 422):
        return ApiError("invalid_argument", f"Grafana refused the request ({status}). {detail}".strip(), False, status)
    if status == 429:
        return ApiError("rate_limited", "Grafana rate limit reached. Try again shortly.", True, status)
    return ApiError("upstream_error", f"Grafana returned {status}. {detail}".strip(), status >= 500, status)


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    text = body.get("message") or body.get("error")
    return str(text)[:500] if text else ""


def _retry_after(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("retry-after")
    if value:
        try:
            return min(float(value), MAX_BACKOFF)
        except ValueError:
            parsed = email.utils.parsedate_to_datetime(value)
            if parsed is not None:
                return max(0.0, min(parsed.timestamp() - time.time(), MAX_BACKOFF))
    return min(2.0**attempt, MAX_BACKOFF)


class GrafanaClient:
    """One instance per tool call; wraps a single httpx client for that call."""

    def __init__(self, creds: Credentials, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        headers = {"Accept": "application/json", "User-Agent": "mspbots-grafana-mcp/1.0"}
        if creds.token:
            headers["Authorization"] = f"Bearer {creds.token}"
        self._http = httpx.AsyncClient(
            base_url=creds.base_url,
            headers=headers,
            timeout=httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout),
            transport=transport,
        )

    async def __aenter__(self) -> "GrafanaClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        deadline = time.monotonic() + self._settings.call_budget_seconds
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        attempt = 0
        while True:
            try:
                response = await self._http.request(method, path, params=clean, json=body)
            except httpx.TimeoutException as exc:
                if attempt < self._settings.max_retries and time.monotonic() + 1 < deadline:
                    attempt += 1
                    await asyncio.sleep(min(2.0**attempt, MAX_BACKOFF))
                    continue
                raise ApiError("upstream_error", "Grafana did not respond in time.", True) from exc
            except httpx.HTTPError as exc:
                raise ApiError("upstream_error", f"Could not reach Grafana: {type(exc).__name__}.", True) from exc

            if response.status_code in RETRY_STATUSES and attempt < self._settings.max_retries:
                wait = _retry_after(response, attempt + 1)
                if time.monotonic() + wait < deadline:
                    attempt += 1
                    await asyncio.sleep(wait)
                    continue

            if response.status_code >= 400:
                raise _status_to_error(response.status_code, _error_detail(response))
            if response.status_code == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise ApiError("upstream_error", "Grafana returned a response that is not JSON.", True) from exc

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: Any, params: dict[str, Any] | None = None) -> Any:
        return await self.request("POST", path, body=body, params=params)
