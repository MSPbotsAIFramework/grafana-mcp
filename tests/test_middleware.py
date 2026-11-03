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


def test_tools_list_works_without_any_headers(client):
    # The admin console refreshes a connector's tool catalog this way, before a tenant has
    # entered credentials -- initialize / tools/list must not be blocked (see PRD-21261 TC7:
    # a missing credential is reported by the *tool call*, not by rejecting the request).
    assert client.post("/mcp", json=INIT, headers=HEADERS).status_code == 200
    r = client.post("/mcp", json=LIST, headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()["result"]["tools"]) == 12


def test_tools_list_is_stateless(client):
    # No initialize / session id: must still answer 200.
    r = client.post("/mcp", json=LIST, headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()["result"]["tools"]) == 12


# TC7: missing X-Grafana-Token -> not_configured, zero outbound calls (not a 500, not a hang,
# and not an HTTP-level 401 -- tools/list above must keep working regardless).
@respx.mock
def test_missing_token_header_returns_not_configured_without_outbound_call(client):
    route = respx.get(f"{BASE}/api/search").mock(return_value=httpx.Response(200, json=[]))
    out = _call_tool(client, "grafana_search_dashboards", {"X-Grafana-Base-Url": BASE})
    assert out["error"]["code"] == "not_configured"
    assert TOKEN_HEADER in out["error"]["message"]
    assert route.call_count == 0


@respx.mock
def test_missing_base_url_header_returns_not_configured_without_outbound_call(client):
    route = respx.get(f"{BASE}/api/search").mock(return_value=httpx.Response(200, json=[]))
    out = _call_tool(client, "grafana_search_dashboards", {"X-Grafana-Token": "tok"})
    assert out["error"]["code"] == "not_configured"
    assert BASE_URL_HEADER in out["error"]["message"]
    assert route.call_count == 0


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
