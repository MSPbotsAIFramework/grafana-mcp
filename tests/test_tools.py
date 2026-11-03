# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Tool behaviour against a mocked Grafana API."""

import json

import httpx
import pytest
import respx
from conftest import BASE

pytestmark = pytest.mark.usefixtures("creds")

EXPECTED_TOOLS = {
    "grafana_search_dashboards",
    "grafana_get_dashboard",
    "grafana_list_folders",
    "grafana_list_datasources",
    "grafana_get_datasource",
    "grafana_query_datasource",
    "grafana_list_alert_rules",
    "grafana_get_alert_rule",
    "grafana_list_contact_points",
    "grafana_search_annotations",
    "grafana_create_annotation",
    "grafana_get_health",
}


async def _tools(mcp):
    return {t.name: t for t in await mcp.list_tools()}


async def test_tool_list_has_exactly_the_twelve_designed_tools(mcp):
    names = set(await _tools(mcp))
    assert names == EXPECTED_TOOLS
    assert len(names) == 12


async def test_descriptions_are_concise(mcp):
    for tool in (await _tools(mcp)).values():
        assert tool.description, tool.name
        assert len(tool.description) <= 500, tool.name
        assert len(tool.description.splitlines()[0]) <= 100, tool.name


async def test_tool_names_are_vendor_prefixed(mcp):
    for name in await _tools(mcp):
        assert name.startswith("grafana_"), name


async def test_annotations_hints(mcp):
    tools = await _tools(mcp)
    read_only = EXPECTED_TOOLS - {"grafana_create_annotation"}
    for name in read_only:
        assert tools[name].annotations.readOnlyHint is True, name
    create = tools["grafana_create_annotation"].annotations
    assert create.readOnlyHint is False
    assert create.destructiveHint is False
    assert create.idempotentHint is False


# TC2 (AC2): tag filter is applied via the upstream query, and the mocked response (already
# scoped to the matching 2 of 3 dashboards) comes back trimmed to the documented shape.
@respx.mock
async def test_search_dashboards_filters_by_tag(call):
    route = respx.get(f"{BASE}/api/search").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "uid": "d1", "title": "Infra A", "url": "/d/d1",
                    "tags": ["infra"], "folderUid": "f1", "folderTitle": "Infra",
                },
                {
                    "uid": "d2", "title": "Infra B", "url": "/d/d2",
                    "tags": ["infra"], "folderUid": "f1", "folderTitle": "Infra",
                },
            ],
        )
    )
    out = await call("grafana_search_dashboards", tag="infra")
    assert route.calls.last.request.url.params["tag"] == "infra"
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"
    assert out["has_more"] is False
    assert [d["uid"] for d in out["items"]] == ["d1", "d2"]
    assert out["items"][0] == {
        "uid": "d1",
        "title": "Infra A",
        "url": "/d/d1",
        "tags": ["infra"],
        "folderUid": "f1",
        "folderTitle": "Infra",
    }


@respx.mock
async def test_search_dashboards_has_more_when_upstream_returns_an_extra_row(call):
    # The tool asks for limit+1 to detect another page without a second round trip.
    items = [
        {"uid": f"d{i}", "title": f"D{i}", "url": "/", "tags": [], "folderUid": None, "folderTitle": None}
        for i in range(3)
    ]
    route = respx.get(f"{BASE}/api/search").mock(return_value=httpx.Response(200, json=items))
    out = await call("grafana_search_dashboards", limit=2)
    assert route.calls.last.request.url.params["limit"] == "3"
    assert out["has_more"] is True
    assert len(out["items"]) == 2


# TC3 (AC3): default shape is a compact summary -- title/uid/tags/folder + exactly N panel
# summaries (id/title/type/datasource), never the raw panel query bodies (targets).
@respx.mock
async def test_get_dashboard_default_is_compact_summary(call):
    panels = [
        {
            "id": i,
            "title": f"Panel {i}",
            "type": "timeseries",
            "datasource": {"uid": "prom"},
            "targets": [{"expr": "up", "secret": "x"}],
        }
        for i in range(5)
    ]
    respx.get(f"{BASE}/api/dashboards/uid/abc123").mock(
        return_value=httpx.Response(
            200,
            json={
                "dashboard": {"uid": "abc123", "title": "My Board", "tags": ["x"], "panels": panels},
                "meta": {"folderUid": "f1", "folderTitle": "Infra"},
            },
        )
    )
    out = await call("grafana_get_dashboard", uid="abc123")
    assert out["uid"] == "abc123"
    assert out["title"] == "My Board"
    assert out["tags"] == ["x"]
    assert out["folderUid"] == "f1"
    assert out["folderTitle"] == "Infra"
    assert len(out["panels"]) == 5
    assert out["panels"][0] == {"id": 0, "title": "Panel 0", "type": "timeseries", "datasource": {"uid": "prom"}}
    assert "targets" not in json.dumps(out["panels"])


@respx.mock
async def test_get_dashboard_include_raw_returns_full_payload(call):
    payload = {"dashboard": {"uid": "abc123", "title": "My Board", "panels": []}, "meta": {"folderUid": "f1"}}
    respx.get(f"{BASE}/api/dashboards/uid/abc123").mock(return_value=httpx.Response(200, json=payload))
    out = await call("grafana_get_dashboard", uid="abc123", include_raw=True)
    assert out == payload


