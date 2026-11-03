# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Annotation search and creation -- the only write tool in this connector.

Grafana API reference: .../developers/http_api/annotations/ (fetched 2026-11-02).
"""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .. import api
from ..config import Settings
from ._common import CREATE, READ, Limit, clamp_limit, cut, run


def _trim_annotation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "dashboardUID": item.get("dashboardUID"),
        "panelId": item.get("panelId"),
        "time": item.get("time"),
        "text": item.get("text"),
        "tags": item.get("tags", []),
    }


def register(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool(
        name="grafana_search_annotations",
        description="Find Grafana annotations by time range, tags or dashboard.\nReturns id, time, text and tags.",
        annotations=READ,
        structured_output=False,
    )
    async def grafana_search_annotations(
        time_from: Annotated[
            str | None, Field(description="Start of the time range, e.g. 'now-24h' or an epoch ms string.")
        ] = None,
        time_to: Annotated[
            str | None, Field(description="End of the time range, e.g. 'now' or an epoch ms string.")
        ] = None,
        tags: Annotated[list[str] | None, Field(description="Only annotations with all of these tags.")] = None,
        dashboard_uid: Annotated[str | None, Field(description="Only annotations on this dashboard.")] = None,
        limit: Limit = None,
    ) -> str:
        size = clamp_limit(limit)

        async def handler(client: api.GrafanaClient):
            params: dict[str, Any] = {
                "from": time_from,
                "to": time_to,
                "tags": tags,
                "dashboardUID": dashboard_uid,
                "limit": size + 1,
            }
            raw = await client.get("/api/annotations", params) or []
            items, has_more = cut(raw, size)
            return {"items": [_trim_annotation(a) for a in items], "has_more": has_more}

        return await run(settings, handler)

    @mcp.tool(
        name="grafana_create_annotation",
        description=(
            "Create a Grafana annotation, e.g. to record that an agent took an action.\n"
            "Additive only -- Grafana does not dedupe repeated calls, so do not retry on an "
            "ambiguous outcome (e.g. a timeout after the request may already have been sent)."
        ),
        annotations=CREATE,
        structured_output=False,
    )
    async def grafana_create_annotation(
        text: Annotated[str, Field(description="Annotation text.")],
        tags: Annotated[list[str] | None, Field(description="Tags to attach to the annotation.")] = None,
        dashboard_uid: Annotated[str | None, Field(description="Dashboard to attach the annotation to.")] = None,
        panel_id: Annotated[
            int | None, Field(description="Panel to attach the annotation to (requires dashboard_uid).")
        ] = None,
        time_ms: Annotated[int | None, Field(description="Event time, epoch ms. Defaults to now if omitted.")] = None,
        time_end_ms: Annotated[int | None, Field(description="End time for a region annotation, epoch ms.")] = None,
    ) -> str:
        async def handler(client: api.GrafanaClient):
            body = {
                "text": text,
                "tags": tags,
                "dashboardUID": dashboard_uid,
                "panelId": panel_id,
                "time": time_ms,
                "timeEnd": time_end_ms,
            }
            return await client.post("/api/annotations", {k: v for k, v in body.items() if v is not None}) or {}

        return await run(settings, handler)
