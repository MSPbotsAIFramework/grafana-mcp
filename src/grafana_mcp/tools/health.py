# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Instance health/version check. Grafana does not require auth for /api/health, so this
tool only needs the base URL -- it works even before a valid token is configured."""

from mcp.server.fastmcp import FastMCP

from .. import api
from ..config import Settings
from ._common import READ, run


def register(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool(
        name="grafana_get_health",
        description=(
            "Check that a Grafana instance is reachable and report its version.\n"
            "Works without a valid token; only the instance base URL is required."
        ),
        annotations=READ,
        structured_output=False,
    )
    async def grafana_get_health() -> str:
        async def handler(client: api.GrafanaClient):
            raw = await client.get("/api/health") or {}
            # "commit" is an internal build hash with no agent-facing value.
            return {"version": raw.get("version"), "database": raw.get("database")}

        return await run(settings, handler, require_token=False)
