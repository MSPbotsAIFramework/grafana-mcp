# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json

import httpx
import pytest
import respx
from conftest import BASE
from starlette.testclient import TestClient

from grafana_mcp import credentials
from grafana_mcp.config import BASE_URL_HEADER, TOKEN_HEADER, Settings
from grafana_mcp.server import create_app

LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

INIT = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "probe", "version": "1"}},
}


@pytest.fixture
def client():
    with TestClient(create_app(Settings())) as c:
        yield c


def _call_tool(client, name: str, headers: dict, **arguments):
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    r = client.post("/mcp", json=body, headers={**HEADERS, **headers})
    assert r.status_code == 200
    content = r.json()["result"]["content"]
    return json.loads(content[0]["text"])


def test_health_needs_no_credentials(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


# Code review round 1, finding 1: a request to /mcp missing a required header is rejected
# with HTTP 401 before it ever reaches a tool (SOP sec 3.2), mirroring itglue-mcp's
# CredentialMiddleware shape. This replaces the earlier TC7 interpretation (an in-band
# `not_configured` tool-call error for an absent header) -- see Fixes round 1 in
# docs/tickets/PRD-21261-review.md for why, and docs/tickets/PRD-21261.md TC7 for the
# original (now superseded) test-case wording.
def test_missing_both_headers_on_tools_list_is_401(client):
    r = client.post("/mcp", json=LIST, headers=HEADERS)
    assert r.status_code == 401
    body = r.json()
    assert set(body["required_headers"]) == {TOKEN_HEADER, BASE_URL_HEADER}


def test_missing_token_header_on_initialize_is_401(client):
    r = client.post("/mcp", json=INIT, headers={**HEADERS, BASE_URL_HEADER: BASE})
    assert r.status_code == 401
    assert r.json()["required_headers"] == [TOKEN_HEADER]


def test_missing_base_url_header_on_tools_list_is_401(client):
    r = client.post("/mcp", json=LIST, headers={**HEADERS, "X-Grafana-Token": "tok"})
    assert r.status_code == 401
    assert r.json()["required_headers"] == [BASE_URL_HEADER]


@pytest.mark.parametrize("token", ["", "   "])
def test_empty_token_header_is_still_401(client, token):
    h = {**HEADERS, "X-Grafana-Token": token, "X-Grafana-Base-Url": BASE}
    assert client.post("/mcp", json=LIST, headers=h).status_code == 401


@respx.mock
def test_missing_token_header_on_tool_call_is_401_without_outbound_call(client):
    route = respx.get(f"{BASE}/api/search").mock(return_value=httpx.Response(200, json=[]))
    body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "grafana_search_dashboards", "arguments": {}},
    }
    r = client.post("/mcp", json=body, headers={**HEADERS, "X-Grafana-Base-Url": BASE})
    assert r.status_code == 401
    assert TOKEN_HEADER in r.json()["required_headers"]
    assert route.call_count == 0


# Admin-console tool-catalog refresh: the console always sends *some* value for a required
# header, even before a tenant has entered real credentials (it never omits the header
# entirely) -- verified against the real precedent this connector's design doc originally
# (and incorrectly) cited, itglue-mcp's test_malformed_region_or_base_url_does_not_... .
# A present-but-not-a-real-token value, and a present-but-malformed base URL, must not be
# rejected at this layer; only an absent header is.
def test_placeholder_headers_do_not_block_initialize_or_tools_list(client):
    h = {**HEADERS, "X-Grafana-Token": "placeholder", "X-Grafana-Base-Url": "placeholder"}
    assert client.post("/mcp", json=INIT, headers=h).status_code == 200
    r = client.post("/mcp", json=LIST, headers=h)
    assert r.status_code == 200
    assert len(r.json()["result"]["tools"]) == 12


def test_tools_list_is_stateless(client):
    # No initialize / session id: must still answer 200 (both required headers present).
    h = {**HEADERS, "X-Grafana-Token": "tok", "X-Grafana-Base-Url": BASE}
    r = client.post("/mcp", json=LIST, headers=h)
    assert r.status_code == 200
    assert len(r.json()["result"]["tools"]) == 12


