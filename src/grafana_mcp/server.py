# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""FastMCP server and HTTP app."""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from ._arguments import EnvelopeFastMCP
from .config import Settings
from .credentials import CredentialMiddleware
from .tools import alerting, annotations, dashboards, datasources, health

INSTRUCTIONS = (
    "Grafana is the MSP's observability dashboard and alerting platform. Dashboards live in "
    "folders and are built from panels backed by datasources (Prometheus, Loki, etc). Typical "
    "flow: grafana_search_dashboards to find a dashboard, grafana_get_dashboard to see its panel "
    "layout, grafana_query_datasource to pull the live data behind a panel's datasource (use "
    "grafana_list_datasources / grafana_get_datasource to find the datasource_uid first). "
    "Alerting: grafana_list_alert_rules / grafana_get_alert_rule inspect rule config, "
    "grafana_list_contact_points shows where alerts notify. grafana_search_annotations / "
    "grafana_create_annotation read and record time-stamped notes on a dashboard (e.g. to mark "
    "that an agent took an action) -- annotation creation is the only write tool here; everything "
    "else is read-only. grafana_get_health checks instance reachability and version and works even "
    "without a token."
)


def create_mcp(settings: Settings) -> FastMCP:
    mcp = EnvelopeFastMCP(
        name="grafana-mcp",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        # The gateway forwards requests with its own Host header; DNS-rebinding
        # protection would reject them with 421.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    for module in (dashboards, datasources, alerting, annotations, health):
        module.register(mcp, settings)
    return mcp


def create_app(settings: Settings) -> Starlette:
    mcp = create_mcp(settings)
    mcp_app = mcp.streamable_http_app()

    async def health_check(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Mount() does not run a sub-app's lifespan; without forwarding it the
    # session manager never starts and /mcp fails while /health still passes.
    return Starlette(
        routes=[Route("/health", health_check), Mount("/", app=CredentialMiddleware(mcp_app))],
        lifespan=lambda app: mcp_app.router.lifespan_context(app),
    )
