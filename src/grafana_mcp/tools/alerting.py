# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Alert rule and contact point listing (read-only).

Grafana API reference: .../developers/http_api/alerting_provisioning/ (fetched 2026-11-02).
The provisioning endpoints used here have no server-side limit or folder filter, so both
are applied client-side after fetching the full list.
"""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .. import api
from ..config import Settings
from ._common import READ, Limit, clamp_limit, cut, run, seg


def _trim_rule(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "uid": item.get("uid"),
        "title": item.get("title"),
        "folderUID": item.get("folderUID"),
        "ruleGroup": item.get("ruleGroup"),
        "noDataState": item.get("noDataState"),
        "execErrState": item.get("execErrState"),
        "isPaused": item.get("isPaused", False),
    }


def register(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool(
        name="grafana_list_alert_rules",
        description=(
            "List Grafana alert rules.\n"
            "Returns title, folder, no-data/exec-error state and paused flag; optionally scoped to one folder."
        ),
        annotations=READ,
        structured_output=False,
    )
    async def grafana_list_alert_rules(
        folder_uid: Annotated[str | None, Field(description="Only rules in this folder uid.")] = None,
        limit: Limit = None,
    ) -> str:
        size = clamp_limit(limit)

        async def handler(client: api.GrafanaClient):
            raw = await client.get("/api/v1/provisioning/alert-rules") or []
            if folder_uid:
                raw = [r for r in raw if r.get("folderUID") == folder_uid]
            items, has_more = cut(raw, size)
            return {"items": [_trim_rule(r) for r in items], "has_more": has_more}

        return await run(settings, handler)

    @mcp.tool(
        name="grafana_get_alert_rule",
        description="Get one Grafana alert rule by uid.\nIncludes its evaluation condition.",
        annotations=READ,
        structured_output=False,
    )
    async def grafana_get_alert_rule(
        uid: Annotated[str, Field(description="Alert rule uid, from grafana_list_alert_rules.")],
    ) -> str:
        async def handler(client: api.GrafanaClient):
            raw = await client.get(f"/api/v1/provisioning/alert-rules/{seg(uid)}") or {}
            trimmed = _trim_rule(raw)
            trimmed["condition"] = raw.get("condition")
            return trimmed

        return await run(settings, handler)

    @mcp.tool(
        name="grafana_list_contact_points",
        description=(
            "List Grafana notification contact points.\n"
            "Returns uid, name and type; notification settings are never returned."
        ),
        annotations=READ,
        structured_output=False,
    )
    async def grafana_list_contact_points(limit: Limit = None) -> str:
        size = clamp_limit(limit)

        async def handler(client: api.GrafanaClient):
            raw = await client.get("/api/v1/provisioning/contact-points") or []
            items, has_more = cut(raw, size)
            # "settings" can carry secrets (webhook URLs, SMTP credentials) -- never forwarded.
            return {
                "items": [{"uid": c.get("uid"), "name": c.get("name"), "type": c.get("type")} for c in items],
                "has_more": has_more,
            }

        return await run(settings, handler)