def test_malformed_base_url_does_not_block_initialize_or_tools_list(client):
    h = {**HEADERS, "X-Grafana-Token": "tok", "X-Grafana-Base-Url": "not-a-url"}
    assert client.post("/mcp", json=INIT, headers=h).status_code == 200
    r = client.post("/mcp", json=LIST, headers=h)
    assert r.status_code == 200
    assert len(r.json()["result"]["tools"]) == 12


@respx.mock
def test_malformed_base_url_is_invalid_argument_on_tool_call(client):
    route = respx.get(f"{BASE}/api/search").mock(return_value=httpx.Response(200, json=[]))
    out = _call_tool(client, "grafana_search_dashboards", {"X-Grafana-Token": "tok", "X-Grafana-Base-Url": "not-a-url"})
    assert out["error"]["code"] == "invalid_argument"
    assert route.call_count == 0


@respx.mock
def test_health_check_tool_works_without_token(client):
    respx.get(f"{BASE}/api/health").mock(
        return_value=httpx.Response(200, json={"version": "10.4.0", "database": "ok", "commit": "deadbeef"})
    )
    out = _call_tool(client, "grafana_get_health", {"X-Grafana-Base-Url": BASE})
    assert out == {"version": "10.4.0", "database": "ok"}


def test_health_check_tool_still_needs_base_url(client):
    # The token exemption (above) is narrow: grafana_get_health still needs to know which
    # instance to probe, so a request missing X-Grafana-Base-Url is 401 regardless.
    body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "grafana_get_health", "arguments": {}},
    }
    r = client.post("/mcp", json=body, headers=HEADERS)
    assert r.status_code == 401
    assert r.json()["required_headers"] == [BASE_URL_HEADER]


# TC10: two concurrent requests, each with its own credentials, must never see the other's
# (contextvars.ContextVar is copied per asyncio Task, so this must hold even when both tasks
# are genuinely interleaved by an await in between).
async def test_concurrent_requests_do_not_leak_credentials_across_contextvar():
    captured: dict[str, str] = {}

    async def one(tenant: str, token: str):
        bound = credentials._current.set(credentials.Credentials(token=token, base_url=f"https://{tenant}.example.com"))
        try:
            await asyncio.sleep(0.01)  # yield control so the two tasks genuinely interleave
            mid = credentials.current()
            captured[f"{tenant}-mid"] = f"{mid.base_url}|{mid.token}"
            await asyncio.sleep(0.01)
            after = credentials.current()
            captured[f"{tenant}-after"] = f"{after.base_url}|{after.token}"
        finally:
            credentials._current.reset(bound)

    await asyncio.gather(one("tenant-a", "token-a"), one("tenant-b", "token-b"))

    assert captured["tenant-a-mid"] == "https://tenant-a.example.com|token-a"
    assert captured["tenant-a-after"] == "https://tenant-a.example.com|token-a"
    assert captured["tenant-b-mid"] == "https://tenant-b.example.com|token-b"
    assert captured["tenant-b-after"] == "https://tenant-b.example.com|token-b"
    # Outside both tasks, the ContextVar is back to unset.
    assert credentials.current() is None


@respx.mock
async def test_concurrent_tool_calls_use_only_their_own_header_pair(mcp):
    """Same as above but through the real tool/HTTP path: each concurrent outbound call must
    carry only its own Authorization header and hit only its own base_url."""
    seen: dict[str, str] = {}

    def responder(request: httpx.Request):
        seen[str(request.url)] = request.headers.get("authorization", "")
        return httpx.Response(200, json=[])

    respx.get(url__regex=r"https://tenant-[ab]\.example\.com/api/search").mock(side_effect=responder)

    async def call_for(tenant: str, token: str):
        bound = credentials._current.set(credentials.Credentials(token=token, base_url=f"https://{tenant}.example.com"))
        try:
            await mcp.call_tool("grafana_search_dashboards", {})
        finally:
            credentials._current.reset(bound)

    await asyncio.gather(call_for("tenant-a", "token-a"), call_for("tenant-b", "token-b"))

    def auth_for(tenant: str) -> str:
        (auth,) = (v for k, v in seen.items() if k.startswith(f"https://{tenant}.example.com/api/search"))
        return auth

    assert auth_for("tenant-a") == "Bearer token-a"
    assert auth_for("tenant-b") == "Bearer token-b"
