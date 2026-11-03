# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Datasource listing and the query proxy.

Grafana API reference: .../developers/http_api/data_source/, .../developers/http_api/other/
(``/api/ds/query``) (fetched 2026-11-02).
"""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .. import api
from ..config import Settings
from ._common import READ, run, seg


def _trim_datasource(item: dict[str, Any]) -> dict[str, Any]:
    # Allow-listed fields only: Grafana's full datasource object can carry secret
    # fields (jsonData, secureJsonFields, basicAuthPassword, ...) that must never
    # reach the agent. Picking the fields below rather than blocking a deny-list
    # means a new secret field Grafana adds later is dropped by default, not leaked.
    return {
        "uid": item.get("uid"),
        "name": item.get("name"),
        "type": item.get("type"),
        "url": item.get("url"),
        "isDefault": item.get("isDefault", False),
    }


def register(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool(
        name="grafana_list_datasources",
        description=(
            "List Grafana datasources.\nReturns uid, name, type and url; use a uid with grafana_query_datasource."
        ),
        annotations=READ,
        structured_output=False,
    )
    async def grafana_list_datasources() -> str:
        async def handler(client: api.GrafanaClient):
            raw = await client.get("/api/datasources") or []
            return {"items": [_trim_datasource(d) for d in raw]}

        return await run(settings, handler)

    @mcp.tool(
        name="grafana_get_datasource",
        description="Get one Grafana datasource by uid.\nReturns name, type and url -- never returns credentials.",
        annotations=READ,
        structured_output=False,
    )
    async def grafana_get_datasource(
        uid: Annotated[str, Field(description="Datasource uid, from grafana_list_datasources.")],
    ) -> str:
        async def handler(client: api.GrafanaClient):
            raw = await client.get(f"/api/datasources/uid/{seg(uid)}") or {}
            return _trim_datasource(raw)

        return await run(settings, handler)

    @mcp.tool(
        name="grafana_query_datasource",
        description=(
            "Run a query against a Grafana datasource over a time range.\n"
            "'query' holds the datasource-specific fields (e.g. {\"expr\": \"up\"} for Prometheus); "
            "returns the result frames."
        ),
        annotations=READ,
        structured_output=False,
    )
    async def grafana_query_datasource(
        datasource_uid: Annotated[str, Field(description="Datasource uid, from grafana_list_datasources.")],
        query: Annotated[dict[str, Any], Field(description="Datasource-specific query fields, e.g. {'expr': 'up'}.")],
        time_from: Annotated[
            str, Field(description="Start of the time range, e.g. 'now-5m' or an epoch ms string.")
        ] = "now-5m",
        time_to: Annotated[
            str, Field(description="End of the time range, e.g. 'now' or an epoch ms string.")
        ] = "now",
        max_data_points: Annotated[
            int | None, Field(description="Cap on returned data points per series.", ge=1)
        ] = None,
    ) -> str:
        async def handler(client: api.GrafanaClient):
            query_obj: dict[str, Any] = {**query, "refId": "A", "datasource": {"uid": seg(datasource_uid)}}
            if max_data_points is not None:
                query_obj["maxDataPoints"] = max_data_points
            body = {"queries": [query_obj], "from": time_from, "to": time_to}
            return await client.post("/api/ds/query", body) or {}

        return await run(settings, handler)
