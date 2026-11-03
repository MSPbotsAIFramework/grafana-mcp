# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Dashboard search/browse and folder listing.

Grafana API reference: .../developers/http_api/folder_dashboard_search/, .../dashboard/
(fetched 2026-11-02).
"""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .. import api
from ..config import Settings
from ._common import READ, Limit, clamp_limit, cut, run, seg


def _trim_search_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "uid": item.get("uid"),
        "title": item.get("title"),
        "url": item.get("url"),
        "tags": item.get("tags", []),
        "folderUid": item.get("folderUid"),
        "folderTitle": item.get("folderTitle"),
    }


def _panel_summary(panel: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": panel.get("id"),
        "title": panel.get("title"),
        "type": panel.get("type"),
        "datasource": panel.get("datasource"),
    }


def register(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool(
        name="grafana_search_dashboards",
        description=(
            "Find Grafana dashboards by title, tag or folder.\n"
            "Returns matching dashboards' title, uid, url, tags and folder -- use the uid with grafana_get_dashboard."
        ),
        annotations=READ,
        structured_output=False,
    )
    async def grafana_search_dashboards(
        query: Annotated[str | None, Field(description="Dashboard title text to match.")] = None,
        tag: Annotated[str | None, Field(description="Dashboard tag to filter by.")] = None,
        folder_uid: Annotated[str | None, Field(description="Only dashboards in this folder uid.")] = None,
        limit: Limit = None,
    ) -> str:
        size = clamp_limit(limit)

        async def handler(client: api.GrafanaClient):
            params = {
                "type": "dash-db",
                "query": query,
                "tag": tag,
                "folderUIDs": folder_uid,
                "limit": size + 1,
            }
            raw = await client.get("/api/search", params) or []
            items, has_more = cut(raw, size)
            return {"items": [_trim_search_item(item) for item in items], "has_more": has_more}

        return await run(settings, handler)

    @mcp.tool(
        name="grafana_get_dashboard",
        description=(
            "Get one dashboard's panel layout as a compact summary.\n"
            "Returns title, tags, folder and panel list (id/title/type/datasource); pass "
            "include_raw for the full dashboard JSON."
        ),
        annotations=READ,
        structured_output=False,
    )
    async def grafana_get_dashboard(
        uid: Annotated[str, Field(description="Dashboard uid, from grafana_search_dashboards.")],
        include_raw: Annotated[
            bool, Field(description="Return the full raw dashboard JSON instead of a summary.")
        ] = False,
    ) -> str:
        async def handler(client: api.GrafanaClient):
            payload = await client.get(f"/api/dashboards/uid/{seg(uid)}") or {}
            if include_raw:
                return payload
            dashboard = payload.get("dashboard") or {}
            meta = payload.get("meta") or {}
            return {
                "uid": dashboard.get("uid"),
                "title": dashboard.get("title"),
                "tags": dashboard.get("tags", []),
                "folderUid": meta.get("folderUid"),
                "folderTitle": meta.get("folderTitle"),
                "panels": [_panel_summary(p) for p in dashboard.get("panels", [])],
            }

        return await run(settings, handler)

    @mcp.tool(
        name="grafana_list_folders",
        description="List Grafana dashboard folders.\nReturns uid and title; use a folder's uid to scope other tools.",
        annotations=READ,
        structured_output=False,
    )
    async def grafana_list_folders(limit: Limit = None) -> str:
        size = clamp_limit(limit)

        async def handler(client: api.GrafanaClient):
            raw = await client.get("/api/search", {"type": "dash-folder", "limit": size + 1}) or []
            items, has_more = cut(raw, size)
            return {"items": [{"uid": i.get("uid"), "title": i.get("title")} for i in items], "has_more": has_more}

        return await run(settings, handler)