# TC11 (AC11): a dashboard whose raw JSON exceeds the per-call budget comes back truncated,
# not cut off mid-JSON. Many distinct top-level keys (not one long list) are used so the
# generic size-cap shrinker cannot bring it under budget by shortening values alone -- it
# must fall through to the explicit truncated marker, which is what this test is pinning.
@respx.mock
async def test_get_dashboard_raw_oversized_is_truncated_not_corrupted(call):
    huge_meta = {f"extra_field_{i}": "x" * 50 for i in range(1200)}
    payload = {"dashboard": {"uid": "big", "title": "Huge", "panels": []}, "meta": huge_meta}
    respx.get(f"{BASE}/api/dashboards/uid/big").mock(return_value=httpx.Response(200, json=payload))

    from grafana_mcp import _json as json_mod

    out = await call("grafana_get_dashboard", uid="big", include_raw=True)
    assert out.get("truncated") is True
    assert isinstance(out, dict)  # valid, parseable JSON -- not a cut-off string
    assert len(json_mod.compact(out)) <= json_mod.MAX_CHARS


# TC5 (AC5): alert-rule folder filtering is client-side (the provisioning API has no
# server-side folder filter).
@respx.mock
async def test_list_alert_rules_filters_by_folder_client_side(call):
    common = {"ruleGroup": "g1", "noDataState": "NoData", "execErrState": "Error", "isPaused": False}
    rules = [
        {"uid": "r1", "title": "R1", "folderUID": "fA", **common},
        {"uid": "r2", "title": "R2", "folderUID": "fB", **common},
    ]
    route = respx.get(f"{BASE}/api/v1/provisioning/alert-rules").mock(return_value=httpx.Response(200, json=rules))
    out = await call("grafana_list_alert_rules", folder_uid="fA")
    # No folder query param is sent upstream -- the filter happens after the fetch.
    assert "folderUID" not in route.calls.last.request.url.params
    assert [r["uid"] for r in out["items"]] == ["r1"]


@respx.mock
async def test_list_contact_points_never_returns_settings(call):
    respx.get(f"{BASE}/api/v1/provisioning/contact-points").mock(
        return_value=httpx.Response(
            200, json=[{"uid": "c1", "name": "Ops", "type": "webhook", "settings": {"url": "https://secret.example.com/hook"}}]
        )
    )
    out = await call("grafana_list_contact_points")
    assert out["items"] == [{"uid": "c1", "name": "Ops", "type": "webhook"}]
    assert "settings" not in json.dumps(out)


@respx.mock
async def test_get_datasource_never_returns_secret_fields(call):
    respx.get(f"{BASE}/api/datasources/uid/ds1").mock(
        return_value=httpx.Response(
            200,
            json={
                "uid": "ds1",
                "name": "Prometheus",
                "type": "prometheus",
                "url": "http://prom:9090",
                "isDefault": True,
                "basicAuthPassword": "s3cr3t",
                "secureJsonFields": {"httpHeaderValue1": True},
            },
        )
    )
    out = await call("grafana_get_datasource", uid="ds1")
    assert out == {
        "uid": "ds1", "name": "Prometheus", "type": "prometheus", "url": "http://prom:9090", "isDefault": True,
    }


@respx.mock
async def test_query_datasource_builds_single_query_with_fixed_refid(call):
    route = respx.post(f"{BASE}/api/ds/query").mock(
        return_value=httpx.Response(200, json={"results": {"A": {"frames": [{"schema": {}, "data": {}}]}}})
    )
    out = await call(
        "grafana_query_datasource",
        datasource_uid="ds1",
        query={"expr": "up", "refId": "should-be-overridden"},
        time_from="now-5m",
        time_to="now",
    )
    body = json.loads(route.calls.last.request.content)
    assert body["from"] == "now-5m" and body["to"] == "now"
    assert body["queries"] == [{"expr": "up", "refId": "A", "datasource": {"uid": "ds1"}}]
    assert out["results"]["A"]["frames"]


@respx.mock
async def test_create_annotation_posts_only_set_fields(call):
    route = respx.post(f"{BASE}/api/annotations").mock(
        return_value=httpx.Response(200, json={"id": 42, "message": "Annotation added"})
    )
    out = await call("grafana_create_annotation", text="agent did a thing", tags=["agent"])
    body = json.loads(route.calls.last.request.content)
    assert body == {"text": "agent did a thing", "tags": ["agent"]}
    assert out["id"] == 42


# Error mapping table (SOP §4.2).
@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (400, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
    ],
)
@respx.mock
async def test_status_code_maps_to_envelope(call, status, code, retryable):
    respx.get(f"{BASE}/api/datasources").mock(return_value=httpx.Response(status, json={"message": "boom"}))
    out = await call("grafana_list_datasources")
    assert out["error"]["code"] == code
    assert out["error"]["retryable"] is retryable


# TC9 (AC9): a connection error maps to upstream_error, retryable, no hang.
@respx.mock
async def test_connection_error_maps_to_upstream_error(call):
    respx.get(f"{BASE}/api/datasources").mock(side_effect=httpx.ConnectError("boom"))
    out = await call("grafana_list_datasources")
    assert out == {
        "error": {"code": "upstream_error", "message": "Could not reach Grafana: ConnectError.", "retryable": True},
    }


@respx.mock
async def test_timeout_maps_to_upstream_error_after_bounded_retries(call):
    route = respx.get(f"{BASE}/api/datasources").mock(side_effect=httpx.ReadTimeout("slow"))
    out = await call("grafana_list_datasources")
    assert out["error"]["code"] == "upstream_error"
    assert out["error"]["retryable"] is True
    assert route.call_count >= 1


@respx.mock
async def test_rate_limit_is_retried_then_succeeds(call):
    route = respx.get(f"{BASE}/api/datasources")
    route.side_effect = [httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json=[])]
    out = await call("grafana_list_datasources")
    assert out == {"items": []}
    assert route.call_count == 2


